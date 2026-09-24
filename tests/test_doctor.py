from look4geo_probe.doctor import summarize_checks


def test_required_failure_makes_doctor_unhealthy():
    checks = [
        {"name": "chrome", "required": True, "ok": True, "detail": "found"},
        {"name": "python", "required": True, "ok": False, "detail": "3.9"},
    ]
    assert summarize_checks(checks) == {"ok": False, "failed": ["python"]}


def test_optional_failure_does_not_make_doctor_unhealthy():
    checks = [{"name": "docker", "required": False, "ok": False, "detail": "missing"}]
    assert summarize_checks(checks) == {"ok": True, "failed": []}
