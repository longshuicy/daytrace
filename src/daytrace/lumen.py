"""Lumen (OpenAI-compatible chat completions) and the week and month summary prompts."""

import json
import os
import re
import urllib.request

from .adapters import NotConfigured

_PROMPT = """\
You help one person look back on their {period}: where their time went and what is pulling at their attention.
{when}

Input JSON:
- "stats": totals for the {period}. "private" is a total of private time; never guess what it was.
- "days": one per day with data: active and meeting minutes, first and last activity (local time), "weekend",
  "projects" touched, project "switches", "meetings", "meeting_runs" (back-to-back meetings), "morning_meetings"
  and "morning_free_max_min" (the longest meeting-free stretch between 9:00 and 12:00), "focus" (stretches of 25
  minutes or more without interruption: when, how long, on what, and what ended it: a meeting, comms (mail or
  chat), a break, or stopped), and "outside_9_5_min" (active minutes before 9:00 or after 17:00).
- "entries": one per project (or for meetings, or a kind of activity without a project): the days it appeared,
  minutes per activity, counts (commits, PRs, wiki edits...), a few titles, sites and git branches.
- "meetings": meetings with notes: day, time, calendar title (null when it wasn't on the calendar), "topics", and
  action items: "mine" (I own them), "waiting" (someone else owns them) and "unassigned". People are "[person]";
  the reader is "me".

Voice: second person ("You ..."), plain and specific. Make each observation sharp and back it with the numbers or
the meeting it comes from. Don't judge: no "good", "productive", "too much" or "you should". Name the tension and
let the reader decide.{advice}

Look for these, and include only what the data shows:
1. Where time went: the projects that took the most time, and meetings vs hands-on work for each (match meeting
   titles and topics to projects by name).
2. Open loops: "mine" items with no later trace on that project (commit, PR, wiki edit, doc or window title), and
   "waiting" items that don't come up again.
3. Recurring topics: the same topic in two or more meetings without being settled.
4. Time vs. weight: projects with lots of meeting time and little hands-on work, or the reverse.
5. Patterns: scattered days (many switches), meeting shape (back-to-back runs, broken mornings), when focus
   happened and what ended it, starts before 8:30, finishes after 18:00, weekend work.

Rules:
- Use only what is in the data. Never invent tasks, people, decisions or outcomes.
- Days missing from "days" have no data: the computer was off or nothing was tracked. Don't call them days off.
- Minutes are spans of activity, not effort.

Reply with only a JSON object, no prose around it:
{{"narrative": "<3 to 5 sentences: the shape of the {period} and the one or two tensions most worth noticing>",
 "observations": [{{"kind": "open_loop" | "recurring" | "time_vs_weight" | "pattern",
                   "text": "<one sentence>", "evidence": "<the numbers, days or meetings behind it>"}}],
{next_week} "themes": ["<2 to 4 short noun phrases>"]}}
Give 3 to 6 observations, the most notable first.{limit}
"""

WEEK_PROMPT = _PROMPT.format(
    period="week", when="They read this on Monday morning, before planning the week ahead.",
    advice=' The one place you suggest anything is "next_week".',
    next_week=' "next_week": [{"project": "<project, or a short name for the area>",\n'
              '                "todos": [{"text": "<a todo to paste into a list>", "why": "<the open loop, meeting '
              'or pattern it comes from>"}]}],\n',
    limit=' "next_week" drafts next week\'s todo list from the open loops and "waiting" items worth chasing, grouped '
          'by project, at most 8 todos in all.')
MONTH_PROMPT = _PROMPT.format(period="month", when="They read this at the end of the month.", advice="",
                              next_week="", limit="")


def prompt(kind: str) -> str:
    return WEEK_PROMPT if kind == "week" else MONTH_PROMPT


OBSERVATION_KINDS = ("open_loop", "recurring", "time_vs_weight", "pattern")


def settings() -> tuple[str, str, str]:
    base, model, key = (os.environ.get(k) for k in ("LUMEN_BASE_URL", "LUMEN_MODEL", "LUMEN_API_KEY"))
    if not (base and model and key):
        raise NotConfigured("set LUMEN_BASE_URL, LUMEN_MODEL and LUMEN_API_KEY in ~/.daytrace/.env")
    return base.rstrip("/"), model, key


def request_body(payload: dict, model: str, system: str = WEEK_PROMPT) -> dict:
    return {
        "model": model,
        "temperature": 0.3,
        "max_tokens": 2000,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }


def complete(body: dict, timeout: float = 120.0) -> str:
    base, _, key = settings()
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"].get("content") or ""


def parse(text: str) -> dict:
    """Pull the JSON object out of a reply that may include <think> blocks or code fences."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Lumen reply had no JSON object")
    data = json.loads(text[start:end + 1])
    observations = [
        {"kind": o.get("kind") if o.get("kind") in OBSERVATION_KINDS else "pattern",
         "text": str(o.get("text", "")).strip(), "evidence": str(o.get("evidence", "")).strip()}
        for o in data.get("observations") or [] if isinstance(o, dict) and str(o.get("text", "")).strip()][:6]
    next_week = []
    for group in data.get("next_week") or []:
        if not isinstance(group, dict):
            continue
        todos = [{"text": str(t.get("text", "")).strip(), "why": str(t.get("why", "")).strip()}
                 for t in group.get("todos") or [] if isinstance(t, dict) and str(t.get("text", "")).strip()]
        if todos:
            next_week.append({"project": str(group.get("project", "")).strip() or "Other", "todos": todos})
    return {
        "narrative": str(data.get("narrative", "")).strip(),
        "themes": [str(t).strip() for t in data.get("themes", []) if str(t).strip()][:4],
        "observations": observations,
        "next_week": next_week,
    }
