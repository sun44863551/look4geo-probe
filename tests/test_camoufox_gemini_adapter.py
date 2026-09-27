from pathlib import Path

import pytest

from look4geo_probe.adapters.camoufox_gemini import (
    CamoufoxGeminiAdapter,
    GeminiBrowserResult,
)
from look4geo_probe.adapters.types import AdapterHealth
from look4geo_probe.models import JobStatus, ProbeRequest


class FakeRuntime:
    def __init__(self, health=AdapterHealth(True, "ready")):
        self.health_result = health
        self.health_profiles: list[Path] = []
        self.login_profiles: list[Path] = []

    async def health(self, profile_dir: Path) -> AdapterHealth:
        self.health_profiles.append(profile_dir)
        return self.health_result

    async def login(self, profile_dir: Path) -> dict:
        self.login_profiles.append(profile_dir)
        return {"status": "succeeded", "platform": "gemini"}

    async def probe(
        self, profile_dir: Path, prompt: str, timeout: float, artifact_dir: Path
    ) -> GeminiBrowserResult:
        return GeminiBrowserResult(status=JobStatus.SUCCEEDED, answer="answer")


def make_adapter(tmp_path: Path, runtime=None) -> CamoufoxGeminiAdapter:
    return CamoufoxGeminiAdapter(
        runtime or FakeRuntime(),
        tmp_path / "profile",
        tmp_path / "artifacts",
    )


def test_constructor_does_not_create_profile_or_artifact_directories(tmp_path):
    adapter = make_adapter(tmp_path)

    assert adapter.name == "camoufox_gemini"
    assert not (tmp_path / "profile").exists()
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.asyncio
async def test_health_delegates_for_gemini(tmp_path):
    runtime = FakeRuntime(AdapterHealth(True, "browser installed"))
    adapter = make_adapter(tmp_path, runtime)

    result = await adapter.health("gemini")

    assert result == AdapterHealth(True, "browser installed")
    assert runtime.health_profiles == [tmp_path / "profile"]


@pytest.mark.asyncio
async def test_adapter_rejects_non_gemini_platforms(tmp_path):
    adapter = make_adapter(tmp_path)

    with pytest.raises(ValueError, match="only supports gemini"):
        await adapter.health("chatgpt")
    with pytest.raises(ValueError, match="only supports gemini"):
        await adapter.login("chatgpt")
    with pytest.raises(ValueError, match="only supports gemini"):
        await adapter.run("chatgpt", ProbeRequest(prompt="hello"))


@pytest.mark.asyncio
async def test_health_rejects_profile_path_that_is_a_file(tmp_path):
    profile = tmp_path / "profile"
    profile.write_text("not a directory", encoding="utf-8")
    adapter = CamoufoxGeminiAdapter(FakeRuntime(), profile, tmp_path / "artifacts")

    result = await adapter.health("gemini")

    assert result.ok is False
    assert result.detail == "profile path is not a directory"


@pytest.mark.asyncio
async def test_health_rejects_profile_parent_without_write_bits(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    adapter = CamoufoxGeminiAdapter(
        FakeRuntime(), locked / "profile", tmp_path / "artifacts"
    )

    try:
        result = await adapter.health("gemini")
    finally:
        locked.chmod(0o755)

    assert result.ok is False
    assert result.detail == "profile parent is not writable"


@pytest.mark.asyncio
async def test_login_delegates_without_exposing_credentials(tmp_path):
    runtime = FakeRuntime()
    adapter = make_adapter(tmp_path, runtime)

    result = await adapter.login("gemini")

    assert result == {"status": "succeeded", "platform": "gemini"}
    assert runtime.login_profiles == [tmp_path / "profile"]
