"""A per-user launchd agent that runs `daytrace sync` every few minutes."""

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

LABEL = "com.daytrace.sync"


def plist_path() -> Path:
    return Path("~/Library/LaunchAgents").expanduser() / f"{LABEL}.plist"


def executable() -> str:
    found = shutil.which("daytrace")
    return found or str(Path(sys.argv[0]).resolve())


def build_plist(interval_seconds: int) -> dict:
    logs = config.log_dir()
    env = {"PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"}
    if "DAYTRACE_HOME" in os.environ:
        env["DAYTRACE_HOME"] = os.environ["DAYTRACE_HOME"]
    return {
        "Label": LABEL,
        "ProgramArguments": [executable(), "sync", "--quiet"],
        "StartInterval": interval_seconds,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "EnvironmentVariables": env,
        "StandardOutPath": str(logs / "sync.log"),
        "StandardErrorPath": str(logs / "sync.err.log"),
    }


def install(interval_seconds: int = 600) -> Path:
    config.log_dir().mkdir(parents=True, exist_ok=True)
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    uninstall(quiet=True)
    with path.open("wb") as f:
        plistlib.dump(build_plist(interval_seconds), f)
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], check=True)
    return path


def uninstall(quiet: bool = False) -> None:
    path = plist_path()
    if path.exists():
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)],
                       capture_output=quiet)
        path.unlink()
