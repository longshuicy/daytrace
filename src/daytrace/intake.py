"""Turn raw adapter output into stored events: drop, classify, categorize, redact."""

from pathlib import Path

from .models import Event, RawEvent, iso
from .rules import Rules
from .scrub import scrub, strip_url

# Attributes that look like secrets to the scrubber but aren't (a commit SHA is 40 hex chars,
# an Outlook conversation id is long base64).
UNSCRUBBED = {"sha", "thread"}


def classify(raw: RawEvent, rules: Rules) -> tuple[str, str | None]:
    """Return (sensitivity, project). Fails closed: anything not clearly work is unknown."""
    if raw.owner and raw.repo:
        return rules.classify_owner(raw.owner, raw.repo)
    if raw.path:
        result = rules.classify_path(Path(raw.path))
        if result:
            return result
    profile = rules.profile_sensitivity(raw.title)
    if profile == "personal":
        return ("personal", None)
    if raw.app in rules.personal_apps or rules.matches(rules.personal_titles, raw.title):
        return ("personal", None)
    if raw.always_work or profile == "work":
        return ("work", None)
    if raw.title:
        target = rules.resolve_title(raw.title)
        if target:
            result = rules.classify_path(target)
            if result:
                return result
    if raw.app in rules.work_apps:
        return ("work", None)
    if raw.app in rules.context_apps and rules.matches(rules.work_titles, raw.title):
        return ("work", None)
    return ("unknown", None)


def transform(raw: RawEvent, rules: Rules) -> Event | None:
    if rules.is_dropped(raw.title):
        return None
    sensitivity, project = classify(raw, rules)
    category = raw.category or rules.category_for(raw.app, raw.title)
    if sensitivity == "work":
        title, url = scrub(raw.title), strip_url(raw.url)
        attrs = {k: scrub(v) if isinstance(v, str) and k not in UNSCRUBBED else v
                 for k, v in raw.attrs.items()}
        if raw.app:
            attrs["app"] = raw.app
    else:
        # Non-work keeps only category and timing.
        title = url = project = None
        attrs = {}
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
