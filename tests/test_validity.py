from look4geo_probe.validity import result_is_clean_success, result_has_terminal_failure


def result(prompt, attempts, status="succeeded"):
    return {"prompt": prompt, "status": status, "attempts": attempts}


def succeeded(answer, sample_index=1):
    return {
        "status": "succeeded",
        "raw_answer": answer,
        "sample_index": sample_index,
    }


def test_measurement_success_rejects_prompt_echo():
    payload = result("Which suppliers have stock?", [succeeded("Which suppliers have stock?")])

    assert result_is_clean_success(payload, expected_repeats=1, min_answer_chars=20) is False


def test_measurement_success_rejects_short_preamble_and_contamination():
    preamble = result("research", [succeeded("I’ll first verify the suppliers.")])
    chinese_preamble = result("research", [succeeded("我会把厂家身份、现货证据和价格分开核验。")])
    contaminated = result(
        "research",
        [succeeded("Look4GEO 主线进度：Hanyu S1 等待批准。" + "x" * 400)],
    )

    assert result_is_clean_success(preamble, expected_repeats=1, min_answer_chars=20) is False
    assert result_is_clean_success(chinese_preamble, expected_repeats=1, min_answer_chars=20) is False
    assert result_is_clean_success(contaminated, expected_repeats=1, min_answer_chars=20) is False


def test_measurement_success_requires_every_repeat_to_have_real_answer():
    payload = result(
        "research",
        [succeeded("A" * 350, 1), succeeded("B" * 350, 2)],
    )

    assert result_is_clean_success(payload, expected_repeats=3, min_answer_chars=300) is False


def test_measurement_success_accepts_complete_repeats():
    payload = result(
        "research",
        [succeeded("A" * 350, 1), succeeded("B" * 350, 2), succeeded("C" * 350, 3)],
    )

    assert result_is_clean_success(payload, expected_repeats=3, min_answer_chars=300) is True


def test_rate_limit_is_terminal_for_batch_retry():
    payload = result(
        "research",
        [{"status": "failed", "failure": "rate_limited"}],
        status="failed",
    )

    assert result_has_terminal_failure(payload) is True
