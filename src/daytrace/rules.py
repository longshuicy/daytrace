"""User-editable rules deciding what counts as work, how apps map to categories, and retention."""

import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_RULES = """\
# daytrace rules. Personal matches always win over work matches.
# Anything that matches neither is "unknown": kept local, never sent to an LLM.

[paths]
work = ["~/Documents/Projects"]
personal = ["~/Documents/Projects/personal"]

[apps]
# Apps whose every window counts as work.
work = ["Microsoft Outlook", "Microsoft Teams", "zoom.us", "Microsoft Word",
        "Microsoft Excel", "Microsoft PowerPoint"]
# Apps that are work only when the window title points at a work repo, path or title pattern.
context = ["Code", "Cursor", "PyCharm", "iTerm2", "Terminal", "Ghostty", "Warp",
           "Google Chrome", "Safari", "Firefox", "Arc", "Microsoft Edge", "Claude"]
personal = ["Messages", "WhatsApp", "Spotify", "Music", "Photos", "FaceTime", "TV"]

[titles]
# Windows matching these are dropped entirely, never stored.
drop = ["(?i)private browsing", "(?i)incognito", "(?i)inprivate"]
personal = ["(?i)\\\\bbank", "(?i)gmail\\\\.com", "(?i)youtube", "(?i)netflix"]
work = ["(?i)illinois", "(?i)confluence", "(?i)jira", "(?i)github"]

[categories]
"Microsoft Outlook" = "email"
"Microsoft Teams" = "chat"
"zoom.us" = "meeting"
"Microsoft Word" = "docs"
"Microsoft Excel" = "docs"
"Microsoft PowerPoint" = "docs"
"Code" = "code"
"Cursor" = "code"
"PyCharm" = "code"
"iTerm2" = "terminal"
"Terminal" = "terminal"
"Ghostty" = "terminal"
"Warp" = "terminal"
"Google Chrome" = "browse"
"Safari" = "browse"
"Firefox" = "browse"
"Arc" = "browse"
"Microsoft Edge" = "browse"
"Claude" = "ai"

[projects]
# Top-level folder under a work path -> project key. Unlisted folders use their lowercased name.
"INCORE-2.0" = "incore"

[github]
# Repos owned by these GitHub users or orgs are personal, wherever they're cloned. Everything else is work.
personal_owners = ["longshuicy"]

[git]
roots = ["~/Documents/Projects"]
max_depth = 3
# Empty means: use `git config --global user.email`.
author_emails = []

[retention]
# Raw ActivityWatch events older than this are deleted from ActivityWatch once ingested.
# Set to 0 to keep raw data forever.
activitywatch_raw_days = 7
"""

_SKIP_DIRS = {"node_modules", "venv", ".venv", "__pycache__", "dist", "build", "site-packages"}
_GITHUB_REMOTE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")
_PATH_IN_TITLE = re.compile(r"(~|/Users/[^/\s]+)(/[^\s:—–|]*)?")


def _paths(values: list[str]) -> list[Path]:
    return [Path(v).expanduser().resolve() for v in values]


def _patterns(values: list[str]) -> list[re.Pattern]:
    return [re.compile(v) for v in values]


def _within(path: Path, roots: list[Path]) -> Path | None:
    for root in roots:
        if path == root or path.is_relative_to(root):
            return root
    return None


@dataclass
class Rules:
    work_paths: list[Path]
    personal_paths: list[Path]
    work_apps: set[str]
    context_apps: set[str]
    personal_apps: set[str]
    drop_titles: list[re.Pattern]
    personal_titles: list[re.Pattern]
    work_titles: list[re.Pattern]
    categories: dict[str, str]
    projects: dict[str, str]
    git_roots: list[Path]
    git_max_depth: int
    git_authors: list[str]
    activitywatch_raw_days: int
    personal_owners: set[str] = field(default_factory=set)
    _remotes: dict[Path, str | None] = field(default_factory=dict, repr=False)
    _repos: dict[str, Path] | None = field(default=None, repr=False)

    @classmethod
    def from_text(cls, text: str) -> "Rules":
        d = tomllib.loads(text)
        paths, apps, titles = d.get("paths", {}), d.get("apps", {}), d.get("titles", {})
        git, retention, github = d.get("git", {}), d.get("retention", {}), d.get("github", {})
        return cls(
            work_paths=_paths(paths.get("work", [])),
            personal_paths=_paths(paths.get("personal", [])),
            work_apps=set(apps.get("work", [])),
            context_apps=set(apps.get("context", [])),
            personal_apps=set(apps.get("personal", [])),
            drop_titles=_patterns(titles.get("drop", [])),
            personal_titles=_patterns(titles.get("personal", [])),
            work_titles=_patterns(titles.get("work", [])),
            categories=d.get("categories", {}),
            projects=d.get("projects", {}),
            git_roots=_paths(git.get("roots", [])),
            git_max_depth=git.get("max_depth", 3),
            git_authors=[e.lower() for e in git.get("author_emails", [])],
            activitywatch_raw_days=retention.get("activitywatch_raw_days", 7),
            personal_owners={o.lower() for o in github.get("personal_owners", [])},
        )

    @classmethod
    def load(cls, path: Path) -> "Rules":
        return cls.from_text(path.read_text() if path.exists() else DEFAULT_RULES)

    @staticmethod
    def matches(patterns: list[re.Pattern], text: str | None) -> bool:
        return bool(text) and any(p.search(text) for p in patterns)

    def is_dropped(self, title: str | None) -> bool:
        return self.matches(self.drop_titles, title)

    def classify_path(self, path: Path) -> tuple[str, str | None] | None:
        """('personal', None) or ('work', project) for a path, or None if neither applies."""
        path = path.expanduser().resolve()
        if _within(path, self.personal_paths):
            return ("personal", None)
        root = _within(path, self.work_paths)
        if root is None:
            return None
        if self.owner_of(path, root) in self.personal_owners:
            return ("personal", None)
        rel = path.relative_to(root).parts
        if not rel:
            return ("work", None)
        return ("work", self.projects.get(rel[0], rel[0].lower()))

    def classify_owner(self, owner: str, repo: str) -> tuple[str, str | None]:
        """Classify a GitHub repo by owner; the project comes from its local clone when there is one."""
        if owner.lower() in self.personal_owners:
            return ("personal", None)
        full = f"{owner}/{repo}".lower()
        local = next((p for p in self.repos().values() if self.remote_of(p) == full), None)
        local = local or self.repos().get(repo.lower())
        if local:
            result = self.classify_path(local)
            if result and result[0] == "work":
                return result
        return ("work", self.projects.get(owner, owner.lower()))

    def owner_of(self, path: Path, stop: Path) -> str | None:
        """GitHub owner of the repo containing path, from its origin remote."""
        for candidate in (path, *path.parents):
            if (candidate / ".git").exists():
                remote = self.remote_of(candidate)
                return remote.split("/")[0] if remote else None
            if candidate == stop:
                return None
        return None

    def remote_of(self, repo: Path) -> str | None:
        """'owner/name' (lowercased) of a local repo's GitHub origin, or None."""
        if repo not in self._remotes:
            out = subprocess.run(["git", "-C", str(repo), "config", "--get", "remote.origin.url"],
                                 capture_output=True, text=True)
            m = _GITHUB_REMOTE.search(out.stdout.strip())
            self._remotes[repo] = f"{m.group(1)}/{m.group(2)}".lower() if m else None
        return self._remotes[repo]

    def category_for_app(self, app: str | None) -> str:
        return self.categories.get(app or "", "other")

    def repos(self) -> dict[str, Path]:
        """Git repositories under the git roots, keyed by lowercased folder name."""
        if self._repos is None:
            self._repos = {}
            for root in self.git_roots:
                for repo in _find_repos(root, self.git_max_depth):
                    self._repos.setdefault(repo.name.lower(), repo)
        return self._repos

    def resolve_title(self, title: str) -> Path | None:
        """Find the repo or path a window title refers to, if any."""
        m = _PATH_IN_TITLE.search(title)
        if m:
            return Path(m.group(0).rstrip(".,)")).expanduser()
        lowered = title.lower()
        for name, path in self.repos().items():
            if len(name) >= 3 and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", lowered):
                return path
        return None


def _find_repos(root: Path, max_depth: int) -> list[Path]:
    found = []
    base = len(root.parts)
    for dirpath, dirnames, _ in os.walk(root):
        current = Path(dirpath)
        if (current / ".git").exists():
            found.append(current)
            dirnames[:] = []
            continue
        if len(current.parts) - base >= max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS]
    return found
