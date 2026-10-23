"""Locations of daytrace's files and loading of ~/.daytrace/.env."""

import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("DAYTRACE_HOME", "~/.daytrace")).expanduser()


def db_path() -> Path:
    return home() / "daytrace.db"


STORAGE_LIMIT = 150 * 1024 * 1024  # past this the app asks you to back up and start over


def backup_dir() -> Path:
    return home() / "backups"


def rules_path() -> Path:
    return home() / "rules.toml"


def env_path() -> Path:
    return home() / ".env"


def log_dir() -> Path:
    return home() / "logs"


ENV_TEMPLATE = """\
# daytrace secrets (chmod 600). Real environment variables override these. See docs/setup.md.

# LLM for week/month summaries: any OpenAI-compatible endpoint (Lumen, OpenRouter, ...).
# LUMEN_BASE_URL=          # ends in /v1
# LUMEN_MODEL=
# LUMEN_API_KEY=
# GITHUB_TOKEN=            # optional: defaults to `gh auth token`
# CONFLUENCE_URL=https://wiki.ncsa.illinois.edu
# CONFLUENCE_TOKEN=        # wiki > profile > Personal Access Tokens
# MS_CLIENT_ID=            # Azure app registration, for `daytrace connect outlook`
# MS_TENANT_ID=
"""


def load_env(path: Path | None = None) -> None:
    """Load KEY=VALUE lines into os.environ without overriding existing variables."""
    path = path or env_path()
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if value and value[0] in "\"'":
            value = value[1:].split(value[0], 1)[0]
        else:
            value = value.split(" #", 1)[0].strip()
        os.environ.setdefault(key, value)
