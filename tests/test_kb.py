import json
from datetime import date, datetime, timedelta

from daytrace import kb, mcp
from daytrace.models import UTC, RawEvent, iso
from daytrace.store import Store
from daytrace.sync import sync

DAY = date(2026, 10, 22)
ZERO = datetime(2026, 10, 22, 5, 0, tzinfo=UTC)
TOKEN = "sk-" + "A" * 30


class Fake:
    name, overlap = "fake", timedelta(0)

    def __init__(self, events):
        self.events = events

    def fetch(self, since, until):
        return self.events


def local(h, m=0, s=0):
    return datetime.combine(DAY, datetime.min.time()).astimezone() + timedelta(
        hours=h, minutes=m, seconds=s
    )


def test_time_slice_merges_windows_and_keeps_private_bare(
    tmp_path, rules, projects, monkeypatch
):
    monkeypatch.setenv("DAYTRACE_HOME", str(tmp_path / "home"))
    store = Store(tmp_path / "t.db")
    store.set_meta("day_zero", iso(ZERO))
    pdg = str(projects / "PDG")
    meeting_at = iso(local(10))
    _write_note(tmp_path / "home", meeting_at)
    events = [
        RawEvent(
            "activitywatch",
            "w1",
            "span",
            "observed",
            local(9),
            local(9, 10),
            category="code",
            app="Code",
            title="zephyrwindow editor",
        ),
        RawEvent(
            "activitywatch",
            "w2",
            "span",
            "observed",
            local(9, 10, 30),
            local(9, 20),
            category="code",
            app="Code",
            title="zephyrwindow editor",
        ),
        RawEvent(
            "slack",
            "s1",
            "point",
            "recorded",
            local(9, 5),
            category="chat",
            title="#pdg · 2 messages",
            url="https://acme.slack.com/archives/C1/p1",
            attrs={"count": 2},
        ),
        RawEvent(
            "outlook_mail",
            "e1",
            "point",
            "recorded",
            local(9, 6),
            category="email",
            title="Hello bob@x.org",
        ),
        RawEvent(
            "git",
            "c2",
            "point",
            "recorded",
            local(9, 7),
            category="commit",
            title=f"ship {TOKEN}",
            path=pdg,
        ),
        RawEvent(
            "outlook_calendar",
            "m1",
            "plan",
            "scheduled",
            local(10),
            local(11),
            category="meeting",
            title="PDG weekly",
            path=pdg,
        ),
        RawEvent(
            "git",
            "c1",
            "point",
            "recorded",
            local(12),
            category="commit",
            title="zephyrvalve landed",
            path=pdg,
        ),
        RawEvent(
            "activitywatch",
            "p1",
            "span",
            "observed",
            local(14),
            local(14, 40),
            app="Spotify",
            title="Very private song",
        ),
    ]
    sync(store, rules, [Fake(events)], local(15))

    morning = kb.at(store, local(9, 15), 20, local(15))
    code = [b for b in morning["blocks"] if b.get("category") == "code"]
    assert (
        len(code) == 1
        and code[0]["titles"] == ["zephyrwindow editor"]
        and code[0]["app"] == "Code"
    )
    dumped = json.dumps(morning)
    assert "slack.com" not in dumped and "archives" not in dumped
    mail = next(p for p in morning["points"] if p.get("category") == "email")
    assert mail["title"] == "Hello [email]"
    leaked = next(p for p in morning["points"] if p.get("category") == "commit")
    assert TOKEN not in leaked["title"] and "[redacted]" in leaked["title"]

    during = kb.at(store, local(10, 30), 15, local(15))
    [meeting] = [b for b in during["blocks"] if b.get("source") == "outlook_calendar"]
    assert (
        meeting["note_id"] == "n1" and "Ship the export" in meeting["digest"]["topics"]
    )

    private = kb.at(store, local(14, 20), 15, local(15))
    assert private["blocks"] == [
        {
            "start": private["blocks"][0]["start"],
            "end": private["blocks"][0]["end"],
            "category": "private",
        }
    ]
    assert "Very private song" not in json.dumps(
        private
    ) and "Spotify" not in json.dumps(private)

    day = kb.summary(store, DAY, DAY, local(15), "day")
    assert day["totals"]["private_min"] == 40
    assert "Very private song" not in json.dumps(day)
    assert (
        day["open"] == []
    )  # the commit after the meeting shares "zephyrvalve" with the action item
    assert day["meetings"][0]["note_id"] == "n1"
    assert any(b.get("category") == "private" for b in day["blocks"])


def test_a_later_day_without_notes_closes_an_action_item(
    tmp_path, rules, projects, monkeypatch
):
    monkeypatch.setenv("DAYTRACE_HOME", str(tmp_path / "home"))
    store = Store(tmp_path / "t.db")
    store.set_meta("day_zero", iso(ZERO))
    pdg = str(projects / "PDG")
    _write_note(tmp_path / "home", iso(local(10)))
    next_day = local(9) + timedelta(days=1)
    events = [
        RawEvent("outlook_calendar", "m1", "plan", "scheduled", local(10), local(11),
                 category="meeting", title="PDG weekly", path=pdg),
        RawEvent("git", "c1", "point", "recorded", next_day,
                 category="commit", title="zephyrvalve landed", path=pdg),
    ]
    now = next_day + timedelta(hours=6)
    sync(store, rules, [Fake(events)], now)

    meeting_day = kb.summary(store, DAY, DAY, now, "day")
    assert meeting_day["open"][0]["text"] == "Order the zephyrvalve"
    both = kb.summary(store, DAY, DAY + timedelta(days=1), now)
    assert both["open"] == []  # Friday's commit closes Thursday's item


def test_search_covers_notes_and_subjects_not_window_titles(
    tmp_path, rules, projects, monkeypatch
):
    monkeypatch.setenv("DAYTRACE_HOME", str(tmp_path / "home"))
    store = Store(tmp_path / "t.db")
    store.set_meta("day_zero", iso(ZERO))
    pdg = str(projects / "PDG")
    _write_note(tmp_path / "home", iso(local(10)))
    sync(
        store,
        rules,
        [
            Fake(
                [
                    RawEvent(
                        "activitywatch",
                        "w1",
                        "span",
                        "observed",
                        local(9),
                        local(9, 20),
                        category="code",
                        app="Code",
                        title="zephyrwindow editor",
                    ),
                    RawEvent(
                        "git",
                        "c1",
                        "point",
                        "recorded",
                        local(12),
                        category="commit",
                        title="zephyrvalve landed",
                        path=pdg,
                    ),
                    RawEvent(
                        "outlook_calendar",
                        "m1",
                        "plan",
                        "scheduled",
                        local(10),
                        local(11),
                        category="meeting",
                        title="PDG weekly",
                        path=pdg,
                    ),
                ]
            )
        ],
        local(15),
    )
    store.save_summary("week:2026-10-18", {}, "The zephyrwriteup held.", "m", local(15))

    notes = kb.search(store, "zephyrvalve", local(15))
    assert [h["kind"] for h in notes["hits"]] == ["note", "subject"]
    assert (
        "zephyrvalve" in notes["hits"][0]["excerpt"] and notes["hits"][0]["id"] == "n1"
    )
    assert notes["hits"][0]["project"] == "pdg"
    assert notes["hits"][1]["title"] == "zephyrvalve landed"

    assert kb.search(store, "zephyrwindow", local(15))["hits"] == []
    assert (
        kb.search(store, "zephyrwriteup", local(15))["hits"][0]["kind"] == "narrative"
    )

    body = kb.note("n1", local(15))
    assert (
        "zephyrvalve rollout" in body["markdown"]
        and "granola_id" not in body["markdown"]
    )


def test_mcp_stays_off_until_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("DAYTRACE_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    store = Store(tmp_path / "home" / "daytrace.db")
    store.set_meta("day_zero", iso(ZERO))
    store.close()
    refused = mcp.call_tool("at", {"time": iso(local(9))})
    assert refused["isError"] and "kb enable" in refused["content"][0]["text"]
    listed = mcp.handle({"method": "tools/list"})
    assert {t["name"] for t in listed["tools"]} == {"at", "summary", "search", "note"}
    store = Store(tmp_path / "home" / "daytrace.db")
    kb.enable(store)
    store.close()
    found = mcp.call_tool("search", {"query": "zephyrvalve"})
    assert not found.get("isError")
    assert json.loads(found["content"][0]["text"])["hits"] == []


def _write_note(home, meeting_at: str) -> None:
    folder = home / "notes" / "granola"
    folder.mkdir(parents=True)
    name = "2026-10-22-1000-pdg-weekly.md"
    (folder / "account.json").write_text(json.dumps({"name": "Casey"}))
    (folder / name).write_text(
        "---\n"
        'title: "PDG weekly"\n'
        "granola_id: n1\n"
        f"meeting_at: {meeting_at}\n"
        f"updated_at: {meeting_at}\n"
        "url: https://notes.granola.ai/n1\n"
        "---\n\n"
        "# PDG weekly\n\n"
        "# Ship the export\n\n"
        "Talked about the zephyrvalve rollout.\n\n"
        "# Next steps\n\n"
        "- **Order the zephyrvalve** (Casey)\n"
    )
    (folder / "index.json").write_text(
        json.dumps(
            {
                "n1": {
                    "title": "PDG weekly",
                    "meeting_at": meeting_at,
                    "updated_at": meeting_at,
                    "url": "https://notes.granola.ai/n1",
                    "path": name,
                },
            }
        )
    )
