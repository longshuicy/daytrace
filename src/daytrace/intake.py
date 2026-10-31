"""Turn raw adapter output into stored events: drop, classify, categorize, redact."""

import re
from pathlib import Path

from .models import Event, RawEvent, iso
from .rules import Rules
from .scrub import scrub, strip_url

# Attributes that look like secrets to the scrubber but aren't (a commit SHA is 40 hex chars,
# an Outlook conversation id is long base64).
UNSCRUBBED = {"sha", "thread"}

_HOST = re.compile(r"^((?:[\w-]+\.)+[a-z]{2,}|localhost(?::\d+)?)(?:[/:]|$)", re.I)
_TITLE_PARTS = re.compile(r"\s+[-–—·|]\s+")
_GITHUB_PATH = re.compile(r"^[\w.-]+/[\w.-]+$")  # GitHub file pages end in "owner/repo", not "GitHub"
_NOT_A_SITE = {"new tab", "untitled", "login", "log in", "sign in"}


def site_of(title: str | None) -> str | None:
    """The site a browser tab title names: its host when the title is an address, else its last part
    ("Pull requests · octocat/hello-world · GitHub" -> "GitHub"), or the whole title when it is just a short
    name ("Microsoft Azure"). None when the title doesn't say."""
    if not title:
        return None
    m = _HOST.match(title)
    if m:
        return m.group(1).lower().removeprefix("www.")
    parts = _TITLE_PARTS.split(title)
    last = parts[-1].strip()
    if len(parts) > 1 and _GITHUB_PATH.match(last):
        return "GitHub"
    if len(parts) > 1:
        return last if 0 < len(last) <= 40 else None
    return last if len(last.split()) <= 3 and last.lower() not in _NOT_A_SITE else None


def classify(raw: RawEvent, rules: Rules) -> tuple[str, str | None]:
    """Return (sensitivity, project): ("private", None) when a private rule matches, else ("normal", project)."""
    path = Path(raw.path) if raw.path else None
    if not path and raw.owner and raw.repo:
        path = rules.local_clone(raw.owner, raw.repo)
    if (raw.private or raw.app in rules.private_apps or rules.matches(rules.private_titles, raw.title)
            or rules.browser_profile(raw.title) in rules.private_profiles
            or (path and rules.is_private_path(path))):
        return ("private", None)
    if raw.owner and raw.repo:
        return ("normal", rules.project_for_repo(raw.owner, raw.repo))
    if path:
        return ("normal", rules.project_for_path(path))
    if raw.title:
        target = rules.resolve_title(raw.title)
        if target and rules.is_private_path(target):
            return ("private", None)
        if target and rules.project_for_path(target):
            return ("normal", rules.project_for_path(target))
    return ("normal", rules.project_for_title(raw.title))


def transform(raw: RawEvent, rules: Rules) -> Event | None:
    if rules.is_dropped(raw.title):
        return None
    sensitivity, project = classify(raw, rules)
    if sensitivity == "private":
        # Private keeps only that it happened and when.
        category, title, url, attrs = "private", None, None, {}
    else:
        category = raw.category or rules.category_for(raw.app, raw.title)
        title, url = scrub(raw.title), strip_url(raw.url)
        attrs = {k: scrub(v) if isinstance(v, str) and k not in UNSCRUBBED else v
                 for k, v in raw.attrs.items()}
        if raw.app:
            attrs["app"] = raw.app
        if rules.categories.get(raw.app or "") == "browse" and site_of(title):
            attrs["site"] = site_of(title)
    return Event(
        source=raw.source,
        source_id=raw.source_id,
        kind=raw.kind,
        evidence=raw.evidence,
        start_at=iso(raw.start_at),
        end_at=iso(raw.end_at) if raw.end_at else None,
        category=category,
        project=project,
        title=title,
        url=url,
        sensitivity=sensitivity,
        attrs=attrs,
    )
