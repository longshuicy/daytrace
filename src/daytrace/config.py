"""Locations of daytrace's files and loading of ~/.daytrace/.env."""

import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("DAYTRACE_HOME", "~/.daytrace")).expanduser()


def db_path() -> Path:
    return home() / "daytrace.db"


def rules_path() -> Path:
    return home() / "rules.toml"


def env_path() -> Path:
    return home() / ".env"


def log_dir() -> Path:
    return home() / "logs"


ENV_TEMPLATE = """\
# daytrace secrets. Keep this file private (chmod 600); it is never committed.
# Real environment variables take precedence over values here.

# LUMEN_BASE_URL=
# LUMEN_MODEL=
# LUMEN_API_KEY=
# GITHUB_TOKEN=            # optional: defaults to your `gh auth token`
# CONFLUENCE_URL=https://wiki.ncsa.illinois.edu
# CONFLUENCE_TOKEN=         # wiki > profile > Personal Access Tokens (read-only use)
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
