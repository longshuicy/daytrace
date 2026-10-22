from datetime import datetime, timedelta

from daytrace.intake import classify, transform
from daytrace.models import UTC, RawEvent
from daytrace.scrub import scrub, strip_url

T0 = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)


def window(app, title, **kw):
    return RawEvent("activitywatch", "b:1", "span", "observed", T0, T0 + timedelta(minutes=5),
                    app=app, title=title, **kw)


def test_work_path_gets_project_mapping(rules, projects):
    raw = RawEvent("claude_code", "s", "span", "recorded", T0, path=str(projects / "INCORE-2.0/incore-helm"))
    assert classify(raw, rules) == ("work", "incore")


def test_personal_path_wins_over_work_root(rules, projects):
    raw = RawEvent("git", "sha", "point", "recorded", T0, path=str(projects / "personal/daytrace"))
    assert classify(raw, rules) == ("personal", None)


def test_editor_title_resolves_repo(rules):
    assert classify(window("Code", "values.yaml — incore-helm"), rules) == ("work", "incore")
    assert classify(window("Code", "notes.md — twentyfour"), rules) == ("personal", None)


def test_terminal_title_with_path(rules, projects):
    assert classify(window("iTerm2", f"zsh {projects}/PDG"), rules) == ("work", "pdg")


def test_editor_without_known_repo_is_unknown(rules):
    assert classify(window("Code", "scratch.py"), rules) == ("unknown", None)


def test_browser_needs_work_title(rules):
    assert classify(window("Google Chrome", "Pull requests · GitHub"), rules)[0] == "work"
    assert classify(window("Google Chrome", "Some recipe blog"), rules)[0] == "unknown"
    assert classify(window("Google Chrome", "Inbox - me@gmail.com"), rules)[0] == "personal"


def test_work_app_and_personal_app(rules):
    assert classify(window("Microsoft Outlook", "Re: budget"), rules) == ("work", None)
    assert classify(window("Spotify", "Some song"), rules) == ("personal", None)


def test_private_browsing_is_dropped(rules):
    assert transform(window("Safari", "Private Browsing — anything"), rules) is None
    assert transform(window("Google Chrome", "Search (Incognito)"), rules) is None


def test_non_work_keeps_only_category_and_time(rules):
    e = transform(window("Spotify", "Some song", url="https://x.test/?q=1"), rules)
    assert e.sensitivity == "personal" and e.category == "other"
    assert e.title is None and e.url is None and e.project is None and e.attrs == {}
    assert e.start_at == "2026-10-22T14:00:00Z" and e.end_at == "2026-10-22T14:05:00Z"


def test_work_title_is_scrubbed(rules):
    e = transform(window("Microsoft Outlook", "token=abc123 for ghp_" + "a" * 36), rules)
    assert "abc123" not in e.title and "ghp_" not in e.title
    assert e.attrs["app"] == "Microsoft Outlook" and e.category == "email"


def test_scrub_and_strip_url():
    assert strip_url("https://a.illinois.edu/p?session=secret#x") == "https://a.illinois.edu/p"
    assert scrub("see https://h/p?token=zzz now") == "see https://h/p now"
    assert scrub("key sk-" + "A" * 30) == "key [redacted]"
    assert scrub("Fix helm values") == "Fix helm values"
