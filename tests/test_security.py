from look4geo_probe.models import JobStatus, PlatformAttempt
from look4geo_probe.security import sanitize_attempt, sanitize_diagnostic


def test_diagnostic_is_redacted_but_raw_answer_is_preserved():
    attempt = PlatformAttempt(
        platform="chatgpt",
        adapter="browser_skill",
        status=JobStatus.FAILED,
        raw_answer="Tokenization is useful",
        diagnostic="Authorization: Bearer sk-secret123456",
    )
    safe = sanitize_attempt(attempt)
    assert safe.raw_answer == "Tokenization is useful"
    assert "sk-secret123456" not in (safe.diagnostic or "")
    assert "[REDACTED]" in (safe.diagnostic or "")


def test_cookie_and_known_environment_values_are_redacted():
    text = "Cookie: session=abc123\nerror used my-private-key"
    safe = sanitize_diagnostic(text, secret_values=["my-private-key"])
    assert "abc123" not in safe
    assert "my-private-key" not in safe
