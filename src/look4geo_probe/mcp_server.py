from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .models import ProbeRequest, RouteMode
from .runtime import build_service


class ProbeMcpTools:
    def __init__(self, service):
        self.service = service

    async def probe_run(
        self,
        prompt: str,
        mode: str = "auto",
        platforms: list[str] | None = None,
        repeats: int = 1,
    ) -> dict:
        submission = await self.service.run(
            ProbeRequest(
                prompt=prompt,
                mode=RouteMode(mode),
                platforms=platforms or [],
                repeats=repeats,
            )
        )
        submission["status"] = submission["status"].value
        return submission

    async def probe_status(self, job_id: str) -> dict:
        job = self.service.status(job_id)
        return {
            "job_id": job.job_id,
            "status": job.status.value,
            "diagnostic": job.diagnostic,
        }

    async def probe_result(self, job_id: str, format: str = "structured") -> dict:
        return self.service.result(job_id).model_dump(mode="json")

    async def probe_platforms(self) -> dict:
        return await self.service.platforms()

    async def probe_login(self, platform: str) -> dict:
        return await self.service.login(platform)


def create_mcp(service=None) -> FastMCP:
    tools = ProbeMcpTools(service or build_service())
    mcp = FastMCP("look4geo-probe")
    mcp.tool()(tools.probe_run)
    mcp.tool()(tools.probe_status)
    mcp.tool()(tools.probe_result)
    mcp.tool()(tools.probe_platforms)
    mcp.tool()(tools.probe_login)
    return mcp


def main() -> None:
    create_mcp().run(transport="stdio")


if __name__ == "__main__":
    main()
