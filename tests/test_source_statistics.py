"""Shopping links must never inflate content citation or publisher statistics."""
import pytest
import json
from typer.testing import CliRunner

from look4geo_probe.models import JobStatus, PlatformAttempt, SourceRecord
from look4geo_probe.sources import citations_from_sources, merge_sources


def record(url, role="cited", origin="answer_dom"):
    return SourceRecord(url=url, domain="untrusted.example", source_role=role,
                        evidence_origin=origin, linked_in_answer=origin == "answer_dom")


@pytest.mark.parametrize("url,kind", [
    ("https://google.com/search?q=oil&ibp=oshop&prds=productid:1", "shopping_entry"),
    ("https://www.google.co.uk/search?prds=productid:1", "shopping_entry"),
    ("https://google.com/search?q=oil", "search_entry"),
    ("https://googleusercontent.com/opaque", "hosted_unresolved"),
])
def test_noncontent_entries_are_preserved_but_not_citations(url, kind):
    s = record(url)
    assert citations_from_sources([s]) == []
    assert s.model_dump(mode="json").get("source_kind") == kind
    assert s.source_role == "surfaced"
    assert s.linked_in_answer is True


@pytest.mark.parametrize("url", [
    "https://developers.google.com/search/docs", "https://support.google.com/webmasters/answer/1",
    "https://example.com/search?q=oil", "https://google.com.evil.example/search?ibp=oshop",
])
def test_publisher_pages_are_not_rejected_as_google_navigation(url):
    s = record(url)
    assert s.model_dump(mode="json").get("source_kind") == "website"
    assert [c.url for c in citations_from_sources([s])] == [url]


def test_merge_cannot_promote_shopping_entry_to_citation():
    merged = merge_sources([
        record("https://google.com/search?ibp=oshop", "surfaced", "source_panel"),
        record("https://google.com/search?ibp=oshop#answer"),
    ])
    assert len(merged) == 1
    assert merged[0].source_role == "surfaced"
    assert citations_from_sources(merged) == []


def test_attempt_reports_all_visible_sources_separately_and_reads_legacy_records():
    shopping = record("https://google.com/search?ibp=oshop").model_dump(mode="json")
    shopping.pop("source_kind", None)
    shopping["source_role"] = "cited"  # Historical schema-3 record.
    attempt = PlatformAttempt.model_validate({
        "platform": "gemini", "adapter": "browser_skill", "status": "succeeded",
        "sources": [shopping, record("https://originaturestore.com/article", "surfaced", "source_panel").model_dump(),
                    record("https://example.com/article").model_dump()],
        "citations": [{"url": shopping["url"]}, {"url": "https://example.com/article"}],
    })
    data = attempt.model_dump(mode="json")
    assert [c["url"] for c in data["citations"]] == ["https://example.com/article"]
    assert data.get("source_statistics") == {
        "total": 3, "website_cited": 1, "website_surfaced": 1,
        "shopping_entries": 1, "search_entries": 0, "hosted_unresolved": 0,
    }
    assert len(data["sources"]) == 3
    assert PlatformAttempt.model_validate(data).model_dump(mode="json") == data


def test_failed_or_empty_capture_is_not_silently_a_measured_zero():
    attempt = PlatformAttempt(platform="gemini", adapter="browser_skill", status=JobStatus.FAILED,
                              source_capture_status="failed")
    assert attempt.model_dump(mode="json").get("source_statistics") is None


def test_source_report_cli_keeps_panel_domains_and_never_overwrites_inputs(tmp_path):
    from look4geo_probe.cli import app

    raw = tmp_path / "raw"
    raw.mkdir()
    payload = {"job_id": "saved-job", "attempts": [{
        "platform": "gemini", "adapter": "browser_skill", "status": "succeeded",
        "source_capture_status": "captured", "sources": [
            record("https://google.com/search?ibp=oshop").model_dump(mode="json"),
            record("https://www.example.com/article", "surfaced", "source_panel").model_dump(mode="json"),
        ],
    }]}
    source = raw / "Q001_gemini.json"
    source.write_text(json.dumps(payload))
    original = source.read_bytes()
    output = tmp_path / "report.json"
    args = ["source-report", str(raw), "--output", str(output)]
    runner = CliRunner()
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    report = json.loads(output.read_text())
    assert report["source_statistics"]["website_surfaced"] == 1
    assert report["source_statistics"]["shopping_entries"] == 1
    assert report["website_domains"] == [{"domain": "example.com", "records": 1, "questions": 1,
                                          "cited": 0, "surfaced": 1}]
    assert source.read_bytes() == original
    original_report = output.read_bytes()
    assert runner.invoke(app, args).exit_code != 0
    assert output.read_bytes() == original_report


def test_source_report_retains_failed_and_no_exposed_question_counts(tmp_path):
    from look4geo_probe.source_reporting import build_source_report

    for qid, status, capture in [("Q1", "failed", "failed"), ("Q2", "succeeded", "none_exposed")]:
        (tmp_path / f"{qid}_gemini.json").write_text(json.dumps({"job_id": qid, "attempts": [{
            "platform": "gemini", "adapter": "browser_skill", "status": status,
            "source_capture_status": capture,
        }]}))
    report = build_source_report(tmp_path, "gemini")
    assert report["question_count"] == 2
    assert report["answer_status_counts"] == {"failed": 1, "succeeded": 1}
    assert report["questions_with_unknown_source_counts"] == 2
    assert all(q["source_statistics"] is None for q in report["questions"])
