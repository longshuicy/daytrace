"""Outlook via Microsoft Graph: calendar events (taken as attended) and email metadata (never bodies)."""

import re
from datetime import datetime, timedelta
from typing import Iterable

from .. import msgraph
from ..models import UTC, RawEvent, iso, parse_iso

UTC_PREFER = {"Prefer": 'outlook.timezone="UTC"'}
LOOKAHEAD = timedelta(days=2)  # so today's later meetings show up before they happen


class OutlookCalendar:
    name = "outlook_calendar"
    overlap = timedelta(days=2)  # meetings get moved and cancelled after the fact

    def window(self, since: datetime, until: datetime) -> tuple[datetime, datetime]:
        """The range fetch() covers completely: stored events in it that weren't returned are gone."""
        return since, until + LOOKAHEAD

    def fetch(self, since: datetime, until: datetime) -> Iterable[RawEvent]:
        start, end = self.window(since, until)
        events = msgraph.get_all("/me/calendarView", {
            "startDateTime": iso(start), "endDateTime": iso(end), "$top": "100",
            "$select": "id,iCalUId,subject,start,end,isAllDay,isCancelled,showAs,responseStatus,"
                       "organizer,attendees,isOnlineMeeting,onlineMeetingProvider,webLink",
        }, headers=UTC_PREFER)
        for e in events:
            raw = calendar_raw(e)
            if raw:
                yield raw


def calendar_raw(e: dict) -> RawEvent | None:
    response = (e.get("responseStatus") or {}).get("response", "none")
    if e.get("isCancelled") or e.get("isAllDay") or response == "declined" or e.get("showAs") == "free":
        return None
    start, end = _when(e["start"]), _when(e["end"])
    organizer = ((e.get("organizer") or {}).get("emailAddress") or {}).get("address", "")
    subject = e.get("subject") or "(no subject)"
    people = [(p.get("emailAddress") or {}).get("name") for p in [e.get("organizer") or {}, *(e.get("attendees") or [])]]
    attrs = {
        "attendees": len(e.get("attendees") or []),
        "response": response,
        "organizer_domain": organizer.partition("@")[2].lower() or None,
        "online": e.get("onlineMeetingProvider") if e.get("isOnlineMeeting") else None,
    }
    redacted = without_names(subject, people)
    if redacted != subject:
        attrs["lumen_title"] = redacted  # what Lumen sees; the names themselves are never stored
    return RawEvent(
        "outlook_calendar", e["id"], "plan", "scheduled", start, end,
        category="meeting", title=subject, url=e.get("webLink"), attrs=attrs,
    )


def without_names(text: str, names: list[str | None]) -> str:
    """text with each name, and each word of three or more letters in it, replaced by "[person]"."""
    words = {w for n in names if n for w in [n, *re.split(r"[\s,()]+", n)] if len(w.strip(".")) >= 3}
    for w in sorted(words, key=len, reverse=True):  # full names before their parts
        text = re.sub(rf"(?<![\w@]){re.escape(w)}(?![\w@])", "[person]", text, flags=re.I)
    return re.sub(r"\[person\](?:[\s,]+\[person\])+", "[person]", text)


def _when(value: dict) -> datetime:
    # With the UTC preference header Graph returns naive "2026-10-22T14:00:00.0000000".
    dt = datetime.fromisoformat(value["dateTime"][:19])
    return dt.replace(tzinfo=UTC)


class OutlookMail:
    name = "outlook_mail"
    overlap = timedelta(hours=1)

    def fetch(self, since: datetime, until: datetime) -> Iterable[RawEvent]:
        sent = msgraph.get_all("/me/mailFolders/sentitems/messages", {
            "$filter": f"sentDateTime ge {iso(since)}", "$orderby": "sentDateTime desc", "$top": "100",
            "$select": "id,subject,sentDateTime,toRecipients,ccRecipients,conversationId,webLink",
        })
        received = msgraph.get_all("/me/mailFolders/inbox/messages", {
            "$filter": f"receivedDateTime ge {iso(since)}", "$orderby": "receivedDateTime desc", "$top": "100",
            "$select": "id,subject,receivedDateTime,from,conversationId",
        })
        for m in sent:
            raw = mail_raw(m, sent=True)
            if raw.start_at < until:
                yield raw
        for m in received:
            raw = mail_raw(m, sent=False)
            if raw.start_at < until:
                yield raw


def _domains(recipients: list[dict]) -> list[str]:
    found = {(r.get("emailAddress") or {}).get("address", "").partition("@")[2].lower() for r in recipients}
    return sorted(d for d in found if d)


def mail_raw(m: dict, sent: bool) -> RawEvent:
    """Subject, time and the other side's domains only: no addresses, names or bodies."""
    if sent:
        recipients = (m.get("toRecipients") or []) + (m.get("ccRecipients") or [])
        attrs = {"direction": "sent", "recipients": len(recipients), "domains": _domains(recipients)}
        at, category = parse_iso(m["sentDateTime"]), "email"
    else:
        attrs = {"direction": "received", "domains": _domains([m.get("from") or {}])}
        at, category = parse_iso(m["receivedDateTime"]), "inbox"
    attrs["thread"] = m.get("conversationId")
    return RawEvent(
        "outlook_mail", m["id"], "point", "recorded", at, category=category,
        title=m.get("subject") or "(no subject)", url=m.get("webLink") if sent else None,
        attrs=attrs,
    )
