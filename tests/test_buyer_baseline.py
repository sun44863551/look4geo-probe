import pytest
from typer.testing import CliRunner
from look4geo_probe.models import BuyerBaseline, ProbeRequest, ProbeResult, JobStatus
from look4geo_probe.cli import app, set_service_factory
from look4geo_probe.mcp_server import ProbeMcpTools
from look4geo_probe.adapters.browser_skill import BskCliClient
from test_cli import FakeService


@pytest.mark.parametrize("baseline,locale", [("english", "en-US"), ("japan", "ja-JP"), ("korea", "ko-KR")])
def test_cli_baseline_preserves_question(baseline, locale):
    service = FakeService()
    set_service_factory(lambda: service)
    result = CliRunner().invoke(app, ["run", "日本語の質問", "--buyer-baseline", baseline, "--json"])
    assert result.exit_code == 0
    assert service.last_request.prompt == "日本語の質問"
    assert service.last_request.buyer_baseline.locale == locale


def test_default_does_not_infer_question_language():
    assert ProbeRequest(prompt="한국어 질문").buyer_baseline == BuyerBaseline.ENGLISH
    old = ProbeResult(job_id="old", prompt="q", status=JobStatus.SUCCEEDED)
    assert old.buyer_baseline is None


@pytest.mark.asyncio
async def test_mcp_korea():
    service = FakeService()
    await ProbeMcpTools(service).probe_run("한국어 질문", buyer_baseline="korea")
    assert service.last_request.buyer_baseline.locale == "ko-KR"


@pytest.mark.asyncio
async def test_locale_is_session_specific_and_fails_closed():
    class Client(BskCliClient):
        async def _run_json(self, *args, **kwargs):
            return {"result": {"navigatorLanguage": "ja-JP", "documentLanguage": "ja"}}
    client = Client("81483588")
    client._buyer_locales.update(japanese="ja-JP", korean="ko-KR")
    assert await client._chatgpt_english_locale_ready("japanese")
    assert not await client._chatgpt_english_locale_ready("korean")
