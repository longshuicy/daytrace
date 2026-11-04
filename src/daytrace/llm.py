"""Any OpenAI-compatible chat completions endpoint, and the week and month summary prompts."""

import json
import os
import re
import urllib.request

from .adapters import NotConfigured

_PROMPT = """\
You help one person look back on their {period} and name what each project actually was.
{when}

Input JSON:
- "stats": totals for the {period}. "private" is a total of private time; never guess what it was.
- "days": one per day with data. The counts are already done: project "switches", "meeting_runs", "focus"
  stretches and what ended them, "outside_9_5_min", and the morning's longest meeting-free stretch. Mention a
  pattern only when it changes what a project was. Don't restate the hours.
- "entries": one per project (or for meetings, or a kind of activity without a project): the days it appeared,
  minutes per activity, counts, a few titles, sites, git branches, and "channels" (Slack channels whose names
  mapped to that project).
- "meetings": notes, each with "project" when the meeting mapped to one (null when it didn't), "topics", and
  action items. "mine" is only what is still open: a later commit, pull request, wiki edit or document on that
  project already shared a word with anything left out. "waiting" is someone else's; "unassigned" has no owner.
  People are "[person]"; the reader is "me".

Voice: second person ("You ..."), plain and specific. Don't judge: no "good", "productive", "too much" or
"you should". Name the thread and let the reader decide.{advice}

For each project that took real time or had notes, say what the thread was and whether the {period} moved it.
Titles, branches, channels and the meetings with that project are one thread. A commit, pull request or wiki
edit means it moved; otherwise it was only talked about. Mention an open item only when it is still listed
under "mine". A meeting with no project stays with the meeting.

Rules:
- Use only what is in the data. Never invent tasks, people, decisions or outcomes.
- Days missing from "days" have no data: the computer was off or nothing was tracked. Don't call them days off.
- Minutes are spans of activity, not effort.

Reply with only a JSON object, no prose around it:
{{"narrative": "<2 to 4 sentences naming the one or two threads most worth noticing>",
 "observations": [{{"kind": "thread",
                    "text": "<one or two sentences: what this project was, and whether it moved>",
                    "evidence": "<the titles, channel, meeting or open item this comes from>"}}],
{next_week} "themes": ["<2 to 4 short noun phrases>"]}}
One observation per such project, the ones that moved or are still open first, at most 8.{limit}
"""

WEEK_PROMPT = _PROMPT.format(
    period="week", when="They read this on Monday morning, before planning the week ahead.",
    advice=' The one place you suggest anything is "next_week".',
    next_week=' "next_week": [{"project": "<the project>",\n'
              '                "todos": [{"text": "<a todo to paste into a list>", "why": "<the open item it '
              'comes from>"}]}],\n',
    limit=' "next_week" is only those "mine" items still in the data, phrased so they can be pasted, grouped by '
          'project, at most 8. Leave it empty when nothing is still open.')
MONTH_PROMPT = _PROMPT.format(period="month", when="They read this at the end of the month.", advice="",
                              next_week="", limit="")


def prompt(kind: str) -> str:
    return WEEK_PROMPT if kind == "week" else MONTH_PROMPT


# "thread" is what a new reply uses. The others remain so a reply that still uses them is kept as sent.
OBSERVATION_KINDS = ("thread", "open_loop", "recurring", "time_vs_weight", "pattern")


def _env(name: str) -> str | None:
    # LUMEN_* is the older name of the same settings.
    return os.environ.get(f"LLM_{name}") or os.environ.get(f"LUMEN_{name}")


def model_name() -> str:
    return _env("MODEL") or "(unset)"


def settings() -> tuple[str, str, str]:
    base, model, key = _env("BASE_URL"), _env("MODEL"), _env("API_KEY")
    if not (base and model and key):
        raise NotConfigured("set LLM_BASE_URL, LLM_MODEL and LLM_API_KEY in ~/.daytrace/.env")
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
        raise ValueError("LLM reply had no JSON object")
    data = json.loads(text[start:end + 1])
    observations = [
        {"kind": o.get("kind") if o.get("kind") in OBSERVATION_KINDS else "pattern",
         "text": str(o.get("text", "")).strip(), "evidence": str(o.get("evidence", "")).strip()}
        for o in data.get("observations") or [] if isinstance(o, dict) and str(o.get("text", "")).strip()][:8]
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
