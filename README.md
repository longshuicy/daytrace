# daytrace

A life, in little traces. A local-first "black box recorder" that reconstructs what your workday actually looked
like from the digital traces you already leave behind.

See [docs/design.md](docs/design.md) for the design.

## Status

Milestones 1–2 done; milestone 3 (online sources) and 6 (desktop app) in progress.

| Source | What it records |
|---|---|
| ActivityWatch | focused app + window title, away-from-keyboard spans (needs ActivityWatch running) |
| Local git | your own commits in every repo under `~/Documents/Projects` |
| Claude Code | active session spans per project (never prompt content) |
| GitHub | PRs opened/merged, reviews, issues, comments, pushes (uses your `gh` sign-in) |
| Confluence | pages and blog posts you created or edited, your comments (needs a token) |
| Outlook calendar | meetings: subject, time, attendee count, your response (scheduled, not proof you attended) |
| Outlook mail | sent and received: subject, time, the other side's *domains*. Never bodies or addresses |

To connect the NCSA wiki: wiki → your profile → **Personal Access Tokens** → create one, then add it to
`~/.daytrace/.env`:

```
CONFLUENCE_TOKEN=...
```

To connect Outlook: register a single-tenant app in the UIUC Azure portal (public client flows on; delegated
`Calendars.Read`, `Mail.ReadBasic`, `offline_access`, `User.Read`; no secret), put `MS_CLIENT_ID` and
`MS_TENANT_ID` in `~/.daytrace/.env`, then run `daytrace connect outlook` and follow the code prompt.
The sign-in is kept in `~/.daytrace/msgraph-token.json` (chmod 600); `daytrace disconnect outlook` removes it.

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

## Day summaries (Lumen)

`daytrace summarize [--date D] [--send]` groups the day's work into episodes and asks Lumen (an
OpenAI-compatible endpoint: `LUMEN_BASE_URL` ending in `/v1`, `LUMEN_MODEL`, `LUMEN_API_KEY` in `.env`) for
episode labels, a short paragraph and themes. It's a dry run unless you pass `--send` or set
`mode = "send"` under `[lumen]` in `rules.toml`.

What leaves the machine: work episodes (local times, project, minutes per activity, counts, up to six
scrubbed titles, branch names) and day counts. Non-work time leaves only as bare time ranges. Every request
(and reply) is saved under `~/.daytrace/audit/<date>/` before anything is sent.

## Desktop app

`app/` is a Tauri app: a menu-bar icon, a small always-on-top pill with today's strip and one summary line,
and a day view (timeline, day clock, projects, traces, source status). It reads data through
`daytrace day --json`, so it needs the CLI installed (`~/.local/bin/daytrace`, or set `DAYTRACE_BIN`).

```bash
cd app
npm install
npx tauri build          # -> src-tauri/target/release/bundle/macos/daytrace.app
npx tauri dev            # run with hot reload
```

Drag the pill to move it, click it to open the day view. Closing windows hides them; quit from the menu-bar icon.

UI-only preview in a browser, with no Rust build: `daytrace day --json > app/dev/dev-day.json`, then
`npm --prefix app run dev` and open http://localhost:1420 (add `?window=pill` for the pill). `app/dev/` is
gitignored.

## Privacy

- Everything stays in `~/.daytrace/`.
- `~/.daytrace/rules.toml` decides what counts as work. Personal matches win; anything unclear is `unknown`.
  Repos owned by `longshuicy` on GitHub are personal wherever they're cloned.
- Nothing is sent anywhere except read-only requests to the services themselves (GitHub, the wiki).
- Non-work events keep only a category and timing: no titles, URLs or project names.
- Private/incognito windows are never stored.
- Raw ActivityWatch events are deleted from ActivityWatch 7 days after daytrace ingests them.

## Development

```bash
uv sync
uv run pytest
```
