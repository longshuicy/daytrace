# Setup

macOS only. Every source is optional; skip what you don't use.

## 1. Install

Prerequisites: [uv](https://docs.astral.sh/uv/getting-started/installation/) (brings Python 3.12+), git.

```bash
uv tool install --editable .     # puts `daytrace` on your PATH
daytrace init                    # creates ~/.daytrace (rules.toml, .env); tracking starts today
daytrace sync                    # collect now
daytrace today                   # look at today
daytrace agent install           # sync every 10 min in the background (launchd)
```

`daytrace status` shows each source and its last sync. Secrets go in `~/.daytrace/.env`; what's private and how
apps/folders map to categories and projects goes in `~/.daytrace/rules.toml` (commented; edit freely).

## 2. Sources

| Source | Setup |
|---|---|
| Apps & windows | `brew install --cask activitywatch` and keep [ActivityWatch](https://activitywatch.net/) running |
| Local git | none; scans `[git] roots` in `rules.toml` for your commits (`git config --global user.email`) |
| Claude Code | none; reads session timing from `~/.claude/projects` |
| Cursor | none; reads agent chat timing from Cursor's local state database (message times only, never text) |
| GitHub | `gh auth login` ([GitHub CLI](https://cli.github.com/)), or `GITHUB_TOKEN` in `.env` |
| Confluence (Data Center) | `CONFLUENCE_URL` (your wiki's address) and `CONFLUENCE_TOKEN` (wiki → profile → **Personal Access Tokens**) in `.env` |
| Outlook | see below |
| Granola notes | `daytrace connect granola` (below) |

### Outlook calendar and mail

daytrace signs in to Microsoft Graph with an app registration you create once (read-only, public client, no
secret).

1. In the [Azure portal](https://portal.azure.com) → **App registrations** → **New registration**: single tenant,
   no redirect URI.
2. **Authentication** → *Allow public client flows* → **Yes**.
3. **API permissions** → Microsoft Graph → *Delegated*: `Calendars.Read`, `Mail.ReadBasic`, `offline_access`,
   `User.Read`. If your tenant requires admin consent, ask your IT admin.
4. Copy the *Application (client) ID* and *Directory (tenant) ID* into `.env`:

   ```
   MS_CLIENT_ID=...
   MS_TENANT_ID=...
   ```

5. `daytrace connect outlook` and follow the code prompt. The sign-in is kept in
   `~/.daytrace/msgraph-token.json` (chmod 600); `daytrace disconnect outlook` removes it.

Only subjects, times, attendee counts and the other side's email *domains* are kept, never bodies or addresses.
A meeting marked private or confidential in Outlook is stored as bare time.

### Granola meeting notes

`daytrace connect granola` signs in to [Granola's MCP server](https://docs.granola.ai/help-center/sharing/integrations/mcp)
with a device code (works on the free plan). Each sync copies new or changed notes, meaning your own notes plus
Granola's summary, into `~/.daytrace/notes/granola/` as Markdown, one file per meeting. The first sync goes back
30 days, which is as far as the free plan reaches. After that, syncing keeps every note even once Granola stops serving it.

The day view attaches each note to the Outlook meeting running when the note was started (or starting within
15 minutes); hover a meeting to see it, click to read the saved copy (with a link to Granola). Notes that
match no meeting show as dots.
Note text is never stored in the database. A week or month summary request gets only each note's topics and
action items (yours, ones you're waiting on, unassigned), with people's names replaced by `[person]`; you see
them in the review and can click any out. The sign-in is in
`~/.daytrace/granola-token.json`; `daytrace disconnect granola` removes it.

## 3. Week/month summaries (optional)

Any OpenAI-compatible chat endpoint works. Set three values in `.env`:

| | [OpenRouter](https://openrouter.ai/docs/quickstart) | Your own gateway |
|---|---|---|
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` | its URL, ending in `/v1` |
| `LLM_MODEL` | any id from [openrouter.ai/models](https://openrouter.ai/models) | a model it serves |
| `LLM_API_KEY` | an [OpenRouter key](https://openrouter.ai/settings/keys) | your key |

The older names `LUMEN_BASE_URL`, `LUMEN_MODEL` and `LUMEN_API_KEY` still work.

A summary is meant for looking back: a short narrative, 3 to 6 observations with the evidence behind them
(open loops from meeting notes, recurring topics, time vs. weight, and patterns like project switching, meeting
runs, focus stretches and time outside 9–5), and for a week, a draft todo list for the next one, grouped by
project, to copy into your own list. daytrace counts the patterns itself; the model only interprets them.

Then, in the app's Week or Month view (or the CLI):

```bash
daytrace summarize week          # build the request; nothing is sent
daytrace summarize week --send   # send it, after reviewing it in ~/.daytrace/audit/ or the app
```

## 4. Desktop app (optional)

Needs [Rust](https://rustup.rs/) and Node.js. See the [Tauri prerequisites](https://v2.tauri.app/start/prerequisites/).

```bash
cd app && npm install
./rebuild.sh                     # build, install to /Applications, launch
```

The app calls `~/.local/bin/daytrace`; set `DAYTRACE_BIN` if yours is elsewhere.

## Uninstall

```bash
daytrace agent uninstall
uv tool uninstall daytrace
rm -rf ~/.daytrace
```
