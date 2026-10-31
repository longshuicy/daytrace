from datetime import datetime, timedelta

from daytrace.intake import classify, transform
from daytrace.models import UTC, RawEvent
from daytrace.scrub import scrub, strip_url

T0 = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)


def window(app, title, **kw):
    return RawEvent("activitywatch", "b:1", "span", "observed", T0, T0 + timedelta(minutes=5),
                    app=app, title=title, **kw)


def test_path_gets_project_mapping(rules, projects):
    raw = RawEvent("claude_code", "s", "span", "recorded", T0, path=str(projects / "INCORE-2.0/incore-helm"))
    assert classify(raw, rules) == ("normal", "incore")


def test_most_specific_project_root_wins(rules, projects):
    raw = RawEvent("git", "sha", "point", "recorded", T0, path=str(projects / "personal/daytrace"))
    assert classify(raw, rules) == ("normal", "daytrace")


def test_editor_title_resolves_repo(rules):
    assert classify(window("Code", "values.yaml — incore-helm"), rules) == ("normal", "incore")
    assert classify(window("Code", "notes.md — twentyfour"), rules) == ("normal", "twentyfour")


def test_terminal_title_with_path(rules, projects):
    assert classify(window("iTerm2", f"zsh {projects}/PDG"), rules) == ("normal", "pdg")


def test_project_titles_name_a_project_when_nothing_else_does(rules, projects, tmp_path):
    from daytrace.rules import Rules
    from daytrace.store import Store

    rules.project_titles = Rules.from_text('[project_titles]\n"team chat" = ["team.?chat"]\n').project_titles
    meeting = RawEvent("outlook_calendar", "m", "plan", "observed", T0, title="Team Chat Design")
    assert classify(meeting, rules) == ("normal", "team chat")
    assert classify(window("Slack", "team-chat-dev (Channel) - Slack"), rules) == ("normal", "team chat")
    assert classify(window("iTerm2", f"team-chat {projects}/PDG"), rules) == ("normal", "pdg")  # a path wins

    s = Store(tmp_path / "t.db")
    named = rules.project_titles
    rules.project_titles = []
    s.upsert([transform(window("Slack", "team-chat-dev"), rules),
              transform(RawEvent("outlook_calendar", "c", "plan", "observed", T0, title="Counselling"), rules)], T0)
    rules.project_titles = named
    assert s.retag(rules.project_for_title, lambda t: t == "Counselling") == (1, 1)
    rows = s.conn.execute("SELECT project, sensitivity, title FROM events ORDER BY source").fetchall()
    assert [tuple(r) for r in rows] == [("team chat", "normal", "team-chat-dev"), (None, "private", None)]


def test_anything_not_private_is_normal(rules):
    assert classify(window("Code", "scratch.py"), rules) == ("normal", None)
    assert classify(window("Google Chrome", "Some recipe blog"), rules) == ("normal", None)
    assert classify(window("Preview", "paper.pdf"), rules) == ("normal", None)


def test_private_apps_titles_and_paths(rules, projects):
    assert classify(window("Spotify", "Some song"), rules) == ("private", None)
    assert classify(window("Google Chrome", "Inbox - me@gmail.com"), rules) == ("private", None)
    rules.private_paths = [projects / "personal/twentyfour"]
    raw = RawEvent("git", "sha", "point", "recorded", T0, path=str(projects / "personal/twentyfour"))
    assert classify(raw, rules) == ("private", None)
    assert classify(window("Code", "notes.md — twentyfour"), rules) == ("private", None)
    assert classify(window("Code", "values.yaml — incore-helm"), rules) == ("normal", "incore")


def test_private_browsing_is_dropped(rules):
    assert transform(window("Safari", "Private Browsing — anything"), rules) is None
    assert transform(window("Google Chrome", "Search (Incognito)"), rules) is None


def test_private_keeps_only_time(rules):
    e = transform(window("Spotify", "Some song", url="https://x.test/?q=1"), rules)
    assert e.sensitivity == "private" and e.category == "private"
    assert e.title is None and e.url is None and e.project is None and e.attrs == {}
    assert e.start_at == "2026-10-22T14:00:00Z" and e.end_at == "2026-10-22T14:05:00Z"


def test_title_is_scrubbed(rules):
    e = transform(window("Microsoft Outlook", "token=abc123 for ghp_" + "a" * 36), rules)
    assert "abc123" not in e.title and "ghp_" not in e.title
    assert e.attrs["app"] == "Microsoft Outlook" and e.category == "email"


def test_scrub_and_strip_url():
    assert strip_url("https://a.example.edu/p?session=secret#x") == "https://a.example.edu/p"
    assert scrub("see https://h/p?token=zzz now") == "see https://h/p now"
    assert scrub("key sk-" + "A" * 30) == "key [redacted]"
    assert scrub("Fix helm values") == "Fix helm values"


def test_web_apps_recognized_from_browser_titles(rules):
    assert rules.category_for("Google Chrome", "INCORE plan - Google Docs") == "docs"
    assert rules.category_for("Google Chrome", "Budget - Google Sheets - Google Chrome - Sam") == "docs"
    assert rules.category_for("Google Chrome", "Release notes - Team Wiki - Confluence") == "wiki"
    assert rules.category_for("Google Chrome", "Mail - Outlook") == "email"
    assert rules.category_for("Google Chrome", "Pull requests · IN-CORE/incore-helm · GitHub") == "review"
    assert rules.category_for("Google Chrome", "Some recipe blog") == "browse"
    assert rules.category_for("Code", "notes - Google Docs.md") == "code"  # only browsers get title categories


def test_private_browser_profile(rules):
    rules.private_profiles = {"Home"}
    doc = lambda title: window("Google Chrome", title)
    assert transform(doc("Inbox - Google Chrome - Home"), rules).sensitivity == "private"
    e = transform(doc("Trip plan - Google Docs - Google Chrome - Sam (example.edu)"), rules)
    assert e.sensitivity == "normal" and e.category == "docs" and e.title == "Trip plan - Google Docs"


def test_site_from_browser_title(rules):
    from daytrace.intake import site_of
    assert site_of("Pull requests · IN-CORE/incore-helm · GitHub") == "GitHub"
    assert site_of("www.arxiv.org/abs/2401.1") == "arxiv.org"
    assert site_of("Snakefile at main · PermafrostDiscoveryGateway/rtsmapping-snakemake") == "GitHub"
    assert site_of("Microsoft Azure") == "Microsoft Azure" and site_of("New Tab") is None
    assert site_of("Sign in to your account") is None
    assert transform(window("Google Chrome", "INCORE plan - Google Docs"), rules).attrs["site"] == "Google Docs"
    assert "site" not in transform(window("Code", "notes - Google Docs.md"), rules).attrs
