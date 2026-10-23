"""Microsoft Graph sign-in (device code flow, public client, no secret) and read-only GET requests."""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from . import config
from .adapters import NotConfigured

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access User.Read Calendars.Read Mail.ReadBasic"


def token_path() -> Path:
    return config.home() / "msgraph-token.json"


def _ids() -> tuple[str, str]:
    client, tenant = os.environ.get("MS_CLIENT_ID"), os.environ.get("MS_TENANT_ID")
    if not client or not tenant:
        raise NotConfigured("set MS_CLIENT_ID and MS_TENANT_ID in ~/.daytrace/.env")
    return client, tenant


def _post(url: str, form: dict) -> dict:
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # OAuth errors come back as 400 with a JSON body we need to read.
        return json.loads(exc.read() or b"{}")


def _save(token: dict) -> None:
    token["expires_at"] = time.time() + int(token.get("expires_in", 3600)) - 120
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({k: token[k] for k in ("access_token", "refresh_token", "expires_at") if k in token}, f)


def connect(say: Callable[[str], None] = print) -> None:
    """Interactive sign-in: show a code, wait for the user to approve in the browser."""
    client, tenant = _ids()
    base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
    flow = _post(f"{base}/devicecode", {"client_id": client, "scope": SCOPES})
    if "device_code" not in flow:
        raise RuntimeError(flow.get("error_description") or flow.get("error") or "device code request failed")
    say(flow["message"])
    interval = int(flow.get("interval", 5))
    deadline = time.time() + int(flow.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        token = _post(f"{base}/token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client, "device_code": flow["device_code"],
        })
        error = token.get("error")
        if not error:
            _save(token)
            return
        if error == "slow_down":
            interval += 5
        elif error != "authorization_pending":
            raise RuntimeError(token.get("error_description") or error)
    raise RuntimeError("sign-in timed out; run `daytrace connect outlook` again")


def disconnect() -> bool:
    path = token_path()
    if path.exists():
        path.unlink()
        return True
    return False


def access_token() -> str:
    client, tenant = _ids()
    path = token_path()
    if not path.exists():
        raise NotConfigured("run `daytrace connect outlook`")
    token = json.loads(path.read_text())
    if token.get("expires_at", 0) > time.time():
        return token["access_token"]
    refreshed = _post(f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token", {
        "grant_type": "refresh_token", "client_id": client,
        "refresh_token": token["refresh_token"], "scope": SCOPES,
    })
    if "access_token" not in refreshed:
        raise RuntimeError(
            f"Microsoft sign-in expired ({refreshed.get('error', 'unknown')}); run `daytrace connect outlook`")
    refreshed.setdefault("refresh_token", token["refresh_token"])
    _save(refreshed)
    return refreshed["access_token"]


def get_all(path: str, params: dict | None = None, headers: dict | None = None, max_pages: int = 20) -> list[dict]:
    """GET a Graph collection, following @odata.nextLink."""
    url = GRAPH + path + ("?" + urllib.parse.urlencode(params) if params else "")
    items: list[dict] = []
    for _ in range(max_pages):
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {access_token()}", "Accept": "application/json", **(headers or {}),
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            page = json.loads(resp.read())
        items.extend(page.get("value", []))
        url = page.get("@odata.nextLink")
        if not url:
            break
    return items
