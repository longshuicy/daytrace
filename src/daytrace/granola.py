"""Granola's hosted MCP server: sign-in (dynamic client registration, then device code flow) and tool calls.
The free plan reaches the last 30 days of notes, so syncing every few minutes keeps a full copy."""

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

MCP = "https://mcp.granola.ai/mcp"
AUTH = "https://mcp-auth.granola.ai/oauth2"
SCOPES = "openid offline_access"
# Registration needs a redirect URI even though the device flow never uses it.
REDIRECT = "http://127.0.0.1:8765/callback"
PROTOCOL = "2025-06-18"


def token_path() -> Path:
    return config.home() / "granola-token.json"


def _post(url: str, form: dict) -> dict:
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read() or b"{}")


def _register() -> str:
    body = json.dumps({
        "client_name": "daytrace", "token_endpoint_auth_method": "none", "response_types": ["code"],
        "grant_types": ["urn:ietf:params:oauth:grant-type:device_code", "refresh_token", "authorization_code"],
        "redirect_uris": [REDIRECT],
    }).encode()
    req = urllib.request.Request(f"{AUTH}/register", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())["client_id"]


def _save(token: dict, client_id: str) -> None:
    token["expires_at"] = time.time() + int(token.get("expires_in", 3600)) - 120
    token["client_id"] = client_id
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({k: token[k] for k in ("access_token", "refresh_token", "expires_at", "client_id") if k in token}, f)


def connect(say: Callable[[str], None] = print) -> None:
    """Interactive sign-in: show a code, wait for the user to approve in the browser."""
    client_id = _register()
    flow = _post(f"{AUTH}/device_authorization", {"client_id": client_id, "scope": SCOPES, "resource": MCP})
    if "device_code" not in flow:
        raise RuntimeError(flow.get("error_description") or flow.get("error") or "device code request failed")
    say(f"Open {flow['verification_uri_complete']} and confirm the code {flow['user_code']}.")
    interval = int(flow.get("interval", 5))
    deadline = time.time() + int(flow.get("expires_in", 300))
    while time.time() < deadline:
        time.sleep(interval)
        token = _post(f"{AUTH}/token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client_id, "device_code": flow["device_code"], "resource": MCP,
        })
        error = token.get("error")
        if not error:
            _save(token, client_id)
            return
        if error == "slow_down":
            interval += 5
        elif error != "authorization_pending":
            raise RuntimeError(token.get("error_description") or error)
    raise RuntimeError("sign-in timed out; run `daytrace connect granola` again")


def disconnect() -> bool:
    path = token_path()
    if path.exists():
        path.unlink()
        return True
    return False


def access_token() -> str:
    path = token_path()
    if not path.exists():
        raise NotConfigured("run `daytrace connect granola`")
    token = json.loads(path.read_text())
    if token.get("expires_at", 0) > time.time():
        return token["access_token"]
    refreshed = _post(f"{AUTH}/token", {
        "grant_type": "refresh_token", "client_id": token["client_id"],
        "refresh_token": token["refresh_token"], "resource": MCP,
    })
    if "access_token" not in refreshed:
        raise RuntimeError(
            f"Granola sign-in expired ({refreshed.get('error', 'unknown')}); run `daytrace connect granola`")
    refreshed.setdefault("refresh_token", token["refresh_token"])
    _save(refreshed, token["client_id"])
    return refreshed["access_token"]


class Session:
    """One MCP session over streamable HTTP: initialize once, then call tools."""

    def __init__(self):
        self.token = access_token()
        self.session_id: str | None = None
        self.next_id = 1
        self._rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                 "clientInfo": {"name": "daytrace", "version": "0"}})
        self._rpc("notifications/initialized", None, notify=True)

    def _rpc(self, method: str, params: dict | None, notify: bool = False) -> dict | None:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            msg["id"], self.next_id = self.next_id, self.next_id + 1
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": PROTOCOL}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(MCP, data=json.dumps(msg).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            self.session_id = resp.headers.get("Mcp-Session-Id") or self.session_id
            body = resp.read().decode()
            if notify:
                return None
            if resp.headers.get_content_type() == "text/event-stream":
                body = _last_sse_data(body)
        reply = json.loads(body)
        if "error" in reply:
            raise RuntimeError(f"Granola {method}: {reply['error'].get('message')}")
        return reply["result"]

    def tools(self) -> list[dict]:
        return self._rpc("tools/list", {})["tools"]

    def call(self, name: str, arguments: dict) -> str:
        """A tool's text output."""
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise RuntimeError(f"Granola {name}: {text}")
        return text


def _last_sse_data(stream: str) -> str:
    events = [e for e in stream.split("\n\n") if "data:" in e]
    return "\n".join(line[5:].strip() for line in events[-1].splitlines() if line.startswith("data:"))
