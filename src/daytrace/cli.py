"""daytrace command line."""

import argparse
import json
import sys
from datetime import date, datetime, time

from . import config, dayjson, granola, launchd, msgraph, period, summarize
from .adapters import NotConfigured, all_adapters
from .adapters import granola as granola_notes
from .models import UTC, iso, parse_iso
from .report import day_bounds, render
from .rules import DEFAULT_RULES, Rules
from .store import Store
from .sync import NotInitialized, day_zero, sync


def _open() -> tuple[Store, Rules]:
    config.load_env()
    return Store(config.db_path()), Rules.load(config.rules_path())


def _statuses(store: Store, rules: Rules) -> dict[str, str]:
    """Last sync status per current source; sources that were removed from daytrace are left out."""
    names = {a.name for a in all_adapters(rules)}
    rows = store.conn.execute("SELECT key, value FROM meta WHERE key LIKE 'status:%'")
    return {k.removeprefix("status:"): v for k, v in rows if k.removeprefix("status:") in names}


def cmd_init(args) -> None:
    home = config.home()
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    if not config.rules_path().exists():
        config.rules_path().write_text(DEFAULT_RULES)
        print(f"wrote {config.rules_path()}")
    if not config.env_path().exists():
        config.env_path().write_text(config.ENV_TEMPLATE)
        print(f"wrote {config.env_path()}")
    config.env_path().chmod(0o600)
    store = Store(config.db_path())
    if store.get_meta("day_zero"):
        print(f"already tracking since {store.get_meta('day_zero')}")
    else:
        # Start of today, local time: today's traces count, nothing earlier does.
        zero = datetime.combine(date.today(), time.min).astimezone()
        store.set_meta("day_zero", iso(zero))
        store.set_meta("schema_version", "1")
        print(f"tracking starts {zero:%Y-%m-%d %H:%M %Z}")


def cmd_sync(args) -> None:
    store, rules = _open()
    adapters = [a for a in all_adapters(rules) if not args.source or a.name in args.source]
    results = sync(store, rules, adapters, datetime.now(UTC))
    for r in results:
        if not r.configured:
            if not args.quiet:
                print(f"{r.source:<17} not configured ({r.error})")
        elif r.ok and not args.quiet:
            extra = f", {r.dropped} dropped" if r.dropped else ""
            extra += f", {r.purged} raw purged" if r.purged else ""
            extra += f", {r.removed} removed" if r.removed else ""
            print(f"{r.source:<17} {r.stored} events{extra}")
        elif not r.ok:
            print(f"{r.source:<17} FAILED {r.error}", file=sys.stderr)


def cmd_today(args) -> None:
    store, rules = _open()
    day = date.fromisoformat(args.date) if args.date else date.today()
    start, end = day_bounds(day)
    rows = store.events_between(iso(start), iso(end))
    print(render(day, rows, _statuses(store, rules), day_zero(store)))


def cmd_day(args) -> None:
    store, rules = _open()
    day = date.fromisoformat(args.date) if args.date else date.today()
    start, end = day_bounds(day)
    statuses = {name: json.loads(value) for name, value in _statuses(store, rules).items()}
    rows, now = store.events_between(iso(start), iso(end)), datetime.now(UTC)
    notes = granola_notes.notes_between(granola_notes.notes_dir(), start, end)
    data = dayjson.build(day, rows, statuses, store.counts_by_source(), day_zero(store), now, notes)
    data["storage"] = {"bytes": config.db_path().stat().st_size, "limit": config.STORAGE_LIMIT}
    print(dayjson.dumps(data))


def cmd_note(args) -> None:
    print(json.dumps(granola_notes.read_note(granola_notes.notes_dir(), args.id)))


def cmd_period(args) -> None:
    store, _ = _open()
    anchor = date.fromisoformat(args.date) if args.date else date.today()
    first, last = (period.week_of if args.kind == "week" else period.month_of)(anchor)
    print(dayjson.dumps(period.build(store, first, last, day_zero(store), datetime.now(UTC), args.kind)))


def cmd_summarize(args) -> None:
    store, _ = _open()
    day_zero(store)
    anchor = date.fromisoformat(args.date) if args.date else date.today()
    first, last = (period.week_of if args.kind == "week" else period.month_of)(anchor)
    if args.send:
        exclude = json.loads(args.exclude) if args.exclude else []
        r = summarize.send_period(store, args.kind, first, datetime.now(UTC), exclude)
    else:
        r = summarize.prepare_period(store, args.kind, first, last, datetime.now(UTC))
    if r.mode == "dry-run":
        print(f"Request built: {r.entries} entries. Nothing was sent.")
        print(f"Review the exact request in {r.audit} (or in the app),")
        date_arg = f" --date {args.date}" if args.date else ""
        print(f"then send it with `daytrace summarize {args.kind}{date_arg} --send`.")
    else:
        print(r.narrative)
        for o in r.observations:
            print(f"\n- {o['text']}\n  ({o['evidence']})")
        for group in r.next_week:
            print(f"\n{group['project']}")
            for t in group["todos"]:
                print(f"  - {t['text']}")
        if r.themes:
            print("\nThemes: " + " · ".join(r.themes))
        print(f"\n(request and reply saved in {r.audit.parent})")


def cmd_status(args) -> None:
    store, rules = _open()
    print(f"home              {config.home()}")
    print(f"tracking          since {day_zero(store).astimezone():%Y-%m-%d %H:%M}")
    counts = store.counts_by_source()
    for adapter in all_adapters(rules):
        raw = store.get_meta(f"status:{adapter.name}")
        st = json.loads(raw) if raw else None
        last = f"last sync {parse_iso(st['at']).astimezone():%m-%d %H:%M}" if st else "never synced"
        if st and not st.get("configured", True):
            last, state = "not configured", ""
        else:
            state = "" if not st or st["ok"] else f"  FAILED {st['error']}"
        print(f"{adapter.name:<17} {counts.get(adapter.name, 0):>6} events  {last}{state}")
    print(f"agent             {'installed' if launchd.plist_path().exists() else 'not installed'}")


def cmd_forget(args) -> None:
    store, _ = _open()
    start = day_bounds(date.fromisoformat(args.start))[0]
    end = day_bounds(date.fromisoformat(args.end))[1]
    print(f"deleted {store.delete_between(iso(start), iso(end))} events")


def cmd_backup(args) -> None:
    store, _ = _open()
    folder = config.backup_dir()
    folder.mkdir(mode=0o700, exist_ok=True)
    dest = folder / f"daytrace-{datetime.now():%Y-%m-%d-%H%M%S}.db"
    store.backup(dest)
    dest.chmod(0o600)
    print(f"backed up to {dest} ({dest.stat().st_size / 1024 / 1024:.1f} MB)")
    if args.start_over:
        zero = datetime.combine(date.today(), time.min).astimezone()
        deleted = store.start_over(iso(zero))
        print(f"started over: deleted {deleted} events before today; now "
              f"{config.db_path().stat().st_size / 1024 / 1024:.1f} MB")


def cmd_connect(args) -> None:
    config.load_env()
    if args.service == "granola":
        print("Sign in with your Granola account. daytrace copies your meeting notes into ~/.daytrace/notes/;")
        print("a week or month summary request gets only their topics and action items, names taken out.\n")
        granola.connect()
        print("\nConnected. Run `daytrace sync --source granola` to copy your notes.")
        return
    print("Sign in with your Illinois Microsoft account. daytrace asks only for read-only access to your")
    print("calendar and email metadata (subjects, times, sender domains), never message bodies.\n")
    msgraph.connect()
    print("\nConnected. Run `daytrace sync` to collect your calendar and email.")


def cmd_disconnect(args) -> None:
    done = granola.disconnect() if args.service == "granola" else msgraph.disconnect()
    print("signed out" if done else "wasn't signed in")


def cmd_agent(args) -> None:
    if args.action == "install":
        print(f"installed {launchd.install(args.interval * 60)}")
    else:
        launchd.uninstall()
        print("uninstalled")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="daytrace", description="Where did today go?")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create ~/.daytrace and start tracking today").set_defaults(func=cmd_init)

    p = sub.add_parser("sync", help="collect new traces from every source")
    p.add_argument("--source", action="append", help="only this source (repeatable)")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("today", help="show a day")
    p.add_argument("--date", help="YYYY-MM-DD, default today")
    p.set_defaults(func=cmd_today)

    p = sub.add_parser("day", help="a day as JSON, for the desktop app")
    p.add_argument("--date", help="YYYY-MM-DD, default today")
    p.add_argument("--json", action="store_true", help="accepted for clarity; output is always JSON")
    p.set_defaults(func=cmd_day)

    p = sub.add_parser("note", help="a saved Granola note as JSON, for the desktop app")
    p.add_argument("id", help="the note's Granola id")
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("period", help="a week (Sunday to Saturday) or month as JSON, for the desktop app")
    p.add_argument("kind", choices=["week", "month"])
    p.add_argument("--date", help="any day in it, YYYY-MM-DD, default today")
    p.set_defaults(func=cmd_period)

    p = sub.add_parser("summarize", help="describe a week or month with Lumen: build, review, then --send")
    p.add_argument("kind", choices=["week", "month"])
    p.add_argument("--date", help="any day in it, YYYY-MM-DD, default today")
    p.add_argument("--send", action="store_true", help="send the request built earlier, after you reviewed it")
    p.add_argument("--exclude", help="with --send: JSON list of what the review took out")
    p.set_defaults(func=cmd_summarize)

    sub.add_parser("status", help="sources, counts, last sync").set_defaults(func=cmd_status)

    p = sub.add_parser("forget", help="delete everything stored for a date range")
    p.add_argument("start", help="YYYY-MM-DD")
    p.add_argument("end", help="YYYY-MM-DD, inclusive")
    p.set_defaults(func=cmd_forget)

    p = sub.add_parser("backup", help=f"copy the database to {config.backup_dir()}")
    p.add_argument("--start-over", action="store_true", help="then keep only today and track from today")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("connect", help="sign in to a service that needs it")
    p.add_argument("service", choices=["outlook", "granola"])
    p.set_defaults(func=cmd_connect)

    p = sub.add_parser("disconnect", help="forget a service's sign-in")
    p.add_argument("service", choices=["outlook", "granola"])
    p.set_defaults(func=cmd_disconnect)

    p = sub.add_parser("agent", help="install or remove the background sync agent")
    p.add_argument("action", choices=["install", "uninstall"])
    p.add_argument("--interval", type=int, default=10, help="minutes between syncs")
    p.set_defaults(func=cmd_agent)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (NotInitialized, NotConfigured, RuntimeError, ValueError) as exc:
        sys.exit(f"daytrace: {exc}")


if __name__ == "__main__":
    main()
