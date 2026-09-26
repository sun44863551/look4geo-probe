from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Citation, SourceEvidenceOrigin, SourceRecord, SourceRole

TRACKING_PARAMETERS = {"gclid", "fbclid"}


def _domain_is_excluded(domain: str, excluded_domains: frozenset[str]) -> bool:
    lowered = domain.casefold().rstrip(".")
    return any(
        lowered == excluded.casefold().rstrip(".")
        or lowered.endswith("." + excluded.casefold().rstrip("."))
        for excluded in excluded_domains
        if excluded.strip(".")
    )


def normalize_source_url(
    url: str,
    *,
    redirect_params: tuple[str, ...] = ("url", "target", "dest", "destination"),
    excluded_domains: frozenset[str] = frozenset(),
) -> str | None:
    try:
        parsed = urlsplit(url.strip())
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None

        redirect_names = {name.casefold() for name in redirect_params}
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        for name, value in query_pairs:
            if name.casefold() in redirect_names:
                destination = urlsplit(value.strip())
                if destination.scheme.casefold() in {"http", "https"} and destination.hostname:
                    return normalize_source_url(
                        value,
                        redirect_params=(),
                        excluded_domains=excluded_domains,
                    )

        domain = parsed.hostname.casefold().rstrip(".")
        if _domain_is_excluded(domain, excluded_domains):
            return None

        if ":" in domain and not domain.startswith("["):
            host = f"[{domain}]"
        else:
            host = domain
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"

        retained_query = urlencode(
            [
                (name, value)
                for name, value in query_pairs
                if not name.casefold().startswith("utm_")
                and name.casefold() not in TRACKING_PARAMETERS
            ],
            doseq=True,
        )
        return urlunsplit((parsed.scheme.casefold(), host, parsed.path, retained_query, ""))
    except (TypeError, ValueError):
        return None


def merge_sources(records: list[SourceRecord]) -> list[SourceRecord]:
    merged: list[SourceRecord] = []
    positions: dict[str, int] = {}
    for record in records:
        normalized_url = normalize_source_url(record.url)
        if normalized_url is None:
            continue
        normalized = record.model_copy(
            update={
                "url": normalized_url,
                "domain": urlsplit(normalized_url).hostname or "",
            }
        )
        position = positions.get(normalized_url)
        if position is None:
            positions[normalized_url] = len(merged)
            merged.append(normalized)
            continue

        existing = merged[position]
        cited = (
            existing.source_role == SourceRole.CITED
            or normalized.source_role == SourceRole.CITED
        )
        answer_dom = (
            existing.evidence_origin == SourceEvidenceOrigin.ANSWER_DOM
            or normalized.evidence_origin == SourceEvidenceOrigin.ANSWER_DOM
        )
        merged[position] = existing.model_copy(
            update={
                "title": existing.title or normalized.title,
                "snippet": existing.snippet or normalized.snippet,
                "source_role": SourceRole.CITED if cited else SourceRole.SURFACED,
                "evidence_origin": (
                    SourceEvidenceOrigin.ANSWER_DOM
                    if cited and answer_dom
                    else existing.evidence_origin
                ),
                "linked_in_answer": existing.linked_in_answer
                or normalized.linked_in_answer,
            }
        )
    return merged


def citations_from_sources(records: list[SourceRecord]) -> list[Citation]:
    return [
        Citation(url=record.url, label=record.title)
        for record in records
        if record.source_role == SourceRole.CITED
    ]
