# daytrace

A life, in little traces. A local-first "black box recorder" that reconstructs what your workday actually looked
like from the digital traces you already leave behind.

See [docs/design.md](docs/design.md) for the design.

## Status

Milestones 1–2: the core collector. Local sources only, no sign-ins yet.

| Source | What it records |
|---|---|
| ActivityWatch | focused app + window title, away-from-keyboard spans (needs ActivityWatch running) |
| Local git | your own commits in every repo under `~/Documents/Projects` |
| Claude Code | active session spans per project (never prompt content) |

## Quick start

```bash
uv tool install --editable .     # puts `daytrace` on your PATH
daytrace init                    # creates ~/.daytrace, tracking starts today
daytrace sync                    # collect now
daytrace today                   # what today looked like
daytrace agent install           # sync every 10 minutes in the background (launchd)
```

Optional, for app and window activity:

```bash
brew install --cask activitywatch
```

Other commands: `daytrace status`, `daytrace today --date 2026-10-22`, `daytrace forget START END`,
`daytrace agent uninstall`.

## Privacy

- Everything stays in `~/.daytrace/`. Nothing is sent anywhere yet.
- `~/.daytrace/rules.toml` decides what counts as work. Personal matches win; anything unclear is `unknown`.
- Non-work events keep only a category and timing: no titles, URLs or project names.
- Private/incognito windows are never stored.
- Raw ActivityWatch events are deleted from ActivityWatch 7 days after daytrace ingests them.

## Development

```bash
uv sync
uv run pytest
```
