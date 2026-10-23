"""GitHub: your pull requests, reviews, issues, comments and pushes, from your own activity feed."""

import json
import os
import subprocess
import urllib.request
from datetime import datetime, timedelta
from typing import Iterable

from ..models import RawEvent, parse_iso

API = "https://api.github.com"
MAX_PAGES = 3  # the events feed holds at most 300 events
# PR and issue actions worth a trace; assignments, labels and edits are noise.
KEPT_ACTIONS = {"opened", "closed", "reopened", "ready_for_review"}


class GitHub:
    name = "github"
    overlap = timedelta(minutes=30)  # the feed can lag a few minutes behind

    def __init__(self, token: str | None = None, timeout: float = 15.0):
        self._token = token
        self.timeout = timeout
        self._titles: dict[str, str | None] = {}

    def token(self) -> str:
        if not self._token:
            self._token = os.environ.get("GITHUB_TOKEN") or _gh_token()
        if not self._token:
            raise RuntimeError("no GitHub token: set GITHUB_TOKEN in ~/.daytrace/.env or run `gh auth login`")
        return self._token

    def _get(self, path: str):
        req = urllib.request.Request(API + path, headers={
            "Authorization": f"Bearer {self.token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def fetch(self, since: datetime, until: datetime) -> Iterable[RawEvent]:
        login = self._get("/user")["login"]
        for page in range(1, MAX_PAGES + 1):
            events = self._get(f"/users/{login}/events?per_page=100&page={page}")
            for e in events:
                at = parse_iso(e["created_at"])
                if since <= at < until:
                    raw = to_raw(e)
                    if raw:
                        yield self._with_title(raw)
            if not events or parse_iso(events[-1]["created_at"]) < since:
                break


    def _with_title(self, raw: RawEvent) -> RawEvent:
        """The feed no longer includes PR/issue titles; look each one up once."""
        number = raw.attrs.get("number")
        if not number or raw.attrs.get("has_title"):
            return raw
        key = f"{raw.attrs['repo']}#{number}"
        if key not in self._titles:
            try:
                self._titles[key] = self._get(f"/repos/{raw.attrs['repo']}/issues/{number}").get("title")
            except OSError:
                self._titles[key] = None
        if self._titles[key]:
            raw.title = f"{raw.title}: {self._titles[key]}"
        return raw


def _gh_token() -> str | None:
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def to_raw(e: dict) -> RawEvent | None:
    """Map one feed event to a point event, or None for types we don't track."""
    full_name = e["repo"]["name"]
    owner, _, repo = full_name.partition("/")
    payload = e.get("payload", {})
    kind = e["type"]
    web = f"https://github.com/{full_name}"

    if kind == "PushEvent":
        branch = payload.get("ref", "").removeprefix("refs/heads/")
        size = payload.get("size")
        what = f"{size} commit{'s' * (size != 1)}" if size else "commits"
        category, title, url = "push", f"Pushed {what} to {branch}", f"{web}/tree/{branch}"
    elif kind == "PullRequestEvent":
        pr = payload.get("pull_request", {})
        action = payload.get("action", "updated")
        if action not in KEPT_ACTIONS:
            return None
        if action == "closed" and pr.get("merged"):
            action = "merged"
        category, title, url = "pr", _numbered(f"{action.capitalize()} PR", pr, payload), _link(pr, payload, web, "pull")
    elif kind == "PullRequestReviewEvent":
        pr = payload.get("pull_request", {})
        state = payload.get("review", {}).get("state", "")
        verb = {"approved": "Approved", "changes_requested": "Requested changes on"}.get(state.lower(), "Reviewed")
        category, title, url = "review", _numbered(f"{verb} PR", pr, payload), _link(pr, payload, web, "pull")
    elif kind == "PullRequestReviewCommentEvent":
        pr = payload.get("pull_request", {})
        category, title, url = "review", _numbered("Commented on PR", pr, payload), _link(pr, payload, web, "pull")
    elif kind == "IssuesEvent":
        issue = payload.get("issue", {})
        if payload.get("action") not in KEPT_ACTIONS:
            return None
        action = payload["action"].capitalize()
        category, title, url = "issue", _numbered(f"{action} issue", issue, payload), _link(issue, payload, web, "issues")
    elif kind == "IssueCommentEvent":
        issue = payload.get("issue", {})
        is_pr = "pull_request" in issue
        category = "review" if is_pr else "issue"
        title = _numbered(f"Commented on {'PR' if is_pr else 'issue'}", issue, payload)
        url = _link(issue, payload, web, "pull" if is_pr else "issues")
    elif kind == "CreateEvent":
        ref_type = payload.get("ref_type", "ref")
        ref = payload.get("ref") or ""
        category, title, url = "repo", f"Created {ref_type} {ref}".strip(), web
    elif kind == "ReleaseEvent":
        release = payload.get("release", {})
        category, title, url = "release", f"Released {release.get('tag_name', '')}".strip(), release.get("html_url")
    else:
        return None

    item = payload.get("pull_request") or payload.get("issue") or {}
    return RawEvent(
        "github", str(e["id"]), "point", "recorded", parse_iso(e["created_at"]),
        category=category, title=title, url=url or web, owner=owner, repo=repo,
        attrs={"repo": full_name, "type": kind, "number": item.get("number") or payload.get("number"),
               "has_title": bool(item.get("title"))},
    )


def _link(item: dict, payload: dict, web: str, kind: str) -> str | None:
    """The PR or issue page. The public events feed often leaves out html_url, but not the number."""
    number = item.get("number") or payload.get("number")
    return item.get("html_url") or (f"{web}/{kind}/{number}" if number else None)


def _numbered(prefix: str, item: dict, payload: dict) -> str:
    number = item.get("number") or payload.get("number")
    label = f"{prefix} #{number}" if number else prefix
    return f"{label}: {item['title']}" if item.get("title") else label
