"""URL purpose, distinct from where a link appeared or publisher credibility."""
from enum import StrEnum
from urllib.parse import parse_qs, urlsplit


class SourceKind(StrEnum):
    WEBSITE = "website"
    SHOPPING_ENTRY = "shopping_entry"
    SEARCH_ENTRY = "search_entry"
    HOSTED_UNRESOLVED = "hosted_unresolved"


# Restrict navigation rules to known hosts; never suffix-match arbitrary domains.
GOOGLE_SEARCH_HOSTS = frozenset({
    "google.com", "google.co.uk", "google.co.jp", "google.co.kr",
    "google.de", "google.fr", "google.ca", "google.com.au", "google.cn",
    "google.com.hk", "google.co.in", "google.co.nz", "google.com.sg",
})


def classify_source_url(url: str) -> SourceKind:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    query = parse_qs(parsed.query, keep_blank_values=True)
    if host == "shopping.google.com":
        return SourceKind.SHOPPING_ENTRY
    if host in GOOGLE_SEARCH_HOSTS:
        if parsed.path in {"/search", "/shopping", "/shopping/"}:
            if query.get("ibp") == ["oshop"] or "prds" in query or parsed.path.startswith("/shopping"):
                return SourceKind.SHOPPING_ENTRY
            return SourceKind.SEARCH_ENTRY
    if host == "googleusercontent.com" or host.endswith(".googleusercontent.com"):
        return SourceKind.HOSTED_UNRESOLVED
    return SourceKind.WEBSITE
