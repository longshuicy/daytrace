"""Slack's hosted MCP server: browser sign-in and tool calls. Search only; never posts."""

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable

from . import config
from .adapters import NotConfigured

MCP = "https://mcp.slack.com/mcp"
AUTHORIZE = "https://slack.com/oauth/v2_user/authorize"
TOKEN_URL = "https://slack.com/api/oauth.v2.user.access"
# Slack's published MCP client (the same one Cursor documents). No Slack app to register.
CLIENT_ID = "3660753192626.8903469228982"
REDIRECT = "http://localhost:8787/callback"
SCOPES = "search:read.public search:read.private search:read.mpim search:read.im"
PROTOCOL = "2025-06-18"
ATTEMPTS = 4  # a 429 is retried this many times, counting the first try
WAIT_CAP = 30  # seconds; a longer Retry-After is cut so one sync cannot stall for minutes


def token_path() -> Path:
    return config.home() / "slack-token.json"


def _post(url: str, form: dict) -> dict:
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read() or b"{}")


def _save(token: dict) -> None:
    expires = int(token.get("expires_in") or 0)
    token["expires_at"] = time.time() + expires - 120 if expires else 0
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    keys = ("access_token", "refresh_token", "expires_at", "client_id")
    with os.fdopen(fd, "w") as f:
        json.dump({k: token[k] for k in keys if token.get(k)}, f)


def disconnect() -> bool:
    path = token_path()
    if path.exists():
        path.unlink()
        return True
    return False


def connect(say: Callable[[str], None] = print) -> None:
    """Open a browser, wait for Slack to redirect to localhost, store the user token."""
    client_id = os.environ.get("SLACK_CLIENT_ID") or CLIENT_ID
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    query = urllib.parse.urlencode({
        "client_id": client_id, "scope": SCOPES, "redirect_uri": REDIRECT, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": MCP,
    })
    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path != "/callback":
                self.send_error(404)
                return
            got.update({k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<p>Signed in. You can close this tab.</p>")

        def log_message(self, fmt, *args):
            pass

    try:
        server = http.server.HTTPServer(("127.0.0.1", 8787), Handler)
    except OSError as exc:
        raise RuntimeError("port 8787 is in use; close whatever is on it and run `daytrace connect slack` again") from exc
    threading.Thread(target=server.handle_request, daemon=True).start()
    say(f"Open {AUTHORIZE}?{query} and approve read-only search.")
    webbrowser.open(f"{AUTHORIZE}?{query}")
    deadline = time.time() + 300
    while time.time() < deadline and "code" not in got and "error" not in got:
        time.sleep(0.2)
    server.server_close()
    if got.get("error"):
        raise RuntimeError(got["error"])
    if got.get("state") != state or "code" not in got:
        raise RuntimeError("sign-in timed out; run `daytrace connect slack` again")
    token = _post(TOKEN_URL, {
        "grant_type": "authorization_code", "client_id": client_id, "code": got["code"],
        "redirect_uri": REDIRECT, "code_verifier": verifier, "resource": MCP,
    })
    access = token.get("access_token") or (token.get("authed_user") or {}).get("access_token")
    if not token.get("ok") or not access:
        raise RuntimeError(token.get("error") or "Slack sign-in failed")
    user = token.get("authed_user") or {}
    _save({
        "access_token": access,
        "refresh_token": token.get("refresh_token") or user.get("refresh_token"),
        "expires_in": token.get("expires_in") or user.get("expires_in") or 0,
        "client_id": client_id,
    })


def access_token() -> str:
    env = os.environ.get("SLACK_TOKEN")
    if env:
        return env
    path = token_path()
    if not path.exists():
        raise NotConfigured("run `daytrace connect slack`")
    token = json.loads(path.read_text())
    if not token.get("expires_at") or token["expires_at"] > time.time():
        return token["access_token"]
    if not token.get("refresh_token") or not token.get("client_id"):
        raise RuntimeError("Slack sign-in expired; run `daytrace connect slack`")
    refreshed = _post(TOKEN_URL, {
        "grant_type": "refresh_token", "refresh_token": token["refresh_token"],
        "client_id": token["client_id"], "resource": MCP,
    })
    access = refreshed.get("access_token") or (refreshed.get("authed_user") or {}).get("access_token")
    if not refreshed.get("ok") or not access:
        raise RuntimeError(
            f"Slack sign-in expired ({refreshed.get('error', 'unknown')}); run `daytrace connect slack`")
    user = refreshed.get("authed_user") or {}
    _save({
        "access_token": access,
        "refresh_token": refreshed.get("refresh_token") or user.get("refresh_token") or token["refresh_token"],
        "expires_in": refreshed.get("expires_in") or user.get("expires_in") or 0,
        "client_id": token["client_id"],
    })
    return access


def _open(req: urllib.request.Request, timeout: float):
    """Send one Slack request, waiting out a 429. The caller closes the response."""
    wait = 2.0
    for attempt in range(ATTEMPTS):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == ATTEMPTS - 1:
                raise
            retry = exc.headers.get("Retry-After") if exc.headers else None
            try:
                pause = float(retry) if retry else wait
            except ValueError:
                pause = wait
            exc.close()
            time.sleep(min(max(pause, 0), WAIT_CAP))
            wait = min(wait * 2, WAIT_CAP)


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
        with _open(req, 60) as resp:
            self.session_id = resp.headers.get("Mcp-Session-Id") or self.session_id
            body = resp.read().decode()
            if notify:
                return None
            if resp.headers.get_content_type() == "text/event-stream":
                body = _last_sse_data(body)
        reply = json.loads(body)
        if "error" in reply:
            raise RuntimeError(f"Slack {method}: {reply['error'].get('message')}")
        return reply["result"]

    def call(self, name: str, arguments: dict) -> str:
        """A tool's text output."""
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise RuntimeError(f"Slack {name}: {text}")
        return text


def _last_sse_data(stream: str) -> str:
    events = [e for e in stream.split("\n\n") if "data:" in e]
    return "\n".join(line[5:].strip() for line in events[-1].splitlines() if line.startswith("data:"))
