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
| Outlook calendar | meetings: subject, time, attendee count, your response (meetings are taken as attended) |
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

## Week and month summaries (Lumen)

`daytrace summarize week|month [--date D]` builds the request for the week (Sunday to Saturday) or month around a
date, for Lumen (an OpenAI-compatible endpoint: `LUMEN_BASE_URL` ending in `/v1`, `LUMEN_MODEL`, `LUMEN_API_KEY`
in `.env`) to write a short paragraph and themes. Building sends nothing: the exact request is saved under
`~/.daytrace/audit/week-<sunday>/` or `month-<YYYY-MM>/` and shown in the app for review, where you can leave out
whole entries or single titles, sites, pages or branches. Only then does **Send** (or
`daytrace summarize week|month --send`) send the reviewed request, and only once: writing again needs a new
review. Days are not summarized on their own.

What the request holds: one entry per project (or meetings, or a kind of activity without a project) with the
days it appeared, minutes per activity, counts, up to three scrubbed titles, site names (or the page title when a
page names no site) and branch names; per-day totals; private time as one total. Slack windows go as the channel
or view name only (a DM is just "Direct message"). Outlook and Teams windows go as a folder or view name
("Inbox", "Chat") or just the app name; sent-mail subjects don't go at all (only the count); and meeting titles go
with the invite's attendee and organizer names replaced by "[person]". Every request and reply is saved in the
audit folder.

## Desktop app

`app/` is a Tauri app: a menu-bar icon, a small always-on-top day clock in the bottom-right corner, and a day
view with Day / Week / Month (a timeline you can switch to a day clock or a sortable table, projects, top sites, an
alert when a source fails to sync, and on Week and Month the Lumen summary with its review step). It reads data through
`daytrace day --json`, so it needs the CLI installed (`~/.local/bin/daytrace`, or set `DAYTRACE_BIN`).

```bash
cd app
npm install
npx tauri build          # -> src-tauri/target/release/bundle/macos/daytrace.app
./rebuild.sh             # build, replace /Applications/daytrace.app and relaunch
npx tauri dev            # run with hot reload
```

Drag the floating clock to move it, click it to open the day view. Closing windows hides them; quit from the menu-bar icon.

UI-only preview in a browser, with no Rust build: `daytrace day --json > app/dev/dev-day.json`, then
`daytrace period week > app/dev/dev-week.json` (same for `month`), then
`npm --prefix app run dev` and open http://localhost:1420 (add `?window=pill` for the pill). `app/dev/` is
gitignored.

## Privacy

- Everything stays in `~/.daytrace/`.
- There is no work/personal split. Everything is recorded as plain activity except what matches `[private]` in
  `~/.daytrace/rules.toml` (apps, title patterns, folders, browser profiles).
- Private events keep only their timing: no app, title, URL or project. They leave the machine only as bare
  time ranges.
- Nothing is sent anywhere except read-only requests to the services themselves (GitHub, the wiki), and Lumen
  when you send a week or month request you reviewed.
- Private/incognito windows are never stored.
- Raw ActivityWatch events are deleted from ActivityWatch 7 days after daytrace ingests them.

## Development

```bash
uv sync
uv run pytest
```
