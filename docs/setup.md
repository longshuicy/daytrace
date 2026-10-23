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
| GitHub | `gh auth login` ([GitHub CLI](https://cli.github.com/)), or `GITHUB_TOKEN` in `.env` |
| Confluence | wiki → profile → **Personal Access Tokens** → `CONFLUENCE_TOKEN` (and `CONFLUENCE_URL` if not the NCSA wiki) |
| Outlook | see below |

### Outlook calendar and mail

daytrace signs in to Microsoft Graph with its own read-only app registration (no secret).

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

## 3. Week/month summaries (optional)

Any OpenAI-compatible chat endpoint works. Set three values in `.env`:

| | Lumen | [OpenRouter](https://openrouter.ai/docs/quickstart) |
|---|---|---|
| `LUMEN_BASE_URL` | your Lumen URL ending in `/v1` | `https://openrouter.ai/api/v1` |
| `LUMEN_MODEL` | a model Lumen serves | any id from [openrouter.ai/models](https://openrouter.ai/models) |
| `LUMEN_API_KEY` | your Lumen key | an [OpenRouter key](https://openrouter.ai/settings/keys) |

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
