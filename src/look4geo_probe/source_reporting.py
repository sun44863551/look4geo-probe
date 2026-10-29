"""Offline source statistics from immutable saved probe results, not citation CSVs."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from .models import PlatformAttempt, SCHEMA_VERSION
from .source_classification import SourceKind
from .sources import merge_sources, summarize_sources


def build_source_report(raw_directory: Path, platform: str) -> dict:
    if not platform.isascii() or not platform.isalpha():
        raise ValueError("platform must be an ASCII platform name")
    paths = sorted(raw_directory.glob(f"*_{platform}.json"))
    if not paths:
        raise ValueError(f"No saved *_{platform}.json records found")
    totals = summarize_sources([])
    domains = defaultdict(lambda: dict(records=0, questions=set(), cited=0, surfaced=0))
    questions, records = [], []
    statuses, captures = Counter(), Counter()
    coverage = defaultdict(set)
    for path in paths:
        raw_bytes = path.read_bytes()
        payload = json.loads(raw_bytes)
        attempts = [a for a in payload.get("attempts", []) if a.get("platform") == platform]
        if not attempts:
            raise ValueError(f"No {platform} attempt in {path.name}")
        attempt = PlatformAttempt.model_validate(attempts[-1])
        qid = path.name.removesuffix(f"_{platform}.json")
        sources = merge_sources(attempt.sources)
        statistics = attempt.source_statistics
        if statistics is not None:
            for key, value in statistics.items():
                totals[key] += value
                if value:
                    coverage[key].add(qid)
        statuses[attempt.status.value] += 1
        captures[attempt.source_capture_status.value] += 1
        questions.append(dict(question_id=qid, job_id=payload.get("job_id"),
                              answer_status=attempt.status.value,
                              source_capture_status=attempt.source_capture_status.value,
                              source_capture_diagnostic=attempt.source_capture_diagnostic,
                              source_statistics=statistics, raw_path=str(path.resolve()),
                              raw_sha256=hashlib.sha256(raw_bytes).hexdigest()))
        for source in sources:
            records.append(dict(question_id=qid, **source.model_dump(mode="json")))
            if source.source_kind != SourceKind.WEBSITE:
                continue
            host = (urlsplit(source.url).hostname or "").lower().rstrip(".")
            if host.startswith("www."):
                host = host[4:]
            d = domains[host]
            d["records"] += 1
            d["questions"].add(qid)
            d[source.source_role.value] += 1
    return dict(
        schema_version=SCHEMA_VERSION, platform=platform, question_count=len(questions),
        source_statistics=totals, answer_status_counts=dict(statuses),
        source_capture_status_counts=dict(captures),
        questions_with_unknown_source_counts=sum(q["source_statistics"] is None for q in questions),
        question_coverage={key: len(coverage[key]) for key in totals},
        website_domains=[dict(domain=host, **{**d, "questions": len(d["questions"])})
                         for host, d in sorted(domains.items(), key=lambda kv: (-kv[1]["records"], kv[0]))],
        questions=questions, sources=records,
        notes=["Counts cover saved evidence, not all platform research.",
               "surfaced websites are not promoted to cited; shopping/search/opaque hosted links are excluded from publisher counts.",
               "Unknown/failed captures are reported separately; source totals may include partial saved evidence.",
               "Question coverage overlaps across categories; hostname grouping removes www. only."],
    )
