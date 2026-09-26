import pytest

from look4geo_probe.models import (
    SourceEvidenceOrigin,
    SourceRecord,
    SourceRole,
)
from look4geo_probe.sources import (
    citations_from_sources,
    merge_sources,
    normalize_source_url,
)


@pytest.mark.parametrize("url", ["ftp://example.com/file", "javascript:alert(1)", "/relative"])
def test_normalize_source_url_rejects_non_http_urls(url):
    assert normalize_source_url(url) is None


def test_normalize_source_url_canonicalizes_host_fragment_and_tracking_parameters():
    normalized = normalize_source_url(
        "HTTPS://Example.COM/product?id=42&utm_source=ai&gclid=abc&fbclid=def#details"
    )

    assert normalized == "https://example.com/product?id=42"


def test_normalize_source_url_unwraps_one_normal_url_redirect_parameter():
    normalized = normalize_source_url(
        "https://chat.deepseek.com/redirect?url=https%3A%2F%2FExample.com%2Fspec%3Fsku%3D7%26utm_medium%3Dchat",
        excluded_domains=frozenset({"chat.deepseek.com"}),
    )

    assert normalized == "https://example.com/spec?sku=7"


@pytest.mark.parametrize(
    "url",
    [
        "https://chat.deepseek.com/a/chat/s/123",
        "https://assets.chat.deepseek.com/icon.svg",
    ],
)
def test_normalize_source_url_rejects_excluded_domains_and_subdomains(url):
    assert (
        normalize_source_url(url, excluded_domains=frozenset({"chat.deepseek.com"}))
        is None
    )


@pytest.mark.parametrize("url", ["not a url", "https:///missing-host", "http://"])
def test_normalize_source_url_rejects_malformed_urls(url):
    assert normalize_source_url(url) is None


def source(
    url: str,
    *,
    title: str | None = None,
    snippet: str | None = None,
    role: SourceRole = SourceRole.SURFACED,
    origin: SourceEvidenceOrigin = SourceEvidenceOrigin.SOURCE_PANEL,
    linked: bool = False,
) -> SourceRecord:
    return SourceRecord(
        url=url,
        title=title,
        domain="placeholder.invalid",
        snippet=snippet,
        source_role=role,
        evidence_origin=origin,
        linked_in_answer=linked,
    )


def test_merge_sources_deduplicates_in_first_seen_order_and_promotes_cited_evidence():
    merged = merge_sources(
        [
            source(
                "https://Example.com/spec?utm_source=panel",
                snippet="Panel summary",
            ),
            source("https://other.example/article", title="Other"),
            source(
                "https://example.com/spec#answer",
                title="Product spec",
                role=SourceRole.CITED,
                origin=SourceEvidenceOrigin.ANSWER_DOM,
                linked=True,
            ),
        ]
    )

    assert [item.url for item in merged] == [
        "https://example.com/spec",
        "https://other.example/article",
    ]
    assert merged[0].model_dump(mode="json") == {
        "url": "https://example.com/spec",
        "title": "Product spec",
        "domain": "example.com",
        "snippet": "Panel summary",
        "source_role": "cited",
        "evidence_origin": "answer_dom",
        "linked_in_answer": True,
    }


def test_citations_from_sources_keeps_only_cited_sources_with_title_labels():
    citations = citations_from_sources(
        [
            source(
                "https://example.com/cited",
                title="Evidence",
                role=SourceRole.CITED,
                origin=SourceEvidenceOrigin.ANSWER_DOM,
                linked=True,
            ),
            source("https://example.com/surfaced", title="Search card"),
        ]
    )

    assert [citation.model_dump() for citation in citations] == [
        {"url": "https://example.com/cited", "label": "Evidence"}
    ]
