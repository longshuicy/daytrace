"""Confluence Data Center: pages and blog posts you created or edited, and your comments."""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Iterable

from ..models import RawEvent, parse_iso
from . import NotConfigured

DEFAULT_URL = "https://wiki.ncsa.illinois.edu"
PAGE_SIZE = 50
MAX_PAGES = 10


class Confluence:
    name = "confluence"
    overlap = timedelta(hours=1)

    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 20.0):
        self.base_url = (base_url or os.environ.get("CONFLUENCE_URL") or DEFAULT_URL).rstrip("/")
        self.token = token or os.environ.get("CONFLUENCE_TOKEN")
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None):
        url = self.base_url + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def fetch(self, since: datetime, until: datetime) -> Iterable[RawEvent]:
        if not self.token:
            raise NotConfigured("set CONFLUENCE_TOKEN in ~/.daytrace/.env")
        me = self._get("/rest/api/user/current")
        # CQL compares dates in the server's timezone, so ask a day wide and filter exactly below.
        day = (since - timedelta(days=1)).strftime("%Y-%m-%d")
        cql = f'contributor = currentUser() and lastmodified >= "{day}" order by lastmodified desc'
        for page in range(MAX_PAGES):
            data = self._get("/rest/api/content/search", {
                "cql": cql, "limit": PAGE_SIZE, "start": page * PAGE_SIZE,
                "expand": "version,space,container",
            })
            for item in data.get("results", []):
                raw = to_raw(item, me, self.base_url)
                if raw and since <= raw.start_at < until:
                    yield raw
            if data.get("size", 0) < PAGE_SIZE:
                break


def to_raw(item: dict, me: dict, base_url: str) -> RawEvent | None:
    """One event per version you made. Only the latest version is visible, so polling often catches more."""
    version = item.get("version", {})
    by = version.get("by", {})
    if not _same_user(by, me) or not version.get("when"):
        return None
    number = version.get("number", 1)
    kind = item.get("type", "page")
    if kind == "comment":
        on = item.get("container", {}).get("title") or item.get("title", "")
        title = f"Commented on: {on}"
    else:
        verb = "Created" if number == 1 else "Edited"
        title = f"{verb} {'blog post' if kind == 'blogpost' else 'page'}: {item.get('title', '')}"
    links = item.get("_links", {})
    return RawEvent(
        "confluence", f"{item['id']}:v{number}", "point", "recorded", parse_iso(version["when"]),
        category="wiki", title=title, url=base_url + links["webui"] if "webui" in links else None,
        always_work=True,
        attrs={"space": item.get("space", {}).get("key"), "type": kind, "version": number},
    )


def _same_user(a: dict, b: dict) -> bool:
    for key in ("userKey", "username", "accountId"):
        if a.get(key) and b.get(key):
            return a[key] == b[key]
    return False
