"""Lumen (OpenAI-compatible chat completions) and the day and week/month summary prompts."""

import json
import os
import re
import urllib.request

from .adapters import NotConfigured

SYSTEM_PROMPT = """\
You turn structured traces of one person's workday into a short, honest diary entry.

You get JSON with day "stats" and "entries": one per project (or for meetings, or a kind of activity without a
project), each with its local time ranges, minutes per activity, counts (commits, PRs, emails...), up to three
titles, site names with minutes, and git branch names. "also" counts short bits too small to list.

Write in the second person ("You ..."), plain and warm. No productivity judgement, no advice, no scores.
Rules:
- Use only what is in the data. Never invent tasks, people, decisions or outcomes beyond the titles.
- Meetings come from the calendar; assume they were attended. Name them, but not what was said or decided.
- Minutes are spans of activity, not effort. Don't call anything hard, productive, or a struggle.
- "commit"/"pr"/"push" are git and GitHub actions.
- "private" blocks are private. At most say there was private time; never guess what it was.

Reply with only a JSON object, no prose around it:
{"narrative": "<3 to 5 sentences, the shape of the day in time order>",
 "themes": ["<2 to 4 short noun phrases>"]}
"""


PERIOD_PROMPT = """\
You turn structured traces of one person's week or month into a short, honest look back.

You get JSON with "period", totals in "stats", one line per day with data in "days" (active and meeting minutes,
first and last activity), and "entries": one per project (or for meetings, or a kind of activity without a
project) with the days it appeared, minutes per activity, counts (commits, PRs, emails...), up to three titles,
site names with minutes, and git branch names. "also" counts bits too small to list; "private" is a total.

Write in the second person ("You ..."), plain and warm. No productivity judgement, no advice, no scores.
Rules:
- Use only what is in the data. Never invent tasks, people, decisions or outcomes beyond the titles.
- Say what you worked on, what shipped (PRs, releases, wiki edits) and where the time went across the days.
- Days missing from "days" have no data: the computer was off or nothing was tracked. Don't call them days off.
- Meetings come from the calendar; assume they were attended. Name them, but not what was said or decided.
- Minutes are spans of activity, not effort. Don't call anything hard, productive, or a struggle.
- "private" is private. At most say there was private time; never guess what it was.

Reply with only a JSON object, no prose around it:
{"narrative": "<4 to 6 sentences, the shape of the period>",
 "themes": ["<2 to 4 short noun phrases>"]}
"""


def settings() -> tuple[str, str, str]:
    base, model, key = (os.environ.get(k) for k in ("LUMEN_BASE_URL", "LUMEN_MODEL", "LUMEN_API_KEY"))
    if not (base and model and key):
        raise NotConfigured("set LUMEN_BASE_URL, LUMEN_MODEL and LUMEN_API_KEY in ~/.daytrace/.env")
    return base.rstrip("/"), model, key


def request_body(payload: dict, model: str, system: str = SYSTEM_PROMPT) -> dict:
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
    return {
        "narrative": str(data.get("narrative", "")).strip(),
        "themes": [str(t).strip() for t in data.get("themes", []) if str(t).strip()][:4],
    }
