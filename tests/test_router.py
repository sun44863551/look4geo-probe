from pathlib import Path

from look4geo_probe.config import load_configuration
from look4geo_probe.models import PlatformHealth, ProbeRequest, RouteMode
from look4geo_probe.router import Router


def make_router() -> Router:
    platforms, routing = load_configuration(Path(__file__).parents[1] / "config")
    return Router(platforms, routing)


def all_healthy() -> dict[str, PlatformHealth]:
    return {name: PlatformHealth() for name in make_router().platform_names}


def test_auto_citation_prompt_prefers_perplexity():
    decision = make_router().route(ProbeRequest(prompt="请提供可核验引用的全球资料"), all_healthy())
    assert "perplexity" in decision.selected_platforms


def test_auto_mixed_market_balances_regions():
    decision = make_router().route(
        ProbeRequest(prompt="比较中国和海外新能源汽车品牌认知"), all_healthy()
    )
    assert decision.domestic_count >= 1
    assert decision.international_count >= 1


def test_mixed_market_records_gap_when_international_unavailable():
    health = all_healthy()
    for name in ("chatgpt", "gemini", "perplexity", "grok"):
        health[name] = PlatformHealth(available=False, reason="offline")
    decision = make_router().route(
        ProbeRequest(prompt="比较中国和海外新能源汽车品牌认知"), health
    )
    assert decision.selected_platforms
    assert "international" in decision.coverage_gaps


def test_manual_never_substitutes_platform():
    health = all_healthy()
    health["chatgpt"] = PlatformHealth(available=False, reason="offline")
    decision = make_router().route(
        ProbeRequest(prompt="test", mode=RouteMode.MANUAL, platforms=["chatgpt"]), health
    )
    assert decision.selected_platforms == []
    assert decision.excluded["chatgpt"] == "offline"


def test_all_selects_every_healthy_platform():
    decision = make_router().route(
        ProbeRequest(prompt="test", mode=RouteMode.ALL), all_healthy()
    )
    assert decision.selected_platforms == make_router().platform_names
