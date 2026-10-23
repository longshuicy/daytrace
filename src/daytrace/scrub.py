"""Remove secrets from text and query strings from URLs."""

import re
from urllib.parse import urlsplit, urlunsplit

REDACTED = "[redacted]"

_SECRETS = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\s*[=:]\s*\S+"),
    re.compile(r"\b[0-9a-fA-F]{40,}\b"),
    re.compile(r"\b[A-Za-z0-9+/]{48,}={0,2}"),
]
_URL = re.compile(r"https?://\S+")
# Browser titles: "host/path?query - Google Chrome - Profile name". Drop the query and the browser/profile tail.
_BARE_URL_QUERY = re.compile(r"\b((?:[\w-]+\.)+[a-z]{2,}(?:/[^\s?#]*)?)[?#]\S*", re.I)
_BROWSER_SUFFIX = re.compile(
    r"\s+[-–—]\s+(Google Chrome|Chromium|Safari|Firefox|Arc|Microsoft Edge|Brave)\b.*$", re.I)


def strip_url(url: str | None) -> str | None:
    if not url:
        return url
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def tidy_title(text: str | None) -> str | None:
    if not text:
        return text
    return _BARE_URL_QUERY.sub(r"\1", _BROWSER_SUFFIX.sub("", text)).strip()


def scrub(text: str | None) -> str | None:
    if not text:
        return text
    text = tidy_title(_URL.sub(lambda m: strip_url(m.group(0)), text))
    for pattern in _SECRETS:
        text = pattern.sub(REDACTED, text)
    return text
