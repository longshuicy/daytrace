"""User-editable rules: what stays private, how apps map to categories and folders to projects, retention."""

import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_RULES = """\
# daytrace rules. Everything is recorded as plain activity except what matches [private].

[private]
# Matching activity is stored as a bare "private" time block: no app, title, url or project.
# It never leaves the machine except as a start and end time.
apps = ["Messages", "WhatsApp", "FaceTime", "Photos", "Spotify", "Music", "TV"]
titles = ["(?i)\\\\bbank", "(?i)gmail\\\\.com", "(?i)youtube", "(?i)netflix"]
# Folders whose commits, sessions and windows are private.
paths = []
# Chrome/Edge/Brave add " - Google Chrome - <profile>" to some window titles.
browser_profiles = []
# Windows matching these are dropped entirely, not even as a time block.
drop = ["(?i)private browsing", "(?i)incognito", "(?i)inprivate"]

[paths]
# The top-level folder under a project root names the project. The most specific root wins, so with
# ["~/Documents/Projects", "~/Documents/Projects/personal"] a folder under personal/ is its own project.
projects = ["~/Documents/Projects"]

[titles]
# Web apps recognized from a browser tab title, checked in order: [pattern, category].
categories = [
  ["(?i)- Google (Docs|Sheets|Slides|Drive|Forms)\\\\b", "docs"],
  ["(?i)confluence", "wiki"],
  ["(?i)\\\\boutlook\\\\b", "email"],
  ["(?i)github", "review"],
]

[categories]
# "idle" counts as time away from the keyboard: the lock screen is not activity.
"loginwindow" = "idle"
"Microsoft Outlook" = "email"
"Microsoft Teams" = "chat"
"Slack" = "chat"
"zoom.us" = "meeting"
"Microsoft Word" = "docs"
"Microsoft Excel" = "docs"
"Microsoft PowerPoint" = "docs"
"Preview" = "docs"
"Adobe Acrobat Reader" = "docs"
"Notes" = "docs"
"Code" = "code"
"Cursor" = "code"
"PyCharm" = "code"
"Claude" = "code"
"iTerm2" = "terminal"
"Terminal" = "terminal"
"Ghostty" = "terminal"
"Warp" = "terminal"
"Google Chrome" = "browse"
"Safari" = "browse"
"Firefox" = "browse"
"Arc" = "browse"
"Microsoft Edge" = "browse"

[projects]
# Top-level folder under a project root -> project key. Unlisted folders use their lowercased name.
# GitHub owners with no local clone map the same way.
# "Website-2.0" = "website"

[project_titles]
# Project key -> patterns (case-insensitive) for titles that name it with no folder to go by: meetings,
# chat channels, browser tabs, documents. Checked in order when nothing else names a project.
# "website" = ["web-?site"]

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
_PROFILE = re.compile(r" [-–—] (?:Google Chrome|Chromium|Microsoft Edge|Brave) [-–—] (.+)$")
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
    project_roots: list[Path]
    private_apps: set[str]
    private_titles: list[re.Pattern]
    private_paths: list[Path]
    private_profiles: set[str]
    drop_titles: list[re.Pattern]
    categories: dict[str, str]
    projects: dict[str, str]
    git_roots: list[Path]
    git_max_depth: int
    git_authors: list[str]
    activitywatch_raw_days: int
    title_categories: list[tuple[re.Pattern, str]] = field(default_factory=list)
    project_titles: list[tuple[re.Pattern, str]] = field(default_factory=list)
    _remotes: dict[Path, str | None] = field(default_factory=dict, repr=False)
    _repos: dict[str, Path] | None = field(default=None, repr=False)

    @classmethod
    def from_text(cls, text: str) -> "Rules":
        d = tomllib.loads(text)
        private, paths, titles = d.get("private", {}), d.get("paths", {}), d.get("titles", {})
        git, retention = d.get("git", {}), d.get("retention", {})
        return cls(
            # Most specific root first, so ~/Projects/personal/x is project "x", not "personal".
            project_roots=sorted(_paths(paths.get("projects", [])), key=lambda p: -len(p.parts)),
            private_apps=set(private.get("apps", [])),
            private_titles=_patterns(private.get("titles", [])),
            private_paths=_paths(private.get("paths", [])),
            private_profiles=set(private.get("browser_profiles", [])),
            drop_titles=_patterns(private.get("drop", [])),
            categories=d.get("categories", {}),
            projects=d.get("projects", {}),
            git_roots=_paths(git.get("roots", [])),
            git_max_depth=git.get("max_depth", 3),
            git_authors=[e.lower() for e in git.get("author_emails", [])],
            activitywatch_raw_days=retention.get("activitywatch_raw_days", 7),
            title_categories=[(re.compile(p), c) for p, c in titles.get("categories", [])],
            project_titles=[(re.compile(p, re.I), project)
                            for project, patterns in d.get("project_titles", {}).items() for p in patterns],
        )

    @classmethod
    def load(cls, path: Path) -> "Rules":
        return cls.from_text(path.read_text() if path.exists() else DEFAULT_RULES)

    @staticmethod
    def matches(patterns: list[re.Pattern], text: str | None) -> bool:
        return bool(text) and any(p.search(text) for p in patterns)

    def is_dropped(self, title: str | None) -> bool:
        return self.matches(self.drop_titles, title)

    def is_private_path(self, path: Path) -> bool:
        return _within(path.expanduser().resolve(), self.private_paths) is not None

    def project_for_path(self, path: Path) -> str | None:
        """The project a path belongs to: its top-level folder under the most specific project root."""
        path = path.expanduser().resolve()
        root = _within(path, self.project_roots)
        if root is None:
            return None
        rel = path.relative_to(root).parts
        return self.projects.get(rel[0], rel[0].lower()) if rel else None

    def project_for_title(self, title: str | None) -> str | None:
        """The project a [project_titles] pattern finds in a title, if any."""
        return next((project for pattern, project in self.project_titles if title and pattern.search(title)), None)

    def local_clone(self, owner: str, repo: str) -> Path | None:
        """The local clone of a GitHub repo: matched by origin remote, else by folder name."""
        full = f"{owner}/{repo}".lower()
        local = next((p for p in self.repos().values() if self.remote_of(p) == full), None)
        return local or self.repos().get(repo.lower())

    def project_for_repo(self, owner: str, repo: str) -> str:
        """A GitHub repo's project: from its local clone when there is one, else the owner."""
        local = self.local_clone(owner, repo)
        project = self.project_for_path(local) if local else None
        return project or self.projects.get(owner, owner.lower())

    def remote_of(self, repo: Path) -> str | None:
        """'owner/name' (lowercased) of a local repo's GitHub origin, or None."""
        if repo not in self._remotes:
            out = subprocess.run(["git", "-C", str(repo), "config", "--get", "remote.origin.url"],
                                 capture_output=True, text=True)
            m = _GITHUB_REMOTE.search(out.stdout.strip())
            self._remotes[repo] = f"{m.group(1)}/{m.group(2)}".lower() if m else None
        return self._remotes[repo]

    def category_for(self, app: str | None, title: str | None) -> str:
        """A recognized web app in the title wins over the app's own category (Chrome showing a Google Doc)."""
        if title and self.categories.get(app or "") == "browse":
            for pattern, category in self.title_categories:
                if pattern.search(title):
                    return category
        return self.categories.get(app or "", "other")

    def browser_profile(self, title: str | None) -> str | None:
        m = _PROFILE.search(title or "")
        return m.group(1) if m else None

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
