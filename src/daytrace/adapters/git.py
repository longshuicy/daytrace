"""Local git: your own commits in every repository under the configured roots."""

import hashlib
import subprocess
from datetime import datetime, timedelta
from typing import Iterable

from ..models import RawEvent, iso, parse_iso
from ..rules import Rules

RECORD, FIELD = "\x1e", "\x1f"
LOG_FORMAT = f"{RECORD}%H{FIELD}%aI{FIELD}%ae{FIELD}%s"


class Git:
    name = "git"
    overlap = timedelta(days=3)  # commits made offline or rebased can show up late

    def __init__(self, rules: Rules):
        self.rules = rules

    def authors(self) -> set[str]:
        if self.rules.git_authors:
            return set(self.rules.git_authors)
        out = subprocess.run(["git", "config", "--global", "user.email"],
                             capture_output=True, text=True)
        email = out.stdout.strip().lower()
        return {email} if email else set()

    def fetch(self, since: datetime, until: datetime) -> Iterable[RawEvent]:
        authors = self.authors()
        for repo in self.rules.repos().values():
            out = subprocess.run(
                ["git", "-C", str(repo), "log", "--all", "--no-merges", "--shortstat",
                 f"--since={iso(since)}", f"--until={iso(until)}", f"--format={LOG_FORMAT}"],
                capture_output=True, text=True,
            )
            if out.returncode != 0:
                continue
            for commit in parse_log(out.stdout):
                if authors and commit["email"] not in authors:
                    continue
                if not since <= commit["at"] < until:
                    continue
                yield RawEvent(
                    "git", commit_key(commit), "point", "recorded", commit["at"],
                    category="commit", title=commit["subject"], path=str(repo),
                    attrs={"repo": repo.name, "sha": commit["sha"], **commit["stat"]},
                )


def commit_key(commit: dict) -> str:
    """Rebased, cherry-picked or cloned copies keep author, author date and subject, not the SHA."""
    ident = f"{commit['email']}|{iso(commit['at'])}|{commit['subject']}"
    return hashlib.sha1(ident.encode()).hexdigest()


def parse_log(text: str) -> list[dict]:
    commits = []
    for record in text.split(RECORD)[1:]:
        header, _, rest = record.partition("\n")
        sha, at, email, subject = header.split(FIELD, 3)
        commits.append({
            "sha": sha, "at": parse_iso(at), "email": email.lower(), "subject": subject,
            "stat": parse_shortstat(rest),
        })
    return commits


def parse_shortstat(text: str) -> dict:
    stat = {"files": 0, "insertions": 0, "deletions": 0}
    for part in text.strip().split(","):
        words = part.split()
        if len(words) >= 2 and words[0].isdigit():
            n = int(words[0])
            if words[1].startswith("file"):
                stat["files"] = n
            elif words[1].startswith("insertion"):
                stat["insertions"] = n
            elif words[1].startswith("deletion"):
                stat["deletions"] = n
    return stat
