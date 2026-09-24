from __future__ import annotations

import asyncio
import json as jsonlib
from collections.abc import Callable

import typer

from .doctor import collect_checks, summarize_checks
from .models import ProbeRequest, RouteMode
from .runtime import build_service

app = typer.Typer(no_args_is_help=True)
_service_factory: Callable = build_service


def set_service_factory(factory: Callable) -> None:
    global _service_factory
    _service_factory = factory


def _print(value, as_json: bool) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if as_json:
        typer.echo(jsonlib.dumps(value, ensure_ascii=False, indent=2))
    else:
        typer.echo(value if isinstance(value, str) else jsonlib.dumps(value, ensure_ascii=False, indent=2))


@app.command()
def run(
    prompt: str,
    mode: RouteMode = typer.Option(RouteMode.AUTO),
    platform: list[str] = typer.Option(None),
    repeats: int = typer.Option(1, min=1, max=10),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    async def execute():
        service = _service_factory()
        submission = await service.run(
            ProbeRequest(prompt=prompt, mode=mode, platforms=platform or [], repeats=repeats)
        )
        await service.wait(submission["job_id"])
        return service.result(submission["job_id"])

    _print(asyncio.run(execute()), json_output)


@app.command()
def status(job_id: str, json_output: bool = typer.Option(False, "--json")) -> None:
    job = _service_factory().status(job_id)
    _print(
        {"job_id": job.job_id, "status": job.status.value, "diagnostic": job.diagnostic},
        json_output,
    )


@app.command()
def result(job_id: str, json_output: bool = typer.Option(False, "--json")) -> None:
    _print(_service_factory().result(job_id), json_output)


@app.command()
def platforms(json_output: bool = typer.Option(False, "--json")) -> None:
    _print(asyncio.run(_service_factory().platforms()), json_output)


@app.command()
def login(platform: str, json_output: bool = typer.Option(False, "--json")) -> None:
    _print(asyncio.run(_service_factory().login(platform)), json_output)


@app.command()
def doctor(json_output: bool = typer.Option(False, "--json")) -> None:
    checks = collect_checks()
    payload = {"summary": summarize_checks(checks), "checks": checks}
    _print(payload, json_output)
    if not payload["summary"]["ok"]:
        raise typer.Exit(1)
