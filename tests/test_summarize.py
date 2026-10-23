import json
import stat
from datetime import date, datetime, timedelta

import pytest

from daytrace import egress, episodes, lumen, summarize
from daytrace.models import UTC, RawEvent, iso
from daytrace.report import day_bounds
from daytrace.store import Store
from daytrace.sync import sync

DAY = date(2026, 10, 22)
ZERO = datetime(2026, 10, 22, 5, 0, tzinfo=UTC)


class Fake:
    name, overlap = "fake", timedelta(0)

    def __init__(self, events):
        self.events = events

    def fetch(self, since, until):
        return self.events


def at(h, m=0):
    return datetime.combine(DAY, datetime.min.time()).astimezone() + timedelta(hours=h, minutes=m)


@pytest.fixture
def day_store(tmp_path, rules, projects, monkeypatch):
    monkeypatch.setenv("DAYTRACE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LUMEN_MODEL", "test-model")
    s = Store(tmp_path / "t.db")
    s.set_meta("day_zero", iso(ZERO))
    pdg = str(projects / "PDG")
    ev = lambda sid, start, minutes=None, **kw: RawEvent(
        "fake", sid, "span" if minutes else "point", "recorded", start,
        start + timedelta(minutes=minutes) if minutes else None, **kw)
    events = [
        ev("a1", at(9), 30, category="ai", path=pdg, attrs={"branch": "feature/water"}),
        ev("a2", at(9, 35), 20, category="ai", path=pdg),             # 5-min gap: same episode
        ev("a3", at(11), 15, category="ai", path=pdg),                # 45-min gap: new episode
        ev("c1", at(9, 50), category="commit", title="Fix it for bob@x.org", path=pdg),
        ev("m1", at(10), 60, category="meeting", title="PDG weekly", always_work=True),
        ev("in", at(10, 5), category="inbox", title="Newsletter", always_work=True),
        ev("p1", at(12), 40, app="Spotify", title="Very private song"),
        ev("f1", at(20), 30, category="ai", path=pdg),                # after "now": not counted
    ]
    events[4].kind = "plan"
    sync(s, rules, [Fake(events)], at(13))
    return s


def test_episodes_split_on_gaps_and_skip_mail_and_future(day_store):
    start, end = day_bounds(DAY)
    eps = episodes.build(day_store.events_between(iso(start), iso(end)), start, end, at(13))
    kinds = [(e["kind"], e["project"], e["minutes"]) for e in eps]
    assert kinds == [("project", "pdg", 50), ("meeting", None, 60), ("project", "pdg", 15)]
    assert eps[0]["counts"] == {"commit": 1} and eps[0]["branches"] == ["feature/water"]
    assert all("Newsletter" not in e["titles"] for e in eps)


def test_dry_run_sends_nothing_and_audits(day_store, rules, monkeypatch):
    monkeypatch.setattr(lumen, "complete", lambda body: pytest.fail("dry run must not call Lumen"))
    r = summarize.summarize(day_store, rules, DAY, at(13))
    assert r.mode == "dry-run" and r.episodes == 3
    assert stat.S_IMODE(r.audit.stat().st_mode) == 0o600
    payload = json.loads(json.loads(r.audit.read_text())["messages"][1]["content"])
    text = json.dumps(payload)
    assert "Very private song" not in text and "Spotify" not in text and "Newsletter" not in text
    assert "bob@x.org" not in text and "Fix it for [email]" in text
    assert payload["non_work"] == [{"start": "12:00", "end": "12:40", "minutes": 40}]
    assert set(payload) == {"date", "stats", "episodes", "non_work"}
    assert set(payload["episodes"][0]) == set(egress.EPISODE_FIELDS)
    assert day_store.summary(DAY.isoformat())["stats"]["dry_run"] is True


def test_send_stores_labels_and_narrative(day_store, rules, monkeypatch):
    def fake_complete(body):
        ids = [e["id"] for e in json.loads(body["messages"][1]["content"])["episodes"]]
        return ("<think>planning</think>```json\n" + json.dumps({
            "episodes": [{"id": ids[0], "label": "Worked on the water timeseries app."},
                         {"id": "not-a-real-id", "label": "ignored"}],
            "narrative": "You started the morning on PDG.", "themes": ["PDG", "calendar"]}) + "\n```")
    monkeypatch.setattr(lumen, "complete", fake_complete)
    r = summarize.summarize(day_store, rules, DAY, at(13), send=True)
    assert r.mode == "sent" and r.narrative == "You started the morning on PDG."
    labels = [e["label"] for e in day_store.episodes(DAY.isoformat())]
    assert labels == ["Worked on the water timeseries app.", None, None]
    assert sorted(p.name.split("-", 1)[1] for p in r.audit.parent.iterdir()) == ["request.json", "response.json"]


def test_final_check_refuses_leaks():
    with pytest.raises(ValueError):
        egress.check({"episodes": [{"titles": ["mail carol@illinois.edu"]}]})
    with pytest.raises(ValueError):
        egress.check({"episodes": [{"titles": ["key sk-" + "A" * 30]}]})
    egress.check({"episodes": [{"titles": ["Opened PR #2"]}]})


def test_parse_reply_variants():
    assert lumen.parse('{"narrative": "x", "themes": ["a"], "episodes": []}')["narrative"] == "x"
    with pytest.raises(ValueError):
        lumen.parse("Sorry, I can't help with that.")
