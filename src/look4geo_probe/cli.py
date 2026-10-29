from __future__ import annotations

import asyncio
import json as jsonlib
from pathlib import Path
from collections.abc import Callable

import typer

from .doctor import collect_checks, summarize_checks
from .models import BuyerBaseline, ProbeRequest, RouteMode
from .runtime import build_service

app = typer.Typer(no_args_is_help=True)
_service_factory: Callable = build_service


@app.command("source-report")
def source_report(
    raw_directory: Path = typer.Argument(..., exists=True, file_okay=False),
    output: Path = typer.Option(..., "--output"),
    platform: str = typer.Option("gemini", "--platform"),
) -> None:
    """Recount saved source evidence without web requests or overwriting files."""
    from .source_reporting import build_source_report

    if output.exists():
        raise typer.BadParameter("Output already exists; choose a new report path")
    try:
        report = build_source_report(raw_directory, platform)
        # Exclusive creation also protects against a concurrent writer.
        with output.open("x", encoding="utf-8") as handle:
            jsonlib.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except (ValueError, OSError) as error:
        raise typer.BadParameter(str(error)) from error
    _print({"output": str(output.resolve()), "question_count": report["question_count"],
            "source_statistics": report["source_statistics"]}, True)


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
    expected_term: list[str] = typer.Option(None, "--expected-term"),
    timeout: float | None = typer.Option(None, "--timeout", min=1, max=1800),
    repeats: int = typer.Option(1, min=1, max=10),
    buyer_baseline: BuyerBaseline = typer.Option(BuyerBaseline.ENGLISH, "--buyer-baseline"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    async def execute():
        service = _service_factory()
        options = {}
        if timeout is not None:
            options["timeout"] = timeout
        if expected_term:
            options["expected_terms"] = expected_term
        submission = await service.run(
            ProbeRequest(
                prompt=prompt,
                buyer_baseline=buyer_baseline,
                mode=mode,
                platforms=platform or [],
                options=options,
                repeats=repeats,
            )
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
