"""daytrace command line."""

import argparse
import json
import sys
from datetime import date, datetime, time

from . import config, launchd
from .adapters import all_adapters
from .models import UTC, iso, parse_iso
from .report import day_bounds, render
from .rules import DEFAULT_RULES, Rules
from .store import Store
from .sync import NotInitialized, day_zero, sync


def _open() -> tuple[Store, Rules]:
    config.load_env()
    return Store(config.db_path()), Rules.load(config.rules_path())


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
        if r.ok and not args.quiet:
            extra = f", {r.dropped} dropped" if r.dropped else ""
            extra += f", {r.purged} raw purged" if r.purged else ""
            print(f"{r.source:<14} {r.stored} events{extra}")
        elif not r.ok:
            print(f"{r.source:<14} FAILED {r.error}", file=sys.stderr)


def cmd_today(args) -> None:
    store, _ = _open()
    day = date.fromisoformat(args.date) if args.date else date.today()
    start, end = day_bounds(day)
    rows = store.events_between(iso(start), iso(end))
    statuses = {
        r[0].removeprefix("status:"): r[1]
        for r in store.conn.execute("SELECT key, value FROM meta WHERE key LIKE 'status:%'")
    }
    print(render(day, rows, statuses, day_zero(store)))


def cmd_status(args) -> None:
    store, rules = _open()
    print(f"home         {config.home()}")
    print(f"tracking     since {day_zero(store).astimezone():%Y-%m-%d %H:%M}")
    counts = store.counts_by_source()
    for adapter in all_adapters(rules):
        raw = store.get_meta(f"status:{adapter.name}")
        st = json.loads(raw) if raw else None
        last = f"last sync {parse_iso(st['at']).astimezone():%m-%d %H:%M}" if st else "never synced"
        state = "" if not st or st["ok"] else f"  FAILED {st['error']}"
        print(f"{adapter.name:<12} {counts.get(adapter.name, 0):>6} events  {last}{state}")
    print(f"agent        {'installed' if launchd.plist_path().exists() else 'not installed'}")


def cmd_forget(args) -> None:
    store, _ = _open()
    start = day_bounds(date.fromisoformat(args.start))[0]
    end = day_bounds(date.fromisoformat(args.end))[1]
    print(f"deleted {store.delete_between(iso(start), iso(end))} events")


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

    sub.add_parser("status", help="sources, counts, last sync").set_defaults(func=cmd_status)

    p = sub.add_parser("forget", help="delete everything stored for a date range")
    p.add_argument("start", help="YYYY-MM-DD")
    p.add_argument("end", help="YYYY-MM-DD, inclusive")
    p.set_defaults(func=cmd_forget)

    p = sub.add_parser("agent", help="install or remove the background sync agent")
    p.add_argument("action", choices=["install", "uninstall"])
    p.add_argument("--interval", type=int, default=10, help="minutes between syncs")
    p.set_defaults(func=cmd_agent)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except NotInitialized as exc:
        sys.exit(f"daytrace: {exc}")


if __name__ == "__main__":
    main()
