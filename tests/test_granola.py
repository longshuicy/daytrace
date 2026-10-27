import json
from datetime import date, datetime, timedelta

from daytrace import dayjson
from daytrace.adapters.granola import (Granola, digest, load_index, match, notes_between, parse_meetings, people,
                                      read_note, save_index)
from daytrace.models import UTC, iso

LISTING = """<meetings_data count="2">
<meeting id="m1" title="Roadmap sync &amp; planning" meeting_at="2026-10-22T10:02:00-05:00" updated_at="u1" url="https://notes.granola.ai/d/m1">
  <known_participants>Pat Example (note creator)</known_participants>
</meeting>
<meeting id="m2" title="Hallway chat" meeting_at="2026-10-22T16:30:00-05:00" updated_at="u2" url="https://notes.granola.ai/d/m2">
</meeting>
</meetings_data>
<pagination has_more="false" />"""


def _detail(mid: str, title: str, at: str, updated: str) -> str:
    return (f'<meeting id="{mid}" title="{title}" meeting_at="{at}" updated_at="{updated}" url="https://x/{mid}">\n'
            f"  <known_participants>Pat Example</known_participants>\n"
            f"  <private_notes>\nmy own line\n</private_notes>\n  <summary>\n# Decisions\n- ship it\n</summary>\n"
            f"</meeting>")


class FakeSession:
    def __init__(self, listing=LISTING):
        self.listing, self.fetched = listing, []

    def call(self, name, args):
        if name == "list_meetings":
            return self.listing
        self.fetched += args["meeting_ids"]
        return "\n".join(_detail(m.id, m.title, m.at, m.updated) for m in self._meetings()
                         if m.id in args["meeting_ids"])

    def _meetings(self):
        class M:
            pass
        out = []
        for m in parse_meetings(self.listing):
            x = M()
            x.id, x.title, x.at, x.updated = m["id"], m["title"], m["meeting_at"], m.get("updated_at", "")
            out.append(x)
        return out


def test_parse_meetings_reads_attributes_and_sections():
    [m] = parse_meetings(_detail("m1", "A &amp; B", "2026-10-22T10:00:00Z", "u1"))
    assert m["title"] == "A & B" and m["meeting_at"] == "2026-10-22T10:00:00Z"
    assert m["sections"] == {"private_notes": "my own line", "summary": "# Decisions\n- ship it"}


def test_sync_writes_markdown_and_refetches_only_changed_notes(tmp_path):
    session = FakeSession()
    adapter = Granola(tmp_path, session=lambda: session)
    now = datetime(2026, 10, 23, tzinfo=UTC)
    assert list(adapter.fetch(now - timedelta(days=1), now)) == []  # notes never become events
    assert session.fetched == ["m1", "m2"]
    index = load_index(tmp_path)
    text = (tmp_path / index["m1"]["path"]).read_text()
    assert "## My notes\n\nmy own line" in text and "## Summary" in text and "Pat Example" not in text
    assert index["m1"]["path"].startswith("2026-10-22-1002-roadmap-sync-planning")

    session.fetched = []
    adapter.fetch(now - timedelta(days=1), now)
    assert session.fetched == []
    session.listing = LISTING.replace('title="Roadmap sync &amp; planning"', 'title="Renamed"').replace("u1", "u1b")
    adapter.fetch(now - timedelta(days=1), now)
    assert session.fetched == ["m1"]
    assert load_index(tmp_path)["m1"]["path"] == index["m1"]["path"]  # same file after a rename
    assert "# Renamed" in (tmp_path / index["m1"]["path"]).read_text()

    note = read_note(tmp_path, "m1")
    assert note["title"] == "Renamed" and note["markdown"].startswith("## My notes\n\nmy own line")


def test_sync_fetches_new_notes_when_the_listing_has_no_updated_at(tmp_path):
    session = FakeSession()
    adapter = Granola(tmp_path, session=lambda: session)
    now = datetime(2026, 10, 23, tzinfo=UTC)
    adapter.fetch(now - timedelta(days=1), now)
    bare = LISTING.replace(' updated_at="u1"', "").replace(' updated_at="u2"', "")
    session.listing = bare.replace("</meetings_data>", '<meeting id="m3" title="New one" '
                                   'meeting_at="2026-10-22T18:00:00-05:00" url="https://x/m3">\n</meeting>\n'
                                   "</meetings_data>")
    session.fetched = []
    adapter.fetch(now - timedelta(days=1), now)
    assert "m3" in session.fetched and "m3" in load_index(tmp_path)


def test_match_picks_the_running_meeting():
    t = lambda h, m=0: datetime(2026, 10, 22, h, m, tzinfo=UTC)
    meetings = [(t(15), t(16)), (t(16), t(17))]
    notes = [{"meeting_at": iso(x)} for x in (t(15, 2), t(15, 50), t(16, 1), t(18), t(14, 50))]
    assert match(notes, meetings) == [0, 0, 1, None, 0]


def test_day_attaches_notes_to_calendar_blocks(tmp_path, rules):
    session = FakeSession()
    Granola(tmp_path, session=lambda: session).fetch(datetime(2026, 10, 21, tzinfo=UTC),
                                                     datetime(2026, 10, 23, tzinfo=UTC))
    day = date(2026, 10, 22)
    start, end = (datetime(2026, 10, 22, 5, tzinfo=UTC), datetime(2026, 10, 23, 5, tzinfo=UTC))
    notes = notes_between(tmp_path, start, end)
    row = {"source": "outlook_calendar", "category": "meeting", "project": None, "sensitivity": "normal",
           "evidence": "scheduled", "kind": "plan", "title": "Roadmap", "url": None, "attrs": "{}",
           "start_at": "2026-10-22T15:00:00Z", "end_at": "2026-10-22T15:30:00Z"}
    data = dayjson.build(day, [row], {}, {}, start, end, notes)
    [block] = data["blocks"]
    assert [n["title"] for n in block["notes"]] == ["Roadmap sync & planning"]
    [loose] = [p for p in data["points"] if p["source"] == "granola"]
    assert loose["title"] == "Hallway chat" and loose["category"] == "notes"
    assert json.dumps(data)  # still plain JSON


NOTE = """---
title: "Roadmap sync with Pat Example"
---

# Roadmap sync with Pat Example

## Summary

# Meeting Context and Attendees

- Pat and Sam joined

# Release Plan for Pat

- Ship on Tuesday

# Next Steps

- **Write up the release notes** (Robin Sample)

  Details nobody sends.
- **Send budget numbers to Robin** (Pat)
- **Book the demo room**
- Open questions:
"""


def _notes_folder(tmp_path):
    (tmp_path / "n.md").write_text(NOTE)
    (tmp_path / "t.md").write_text("---\n---\n\n# Tools\n\n## Summary\n\n# Using snakemake\n\n- fine\n")
    save_index(tmp_path, {
        "n1": {"title": "Roadmap sync with Pat Example", "meeting_at": "2026-10-22T15:00:00Z", "updated_at": "u",
               "url": None, "path": "n.md"},
        "t1": {"title": "Pipelines with Snakemake", "meeting_at": "2026-10-22T16:00:00Z", "updated_at": "u",
               "url": None, "path": "t.md"},
    })
    return tmp_path


def test_people_come_from_owners_and_titles_but_not_tools(tmp_path):
    assert people(_notes_folder(tmp_path)) == {"Robin Sample", "Pat", "Pat Example"}


def test_digest_splits_action_items_and_takes_names_out(tmp_path):
    folder = _notes_folder(tmp_path)
    d = digest(folder, "n1", people(folder), "Robin Sample")
    assert d["topics"] == ["Release Plan for [person]"]  # the attendees heading isn't a topic
    assert d["mine"] == ["Write up the release notes"]
    assert d["waiting"] == ["Send budget numbers to me"]
    assert d["unassigned"] == ["Book the demo room"]  # "Open questions:" only introduces a list
    assert "Pat" not in json.dumps(d) and "Details" not in json.dumps(d)
