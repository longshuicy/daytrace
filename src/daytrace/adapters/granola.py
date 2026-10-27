"""Granola meeting notes, copied to Markdown files in ~/.daytrace/notes/granola/ through Granola's MCP server.

Notes are files, not events: their text never enters the database. The day view attaches each note to the calendar
meeting it was taken in (see match). A week or month request gets only a digest of each note: its topics and action
items, with people's names taken out (see digest)."""

import html
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

from .. import config, granola
from ..models import RawEvent, iso, parse_iso
from .outlook import without_names

BACKFILL = timedelta(days=30)  # all the free plan can reach
BATCH = 10  # get_meetings takes at most 10 ids
EARLY = timedelta(minutes=15)  # a note started this long before a meeting still belongs to it

_MEETING = re.compile(r"<meeting (.*?)>(.*?)</meeting>", re.S)
_ATTR = re.compile(r'(\w+)="([^"]*)"')
_SECTION = re.compile(r"<(\w+)>\n?(.*?)\n?\s*</\1>", re.S)
_PAGINATION = re.compile(r"<pagination (.*?)/>", re.S)
_CREATOR = re.compile(r"^\s*(.+?) \(note creator\)", re.M)
SECTIONS = {"private_notes": "My notes", "notes": "My notes", "summary": "Summary"}
NEXT_STEPS = re.compile(r"next steps|action items|follow[- ]?ups?|to-?dos?", re.I)
BOILERPLATE = re.compile(r"\b(context|attendees|participants|agenda)\b", re.I)  # headings that aren't topics
_OWNERS = re.compile(r"\s*\(([^()]*)\)\s*$")
_WITH = re.compile(r"\bwith (.+)$", re.I)
_NAME = re.compile(r"^[A-Z][a-z]+(?: [A-Z][a-z]+){0,2}$")  # "Pat", "Robin Sample"; not "NCSA" or "the team"


def notes_dir() -> Path:
    return config.home() / "notes" / "granola"


class Granola:
    name = "granola"
    overlap = timedelta(days=2)  # summaries are generated, and notes edited, after the meeting

    def __init__(self, folder: Path | None = None, session: Callable[[], granola.Session] | None = None):
        self.folder = folder or notes_dir()
        self.session = session or granola.Session

    def fetch(self, since: datetime, until: datetime) -> Iterable[RawEvent]:
        index = load_index(self.folder)  # granola.Session raises NotConfigured before sign-in
        if not index:
            since = min(since, until - BACKFILL)
        s = self.session()
        listed = list_meetings(s, since, until)
        creator = next((m["creator"] for m in listed if m.get("creator")), None)
        if creator:
            (self.folder / "account.json").parent.mkdir(parents=True, exist_ok=True)
            (self.folder / "account.json").write_text(json.dumps({"name": creator}))
        # list_meetings may leave out updated_at; then a note can't be told unchanged and is fetched again.
        changed = [m for m in listed if m["id"] not in index or not m.get("updated_at")
                   or index[m["id"]].get("updated_at") != m["updated_at"]]
        for i in range(0, len(changed), BATCH):
            for m in parse_meetings(s.call("get_meetings", {"meeting_ids": [c["id"] for c in changed[i:i + BATCH]],
                                                            "max_participants": 0})):
                index[m["id"]] = write_note(self.folder, m, index.get(m["id"]))
        save_index(self.folder, index)
        return []


def list_meetings(s: granola.Session, since: datetime, until: datetime) -> list[dict]:
    args = {"time_range": "custom", "from_date": iso(since), "to_date": iso(until), "max_participants": 0}
    meetings, cursor = [], ""
    for _ in range(20):
        text = s.call("list_meetings", {**args, "cursor": cursor})
        meetings += parse_meetings(text)
        page = _PAGINATION.search(text)
        attrs = dict(_ATTR.findall(page.group(1))) if page else {}
        cursor = attrs.get("next_cursor", "")
        if attrs.get("has_more") != "true" or not cursor:
            break
    return meetings


def parse_meetings(text: str) -> list[dict]:
    """Granola's XML-ish tool output: each meeting's attributes plus its body sections (summary, notes)."""
    out = []
    for head, body in _MEETING.findall(text):
        m = {k: html.unescape(v) for k, v in _ATTR.findall(head)}
        m["sections"] = {tag: html.unescape(content.strip()) for tag, content in _SECTION.findall(body)
                         if tag in SECTIONS and content.strip()}
        creator = _CREATOR.search(body)
        if creator:
            m["creator"] = creator.group(1).strip()
        out.append(m)
    return out


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "meeting"


def write_note(folder: Path, m: dict, previous: dict | None) -> dict:
    """Write one note as Markdown, keeping its file name when the title changes. Returns its index entry."""
    at = parse_iso(m["meeting_at"])
    name = previous["path"] if previous else f"{at.astimezone():%Y-%m-%d-%H%M}-{_slug(m.get('title', ''))}.md"
    title = m.get("title") or "(untitled)"
    lines = ["---", f"title: {json.dumps(title)}", f"granola_id: {m['id']}", f"meeting_at: {m['meeting_at']}",
             f"updated_at: {m.get('updated_at', '')}", f"url: {m.get('url', '')}", "---", "", f"# {title}", ""]
    for tag, heading in SECTIONS.items():
        if tag in m["sections"]:
            lines += [f"## {heading}", "", m["sections"][tag], ""]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("\n".join(lines))
    return {"title": title, "meeting_at": iso(at), "updated_at": m.get("updated_at"), "url": m.get("url"),
            "path": name}


def load_index(folder: Path) -> dict:
    path = folder / "index.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save_index(folder: Path, index: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.json").write_text(json.dumps(index, indent=1, sort_keys=True))


def notes_between(folder: Path, start: datetime, end: datetime) -> list[dict]:
    """Index entries for notes started in [start, end), with their file's full path."""
    return sorted(({**n, "id": i, "path": str(folder / n["path"])} for i, n in load_index(folder).items()
                   if start <= parse_iso(n["meeting_at"]) < end), key=lambda n: n["meeting_at"])


def read_note(folder: Path, note_id: str) -> dict:
    """A saved note's index entry and its Markdown body, without the front matter and title line."""
    entry = load_index(folder).get(note_id)
    if not entry:
        raise ValueError(f"no saved note {note_id}")
    text = html.unescape((folder / entry["path"]).read_text())
    body = text.split("\n---\n", 1)[1].lstrip() if text.startswith("---\n") else text
    if body.startswith("# "):
        body = body.split("\n", 1)[1] if "\n" in body else ""
    return {**entry, "id": note_id, "path": str(folder / entry["path"]), "markdown": body.strip()}


def match(notes: list[dict], meetings: list[tuple[datetime, datetime]]) -> list[int | None]:
    """For each note, the index of the meeting it was taken in: the latest-started one running when the note was
    started, else one about to start. None when it fits no meeting."""
    out = []
    for n in notes:
        at = parse_iso(n["meeting_at"])
        running = [(s, i) for i, (s, e) in enumerate(meetings) if s <= at < e]
        soon = [(s, i) for i, (s, e) in enumerate(meetings) if at < s <= at + EARLY]
        out.append(max(running)[1] if running else min(soon)[1] if soon else None)
    return out


def my_name(folder: Path) -> str | None:
    """The note creator's name as Granola has it, saved at sync."""
    path = folder / "account.json"
    return json.loads(path.read_text()).get("name") if path.exists() else None


def _bullets(section: str) -> list[str]:
    """Top-level "- " items of a Markdown section, without the detail lines under them."""
    return [line[2:].strip() for line in section.splitlines() if line.startswith("- ")]


def _owners(item: str) -> tuple[str, list[str]]:
    """'**Do the thing** (Pat, Sam)' -> ('Do the thing', ['Pat', 'Sam'])."""
    m = _OWNERS.search(item)
    owners = [o.strip() for o in re.split(r",|\band\b|&", m.group(1)) if o.strip()] if m else []
    text = item[:m.start()] if m else item
    return text.replace("**", "").strip(), owners


def people(folder: Path) -> set[str]:
    """Everyone the notes name as an action item's owner or in a title ("... with Pat and Sam"), to take out of
    what Lumen sees."""
    owners, titled, text = set(), set(), []
    for note_id, entry in load_index(folder).items():
        m = _WITH.search(entry.get("title") or "")
        if m:
            titled |= {n.strip() for n in re.split(r",|\band\b|&", m.group(1)) if _NAME.match(n.strip())}
        if (folder / entry["path"]).exists():
            text.append(read_note(folder, note_id)["markdown"])  # without the title, which names people too
            for _, who in _sections(text[-1])[1]:
                owners |= {o for o in who if _NAME.match(o)}
    # Titles also say "... with Snakemake": a tool, not a person, shows up lowercase or in a heading somewhere.
    corpus = "\n".join(text)
    headings = "\n".join(line for line in corpus.splitlines() if line.startswith("#"))
    tools = {n for n in titled - owners if re.search(rf"(?<!\w){re.escape(n.lower())}(?!\w)", corpus)
             or re.search(rf"(?<!\w){re.escape(n)}(?!\w)", headings)}
    return owners | (titled - tools)


def _sections(body: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """A note's topics (its top-level headings) and its action items with their owners."""
    topics, items = [], []
    heading, section = None, []
    for line in [*body.splitlines(), "# "]:
        if not re.match(r"^#{1,6} ", line):
            section.append(line)
            continue
        if heading and NEXT_STEPS.search(heading):
            # "Future directions:" introduces a list; it isn't an item itself.
            items += [_owners(item) for item in _bullets("\n".join(section)) if not item.rstrip("*").endswith(":")]
        heading, section = line.lstrip("#").strip(), []
        if heading and line.startswith("# ") and not NEXT_STEPS.search(heading) and not BOILERPLATE.search(heading):
            topics.append(heading)
    return topics, items


def digest(folder: Path, note_id: str, names: set[str], me: str | None) -> dict:
    """What a week or month request gets from one note: its topics (Granola's headings) and its action items, split
    into mine, waiting on someone else, and unassigned. People are "[person]", and I am "me"."""
    mine = {w.lower() for w in (me or "").split() if len(w) >= 3}

    def redact(text: str) -> str:
        for w in sorted({me or "", *mine} - {""}, key=len, reverse=True):
            text = re.sub(rf"(?<!\w){re.escape(w)}(?!\w)", "me", text, flags=re.I)
        return without_names(text, sorted(names))

    topics, items = _sections(read_note(folder, note_id)["markdown"])
    out = {"topics": [redact(t) for t in topics], "mine": [], "waiting": [], "unassigned": []}
    for text, owners in items:
        key = ("mine" if any(w.lower() in mine for o in owners for w in o.split())
               else "waiting" if owners else "unassigned")
        out[key].append(redact(text))
    return out
