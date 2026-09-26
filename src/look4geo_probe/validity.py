from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

CONTAMINATION_RE = re.compile(
    r"Look4GEO|GEO\s*客户系统|主线进度|USER_APPROVAL_FOR_HANYU|Hanyu\s*S0|Hanyu\s*S1\s*批准",
    re.I,
)
PREAMBLE_RE = re.compile(
    r"^(?:我会|我先|我将|I(?:['’]?ll| will)\b|Let me\b)", re.I
)
STUB_RE = re.compile(
    r"^(正在运行代码解释器|正在思考|正在搜索网络|搜索中|加载中|跳过)(?:…|\.\.\.)?(?:\s*跳过)?$",
    re.I,
)
TERMINAL_FAILURES = {"login_required", "rate_limited"}


def _comparable(value: str) -> str:
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE).casefold()


def answer_is_valid_measurement(
    prompt: str, answer: str, *, min_answer_chars: int = 300
) -> bool:
    answer = answer.strip()
    if not answer or STUB_RE.fullmatch(answer):
        return False
    if _comparable(answer) == _comparable(prompt):
        return False
    if CONTAMINATION_RE.search(answer):
        return False
    if PREAMBLE_RE.match(answer) and len(answer) < 300:
        return False
    if len(answer) < min_answer_chars:
        return False
    return True


def result_is_clean_success(
    payload: dict, *, expected_repeats: int, min_answer_chars: int = 300
) -> bool:
    if payload.get("status") != "succeeded":
        return False
    attempts = payload.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != expected_repeats:
        return False
    prompt = str(payload.get("prompt") or "")
    return all(
        attempt.get("status") == "succeeded"
        and answer_is_valid_measurement(
            prompt,
            str(attempt.get("raw_answer") or ""),
            min_answer_chars=min_answer_chars,
        )
        for attempt in attempts
    )


def result_has_terminal_failure(payload: dict) -> bool:
    return any(
        str(attempt.get("failure") or "") in TERMINAL_FAILURES
        for attempt in payload.get("attempts") or []
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--expected-repeats", type=int, default=1)
    parser.add_argument("--min-answer-chars", type=int, default=300)
    parser.add_argument("--terminal-failure", action="store_true")
    args = parser.parse_args()
    try:
        payload = json.loads(args.result.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 1
    if args.terminal_failure:
        return 0 if result_has_terminal_failure(payload) else 1
    return 0 if result_is_clean_success(
        payload,
        expected_repeats=args.expected_repeats,
        min_answer_chars=args.min_answer_chars,
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
