from __future__ import annotations

from collections.abc import Mapping

from .models import PlatformHealth, ProbeRequest, RouteMode, RoutingDecision


class Router:
    def __init__(self, platforms: dict[str, dict], routing: dict):
        self.platforms = platforms
        self.routing = routing
        self.platform_names = list(platforms)

    def route(
        self, request: ProbeRequest, health: Mapping[str, PlatformHealth]
    ) -> RoutingDecision:
        healthy: list[str] = []
        excluded: dict[str, str] = {}
        for name in self.platform_names:
            state = health.get(name, PlatformHealth(available=False, reason="unknown"))
            if not state.available:
                excluded[name] = state.reason or "offline"
            elif not state.logged_in:
                excluded[name] = state.reason or "login_required"
            elif state.rate_limited:
                excluded[name] = state.reason or "rate_limited"
            else:
                healthy.append(name)

        if request.mode == RouteMode.MANUAL:
            selected = [name for name in request.platforms if name in healthy]
            for name in request.platforms:
                if name not in self.platforms:
                    excluded[name] = "unknown_platform"
            return self._decision(selected, excluded, request.prompt, [])

        if request.mode == RouteMode.ALL:
            return self._decision(healthy, excluded, request.prompt, [])

        scores, reasons = self._score(request.prompt)
        ranked = sorted(healthy, key=lambda name: (-scores[name], self.platform_names.index(name)))
        mode_config = self.routing["modes"][request.mode.value]
        limit = int(mode_config["max"])
        selected = ranked[:limit]

        wants_domestic = self._wants_domestic(request.prompt)
        wants_international = self._wants_international(request.prompt)
        coverage_gaps: list[str] = []
        if wants_domestic and wants_international:
            selected = self._balance_regions(selected, ranked, limit)
            regions = {self.platforms[name]["region"] for name in selected}
            if "domestic" not in regions:
                coverage_gaps.append("domestic")
            if "international" not in regions:
                coverage_gaps.append("international")

        selected_reasons = {
            name: reasons[name] or ["default_priority"] for name in selected
        }
        return self._decision(selected, excluded, request.prompt, coverage_gaps, selected_reasons)

    def _score(self, prompt: str) -> tuple[dict[str, int], dict[str, list[str]]]:
        lowered = prompt.casefold()
        scores = {name: 0 for name in self.platform_names}
        reasons = {name: [] for name in self.platform_names}
        for name, signals in self.routing.get("signals", {}).items():
            for signal in signals:
                if str(signal).casefold() in lowered:
                    scores[name] += 10
                    reasons[name].append(f"matched:{signal}")
        if self._wants_domestic(prompt):
            for name, config in self.platforms.items():
                if config["region"] == "domestic":
                    scores[name] += 2
        if self._wants_international(prompt):
            for name, config in self.platforms.items():
                if config["region"] == "international":
                    scores[name] += 2
        return scores, reasons

    @staticmethod
    def _wants_domestic(prompt: str) -> bool:
        lowered = prompt.casefold()
        return any(word in lowered for word in ("中国", "国内", "中文", "china"))

    @staticmethod
    def _wants_international(prompt: str) -> bool:
        lowered = prompt.casefold()
        return any(word in lowered for word in ("海外", "全球", "国际", "global", "international"))

    def _balance_regions(self, selected: list[str], ranked: list[str], limit: int) -> list[str]:
        result = list(selected)
        for region in ("domestic", "international"):
            if any(self.platforms[name]["region"] == region for name in result):
                continue
            candidate = next(
                (name for name in ranked if self.platforms[name]["region"] == region), None
            )
            if candidate:
                if len(result) >= limit:
                    result[-1] = candidate
                else:
                    result.append(candidate)
        return list(dict.fromkeys(result))

    def _decision(
        self,
        selected: list[str],
        excluded: dict[str, str],
        prompt: str,
        gaps: list[str],
        reasons: dict[str, list[str]] | None = None,
    ) -> RoutingDecision:
        return RoutingDecision(
            selected_platforms=selected,
            reasons=reasons or {name: ["explicit_mode"] for name in selected},
            excluded=excluded,
            coverage_gaps=gaps,
            domestic_count=sum(
                self.platforms[name]["region"] == "domestic" for name in selected
            ),
            international_count=sum(
                self.platforms[name]["region"] == "international" for name in selected
            ),
        )
