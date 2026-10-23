"""Lumen (OpenAI-compatible chat completions) and the day-summary prompt."""

import json
import os
import re
import urllib.request

from .adapters import NotConfigured

SYSTEM_PROMPT = """\
You turn structured traces of one person's workday into a short, honest diary entry.

You get JSON with day "stats" and "episodes": chunks of work with local times, a project, minutes per
activity, counts (commits, PRs, emails...), a few titles and git branch names.

Write in the second person ("You ..."), plain and warm. No productivity judgement, no advice, no scores.
Rules:
- Use only what is in the data. Never invent tasks, people, decisions or outcomes beyond the titles.
- Meetings come from a calendar: they were scheduled. Say "you had X on your calendar", not what happened in it.
- Minutes are spans of activity, not effort. Don't call anything hard, productive, or a struggle.
- "ai" means working with an AI coding assistant; "commit"/"pr"/"push" are git and GitHub actions.
- "non_work" blocks are private. At most say there was personal time; never guess what it was.

Reply with only a JSON object, no prose around it:
{"episodes": [{"id": "<episode id>", "label": "<one sentence, at most 14 words>"}],
 "narrative": "<3 to 5 sentences, the shape of the day in time order>",
 "themes": ["<2 to 4 short noun phrases>"]}
"""


def settings() -> tuple[str, str, str]:
    base, model, key = (os.environ.get(k) for k in ("LUMEN_BASE_URL", "LUMEN_MODEL", "LUMEN_API_KEY"))
    if not (base and model and key):
        raise NotConfigured("set LUMEN_BASE_URL, LUMEN_MODEL and LUMEN_API_KEY in ~/.daytrace/.env")
    return base.rstrip("/"), model, key


def request_body(payload: dict, model: str) -> dict:
    return {
        "model": model,
        "temperature": 0.3,
        "max_tokens": 2000,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
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
        "labels": {e["id"]: str(e["label"]).strip() for e in data.get("episodes", []) if e.get("id") and e.get("label")},
        "narrative": str(data.get("narrative", "")).strip(),
        "themes": [str(t).strip() for t in data.get("themes", []) if str(t).strip()][:4],
    }
