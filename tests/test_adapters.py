import json
import sqlite3
import subprocess
from datetime import datetime, timedelta

from daytrace.adapters.activitywatch import stalled, to_raw
from daytrace.adapters.claude_code import parse_session_file
from daytrace.adapters.cursor import Cursor
from daytrace.adapters.git import Git, parse_shortstat
from daytrace.intake import transform
from daytrace.models import UTC


def test_activitywatch_window_and_afk():
    e = {"id": 7, "timestamp": "2026-10-22T14:00:00Z", "duration": 90.5,
         "data": {"app": "Code", "title": "x — PDG"}}
    raw = to_raw("aw-watcher-window_host", "currentwindow", e)
    assert raw.source_id == "aw-watcher-window_host:7" and raw.app == "Code"
    assert raw.end_at - raw.start_at == timedelta(seconds=90.5)

    afk = {"id": 8, "timestamp": "2026-10-22T15:00:00Z", "duration": 600, "data": {"status": "afk"}}
    assert to_raw("aw-watcher-afk_host", "afkstatus", afk).category == "idle"
    afk["data"]["status"] = "not-afk"
    assert to_raw("aw-watcher-afk_host", "afkstatus", afk) is None
    assert to_raw("b", "currentwindow", {**e, "duration": 0}) is None


def test_activitywatch_flags_a_stopped_window_watcher():
    def buckets(window, afk="2026-10-29T18:40:00+00:00"):
        b = {"afk": {"type": "afkstatus", "last_updated": afk}}
        if window is not None:
            b["window"] = {"type": "currentwindow", "last_updated": window}
        return b

    assert stalled(buckets("2026-10-29T18:39:59.5+00:00")) is None
    assert stalled(buckets("2026-10-29T18:20:00+00:00")) is None  # within the slack
    assert "stopped reporting" in stalled(buckets("2026-10-28T22:08:47.738000+00:00"))
    assert "isn't running" in stalled(buckets(None))
    assert stalled({"window": {"type": "currentwindow", "last_updated": "2026-10-28T22:08:47+00:00"}}) is None


def _line(ts, kind="user", prompt="p1", cwd="/w/PDG", session="s1"):
    return json.dumps({"type": kind, "timestamp": ts, "cwd": cwd, "sessionId": session,
                       "promptId": prompt if kind == "user" else None, "gitBranch": "main",
                       "message": {"content": "never stored"}})


def test_claude_code_splits_on_gaps(tmp_path):
    f = tmp_path / "s1.jsonl"
    f.write_text("\n".join([
        json.dumps({"type": "queue-operation", "timestamp": "2026-10-22T13:59:00Z"}),
        _line("2026-10-22T14:00:00Z"),
        _line("2026-10-22T14:03:00Z", kind="assistant"),
        _line("2026-10-22T14:08:00Z", prompt="p2"),
        _line("2026-10-22T15:30:00Z", prompt="p3"),
        "not json",
    ]))
    spans = parse_session_file(f)
    assert len(spans) == 2
    first, second = spans
    assert first.path == "/w/PDG" and first.attrs == {"prompts": 2, "branch": "main"}
    assert first.end_at - first.start_at == timedelta(minutes=8)
    assert second.end_at - second.start_at == timedelta(minutes=1)  # minimum span
    assert "never stored" not in repr(spans)


def test_cursor_chats_split_on_gaps(tmp_path):
    (tmp_path / "globalStorage").mkdir()
    for ws, d in {"w1": {"folder": "file:///w/My%20PDG"},
                  "w2": {"workspace": f"file://{tmp_path}/two.code-workspace"}}.items():
        (tmp_path / "workspaceStorage" / ws).mkdir(parents=True)
        (tmp_path / "workspaceStorage" / ws / "workspace.json").write_text(json.dumps(d))
    (tmp_path / "two.code-workspace").write_text(json.dumps({"folders": [{"path": "SMM"}, {"path": "other"}]}))
    db = sqlite3.connect(tmp_path / "globalStorage" / "state.vscdb")
    db.execute("CREATE TABLE composerHeaders (composerId, workspaceId, lastUpdatedAt, isSubagent)")
    db.execute("CREATE TABLE cursorDiskKV (key, value)")
    msg = lambda t, kind: {"createdAt": f"2026-10-22T{t}:00Z", "type": kind,
                           "grouping": {"textPreview": "never stored"}}
    chats = {"c1": ("w1", 0, [msg("14:00", 1), msg("14:03", 2), msg("14:08", 1), msg("15:30", 1)]),
             "c2": ("w2", 0, [msg("16:00", 1)]),
             "sub": ("w1", 1, [msg("14:01", 2)]),
             "old": ("w1", 0, [msg("09:00", 1)])}
    for cid, (ws, sub, headers) in chats.items():
        updated = datetime(2026, 10, 21 if cid == "old" else 22, tzinfo=UTC).timestamp() * 1000
        db.execute("INSERT INTO composerHeaders VALUES (?, ?, ?, ?)", (cid, ws, updated, sub))
        db.execute("INSERT INTO cursorDiskKV VALUES (?, ?)",
                   (f"composerData:{cid}", json.dumps({"name": "never stored", "fullConversationHeadersOnly": headers})))
    db.commit()

    since = datetime(2026, 10, 22, tzinfo=UTC)
    spans = list(Cursor(tmp_path).fetch(since, since + timedelta(days=1)))
    assert [(s.path, s.attrs["prompts"]) for s in spans] == \
        [("/w/My PDG", 2), ("/w/My PDG", 1), (str((tmp_path / "SMM").resolve()), 1)]
    assert spans[0].end_at - spans[0].start_at == timedelta(minutes=8)
    assert spans[1].end_at - spans[1].start_at == timedelta(minutes=1)  # minimum span
    assert "never stored" not in repr(spans)


def test_shortstat():
    assert parse_shortstat(" 3 files changed, 10 insertions(+), 2 deletions(-)") == \
        {"files": 3, "insertions": 10, "deletions": 2}
    assert parse_shortstat(" 1 file changed, 1 deletion(-)") == \
        {"files": 1, "insertions": 0, "deletions": 1}


def test_git_reads_own_commits(rules, projects, monkeypatch):
    repo = projects / "PDG"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    env = {"GIT_AUTHOR_DATE": "2026-10-22T14:00:00Z", "GIT_COMMITTER_DATE": "2026-10-22T14:00:00Z"}
    for email, msg in [("me@example.edu", "Fix helm values"), ("other@x.org", "Not mine")]:
        (repo / f"{msg}.txt").write_text(msg)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", f"user.email={email}", "-c", "user.name=x",
                        "commit", "-q", "-m", msg], check=True, env={**env, "PATH": "/usr/bin:/bin"})
    rules.git_authors = ["me@example.edu"]
    since = datetime(2026, 10, 22, tzinfo=UTC)
    commits = list(Git(rules).fetch(since, since + timedelta(days=1)))
    assert [c.title for c in commits] == ["Fix helm values"]
    assert commits[0].path == str(repo) and commits[0].attrs["files"] == 1
    assert commits[0].attrs["branch"] in ("main", "master")

    # A rewritten copy on another branch (rebase, cherry-pick) has a new SHA but is the same work.
    git = ["git", "-C", str(repo), "-c", "user.email=me@example.edu", "-c", "user.name=x"]
    subprocess.run(git + ["checkout", "-q", "-b", "copy", "HEAD~1"], check=True)
    later = {**env, "GIT_COMMITTER_DATE": "2026-10-22T16:00:00Z", "PATH": "/usr/bin:/bin"}
    subprocess.run(git + ["commit", "-q", "--amend", "--no-edit"], check=True, env=later)
    copies = list(Git(rules).fetch(since, since + timedelta(days=1)))
    assert len({c.attrs["sha"] for c in copies}) == 2
    assert len({c.source_id for c in copies}) == 1

    stored = transform(copies[0], rules)
    assert stored.attrs["sha"] == copies[0].attrs["sha"] and stored.project == "pdg"
