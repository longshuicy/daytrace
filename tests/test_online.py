import os
import subprocess
from datetime import datetime, timedelta

from daytrace.adapters.confluence import Confluence
from daytrace.adapters.confluence import to_raw as wiki_raw
from daytrace.adapters.github import GitHub
from daytrace.adapters.github import to_raw as gh_raw
from daytrace.config import load_env
from daytrace.intake import classify, transform
from daytrace.models import UTC, RawEvent, iso
from daytrace.store import Store
from daytrace.sync import sync

T0 = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)


def _remote(repo, url):
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", url], check=True)


def test_personal_owner_wins_anywhere_under_work_root(rules, projects):
    site = projects / "longshuicy.github.io"
    (site / ".git").mkdir(parents=True)
    _remote(site, "https://github.com/longshuicy/longshuicy.github.io.git")
    _remote(projects / "PDG", "git@github.com:ncsa/PDG.git")
    rules._repos = None

    commit = RawEvent("git", "a", "point", "recorded", T0, path=str(site))
    assert classify(commit, rules) == ("personal", None)
    session = RawEvent("claude_code", "b", "span", "recorded", T0, path=str(site / "src"))
    assert classify(session, rules) == ("personal", None)
    assert classify(RawEvent("git", "c", "point", "recorded", T0, path=str(projects / "PDG")), rules) == \
        ("work", "pdg")


def test_github_owner_rule_and_local_project(rules):
    ev = lambda owner, repo: RawEvent("github", "1", "point", "recorded", T0, owner=owner, repo=repo)
    assert classify(ev("LongShuicy", "notes"), rules) == ("personal", None)
    assert classify(ev("IN-CORE", "incore-helm"), rules) == ("work", "incore")  # via local clone
    assert classify(ev("ncsa", "unknown-repo"), rules) == ("work", "ncsa")


def test_github_repo_matched_to_clone_by_remote(rules, projects):
    clone = projects / "chatUIUC" / "self-hostable-uiuc-chat"
    (clone / ".git").mkdir(parents=True)
    _remote(clone, "https://github.com/Center-for-AI-Innovation/uiuc-chat-frontend.git")
    rules._repos = None
    ev = RawEvent("github", "1", "point", "recorded", T0, owner="Center-for-AI-Innovation",
                  repo="uiuc-chat-frontend")
    assert classify(ev, rules) == ("work", "chatuiuc")


def _gh(kind, payload, repo="IN-CORE/incore-helm"):
    return {"id": "42", "type": kind, "created_at": "2026-10-22T14:00:00Z",
            "repo": {"name": repo}, "payload": payload}


def test_github_event_mapping():
    pr = {"number": 7, "title": "Bump chart", "html_url": "https://github.com/x/pull/7", "merged": True}
    cases = {
        ("PullRequestEvent", "closed"): ("pr", "Merged PR #7: Bump chart"),
        ("PullRequestEvent", "opened"): ("pr", "Opened PR #7: Bump chart"),
    }
    for (kind, action), expected in cases.items():
        raw = gh_raw(_gh(kind, {"action": action, "pull_request": pr}))
        assert (raw.category, raw.title) == expected and raw.owner == "IN-CORE"

    review = gh_raw(_gh("PullRequestReviewEvent", {"pull_request": pr, "review": {"state": "approved"}}))
    assert review.title == "Approved PR #7: Bump chart" and review.category == "review"

    push = gh_raw(_gh("PushEvent", {"ref": "refs/heads/main", "size": 2}))
    assert push.title == "Pushed 2 commits to main"
    # Trimmed payloads (no size, no PR title) still produce something sensible.
    assert gh_raw(_gh("PushEvent", {"ref": "refs/heads/dev"})).title == "Pushed commits to dev"
    assert gh_raw(_gh("PullRequestEvent", {"action": "opened", "number": 9})).title == "Opened PR #9"

    comment = gh_raw(_gh("IssueCommentEvent", {"issue": {"number": 3, "title": "Bug", "pull_request": {}}}))
    assert comment.category == "review" and comment.title == "Commented on PR #3: Bug"
    assert gh_raw(_gh("WatchEvent", {})) is None
    assert gh_raw(_gh("PullRequestEvent", {"action": "assigned", "pull_request": pr})) is None
    assert gh_raw(_gh("IssuesEvent", {"action": "labeled", "issue": {"number": 1}})) is None


def test_github_title_lookup_skips_personal_and_caches():
    calls = []
    gh = GitHub({"longshuicy"}, token="t")
    gh._get = lambda path: calls.append(path) or {"title": "Bump chart"}
    trimmed = lambda repo: gh_raw(_gh("PullRequestEvent", {"action": "opened", "number": 9}, repo=repo))
    assert gh._with_title(trimmed("IN-CORE/incore-helm")).title == "Opened PR #9: Bump chart"
    assert gh._with_title(trimmed("IN-CORE/incore-helm")).title == "Opened PR #9: Bump chart"
    assert gh._with_title(trimmed("longshuicy/diary")).title == "Opened PR #9"
    assert calls == ["/repos/IN-CORE/incore-helm/issues/9"]


def test_github_personal_event_is_redacted(rules):
    raw = gh_raw(_gh("IssuesEvent", {"action": "opened", "issue": {"number": 1, "title": "Secret plans"}},
                     repo="longshuicy/diary"))
    e = transform(raw, rules)
    assert e.sensitivity == "personal" and e.title is None and e.url is None and e.attrs == {}


ME = {"userKey": "k1", "username": "cwang138"}


def _page(by, number=3, kind="page"):
    return {"id": "100", "type": kind, "title": "INCORE release notes",
            "version": {"number": number, "when": "2026-10-22T09:00:00.000-05:00", "by": by},
            "space": {"key": "INCORE"}, "container": {"title": "Parent page"},
            "_links": {"webui": "/display/INCORE/Release+notes"}}


def test_confluence_mapping():
    raw = wiki_raw(_page({"userKey": "k1"}), ME, "https://wiki.test")
    assert raw.title == "Edited page: INCORE release notes" and raw.always_work
    assert raw.url == "https://wiki.test/display/INCORE/Release+notes"
    assert raw.source_id == "100:v3" and iso(raw.start_at) == "2026-10-22T14:00:00Z"
    assert wiki_raw(_page({"userKey": "k1"}, number=1), ME, "").title.startswith("Created page")
    assert wiki_raw(_page({"userKey": "k1"}, kind="comment"), ME, "").title == "Commented on: Parent page"
    assert wiki_raw(_page({"userKey": "someone-else"}), ME, "") is None


def test_unconfigured_source_is_not_a_failure(tmp_path, rules, monkeypatch):
    monkeypatch.delenv("CONFLUENCE_TOKEN", raising=False)
    s = Store(tmp_path / "t.db")
    s.set_meta("day_zero", iso(T0))
    [r] = sync(s, rules, [Confluence()], T0 + timedelta(hours=1))
    assert r.ok and not r.configured and s.get_cursor("confluence") is None


def test_env_file_parsing(tmp_path, monkeypatch):
    for key in ("A", "B", "C", "D"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text('A=plain   # a comment\nexport B="quoted # kept"\n# C=skipped\nD=\n')
    load_env(env)
    assert os.environ["A"] == "plain" and os.environ["B"] == "quoted # kept"
    assert "C" not in os.environ and os.environ["D"] == ""
