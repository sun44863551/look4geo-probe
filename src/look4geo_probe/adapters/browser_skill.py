from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .base import ProbeAdapter
from .types import AdapterHealth
from ..browser_lock import BrowserOperationLock
from ..models import (
    FailureKind,
    BuyerBaseline,
    JobStatus,
    PlatformAttempt,
    ProbeRequest,
    SourceCaptureStatus,
    SourceEvidenceOrigin,
    SourceRecord,
    SourceRole,
)
from ..sources import citations_from_sources, merge_sources, normalize_source_url
from ..validity import CONTAMINATION_RE

PLATFORMS = {
    "doubao": {
        "url": "https://www.doubao.com/chat/?channel=sysceo&from_login=1",
        "textbox": (
            "发送消息",
            "输入消息",
            "问问豆包",
            "发消息...",
            "发消息或按住空格说话...",
        ),
        "composer_selector": 'textarea, div[role="textbox"], div[contenteditable="true"]',
        "login_markers": ("登录", "扫码登录"),
        "blocking_login_markers": (
            "登录以解锁更多功能",
            "使用豆包或飞书账号登录",
        ),
        "conversation_marker": "/chat/",
        "answer_selector": '[data-testid="message_text_content"]',
        "default_timeout": 300.0,
    },
    "chatgpt": {
        "url": "https://chatgpt.com/",
        "textbox": ("给 ChatGPT 发消息", "询问 ChatGPT", "与 ChatGPT 聊天", "Chat with ChatGPT"),
        "composer_selector": 'div[contenteditable="true"]',
        "login_markers": ("登录", "注册"),
        "conversation_marker": "/c/",
        "answer_selector": '[data-message-author-role="assistant"] .markdown',
    },
    "deepseek": {
        "url": "https://chat.deepseek.com/",
        "textbox": ("给 DeepSeek 发送消息",),
        "composer_selector": 'textarea, div[contenteditable="true"]',
        "login_markers": ("请输入手机号", "密码登录"),
        "conversation_marker": "/a/chat/s/",
        "answer_selector": ".ds-markdown, .markdown",
    },
    "yuanbao": {
        "url": "https://yuanbao.tencent.com/chat",
        "textbox": ("输入消息", "有问题尽管问我", "给元宝发送消息"),
        "composer_selector": 'textarea, div[role="textbox"], div[contenteditable="true"], div[data-slate-editor="true"]',
        "login_markers": ("登录", "微信扫码登录", "上次登录"),
        "blocking_login_markers": ("微信登录", "请使用微信扫描二维码登录"),
        "conversation_marker": "/chat/",
        "answer_selector": '[data-role="assistant"], [class*="assistant"], [class*="markdown"], [class*="answer"]',
    },
    "baidu": {
        "url": "https://chat.baidu.com/",
        "textbox": (),
        "composer_selector": "textarea.ci-textarea",
        "login_markers": ("请登录", "登录同步历史对话"),
        "conversation_marker": "/search/",
        "answer_selector": ".chat-search-answer-generate",
    },
    "gemini": {
        "url": "https://gemini.google.com/app",
        "textbox": (
            "输入提示",
            "Enter a prompt",
            "Enter a prompt for Gemini",
            "向 Gemini 提问",
        ),
        "composer_selector": (
            'div[aria-label="Enter a prompt for Gemini"], '
            'rich-textarea [contenteditable="true"][role="textbox"], '
            'div[contenteditable="true"][role="textbox"][aria-multiline="true"]'
        ),
        "login_markers": ("登录", "Sign in", "Continue with Google"),
        "conversation_marker": "/app/",
        "answer_selector": "message-content .markdown",
    },
    "perplexity": {
        "url": "https://www.perplexity.ai/",
        "textbox": ("输入 @ 以使用连接器", "问任何事情..."),
        "composer_selector": 'div[contenteditable="true"]',
        "login_markers": ("登录", "继续使用"),
        "conversation_marker": "/search/",
        "answer_selector": '[class*="prose"]',
    },
    "grok": {
        "url": "https://grok.com/",
        "textbox": ("Ask Grok anything", "Ask anything", "向 Grok 提问", "输入消息"),
        "composer_selector": (
            'div[contenteditable="true"][aria-label="Ask Grok anything"], '
            'div[role="textbox"], textarea'
        ),
        "login_markers": ("登录", "Sign in", "Continue with X", "Continue with Google"),
        "conversation_marker": "/c/",
        "answer_selector": '[data-message-author-role="assistant"], [class*="assistant"], [class*="response"], [class*="markdown"]',
    },
}
for _platform_config in PLATFORMS.values():
    _platform_config.update(
        {
            "source_trigger_labels": (),
            "source_trigger_selectors": (),
            "source_panel_selectors": (),
            "source_panel_labels": (),
            "source_card_selectors": (),
            "source_url_attributes": ("href",),
            "excluded_source_domains": (),
            "document_source_fallback": True,
        }
    )

PLATFORMS["doubao"].update(
    {
        "document_source_fallback": False,
        "source_trigger_labels": ("来源", "参考资料", "网页"),
        "source_trigger_selectors": ('[data-testid*="source"]',),
        "source_panel_selectors": ('[role="dialog"]',),
        "source_panel_labels": ("来源", "参考资料"),
        "source_card_selectors": ("a[href]",),
        "excluded_source_domains": ("doubao.com",),
    }
)
PLATFORMS["deepseek"].update(
    {
        "source_trigger_labels": ("个网页", "搜索到"),
        "source_panel_labels": ("搜索结果",),
        "source_card_selectors": ("a[href]",),
        "excluded_source_domains": ("deepseek.com",),
    }
)
PLATFORMS["yuanbao"].update(
    {
        "source_trigger_labels": ("源", "引用来源"),
        "source_panel_selectors": (".agent-dialogue-references",),
        "source_panel_labels": ("引用来源",),
        "source_card_selectors": (".agent-dialogue-references__item",),
        "source_url_attributes": ("data-url", "href"),
        "excluded_source_domains": ("yuanbao.tencent.com",),
    }
)
PLATFORMS["baidu"].update(
    {
        "source_trigger_labels": ("来源", "参考资料"),
        "source_panel_labels": ("来源", "参考资料"),
        "source_card_selectors": ("a[href]",),
        "excluded_source_domains": ("wenxin.baidu.com", "chat.baidu.com"),
    }
)
PLATFORMS["chatgpt"].update(
    {
        "source_trigger_labels": ("Sources", "来源", "引用"),
        "source_trigger_selectors": ('button[aria-label*="source" i]',),
        "source_panel_selectors": ('[role="dialog"]',),
        "source_panel_labels": ("Sources", "来源"),
        "source_card_selectors": ("a[href]",),
        "excluded_source_domains": ("chatgpt.com",),
    }
)
PLATFORMS["gemini"].update(
    {
        "source_trigger_labels": ("View source details", "Sources", "来源"),
        "source_trigger_selectors": (
            'button[aria-label*="View source details" i]',
        ),
        "source_panel_selectors": ('[role="dialog"]',),
        "source_panel_labels": ("Sources", "来源"),
        "source_card_selectors": ("a[href]",),
        "excluded_source_domains": ("gemini.google.com",),
    }
)
PLATFORMS["perplexity"].update(
    {
        "answer_sources_only": True,
        "source_trigger_labels": ("个来源", "来源", "Sources"),
        "source_panel_selectors": ('[class*="max-h-[300px]"]',),
        "source_panel_labels": ("来源", "Sources", "链接"),
        "source_card_selectors": ("a[href]",),
        "excluded_source_domains": ("perplexity.ai",),
    }
)
PLATFORMS["grok"].update(
    {
        "source_trigger_labels": ("Sources", "来源", "网页"),
        "source_trigger_selectors": ('button[aria-label*="source" i]',),
        "source_panel_selectors": ('[role="dialog"]',),
        "source_panel_labels": ("Sources", "来源"),
        "source_card_selectors": ("a[href]",),
        "excluded_source_domains": ("grok.com",),
    }
)
REF_PATTERN = re.compile(r"(@e\d+)\s+textbox\s+\"([^\"]+)\"")
BUTTON_REF_PATTERN = re.compile(r"(@e\d+)\s+button\s+\"([^\"]+)\"")
CONTROL_REF_PATTERN = re.compile(
    r"(@e\d+)\s+(?:button|menuitem)\s+\"([^\"]+)\""
)
URL_PATTERN = re.compile(r"https?://[^\s<>\])}\u200b\u2060]+")


def extract_text_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(".,;:!?，。；：！？'\"")
        following = text[match.end() :]
        if url.endswith("-") and re.match(r"\r?\n\d+(?:\r?\n|$)", following):
            url = url[:-1]
        if url:
            urls.append(url)
    return urls
TRANSIENT_ANSWER_LINES = {
    "正在搜索网络",
    "跳过",
    "搜索中",
    "思考中",
    "generating",
    "searching the web",
    "skip",
    "核查供应商资料",
}
RATE_LIMIT_MARKERS = (
    "429",
    "rate limit",
    "请求过于频繁",
    "已达到免费搜索次数上限",
    "使用权限将在几小时后重置",
    "free searches limit",
    "距离限制重置还剩",
    "upgrade to supergrok",
    # Grok 免费层限额文案。界面语言会随账号/浏览器语言变化：
    # 英文为「Free tier limit reached / Upgrade to SuperGrok」，
    # 中文为「免费版限额已达上限 / 请稍后再试，或升级至 SuperGrok 享受更高限额和高级功能。」
    # 2026-09-29 实测：中文文案未被识别，导致额度墙被误记为 timeout 且跳过挂起机制。
    "free tier limit reached",
    "免费版限额已达上限",
    "限额已达上限",
    "升级到 supergrok",
    "升级至 supergrok",
)
GEMINI_REFUSAL_MARKERS = (
    "i'm having a hard time fulfilling your request",
    "i am having a hard time fulfilling your request",
    "我只是一个语言模型，理解不了这个问题",
    "我只是一个语言模型，无法提供",
    "身为一个语言模型，我没办法提供",
    "我只是一个语言模型，不具备",
)
GEMINI_TRANSIENT_ERROR_MARKERS = (
    "i seem to be encountering an error",
    "i encountered an error doing what you asked",
    "something went wrong",
    "there was an error generating a response",
)
HUMAN_VERIFICATION_MARKERS = (
    "请确认你的年龄以继续",
    "你出生于哪一年",
    "通过验证以确保正常访问",
    "请拖动下方滑块完成验证",
    "Our systems have detected unusual traffic from your computer network",
    "我们的系统检测到您的计算机网络中存在异常流量",
)
PREAMBLE_PATTERN = re.compile(
    r"^(?:我会|我先|我将|我来|I(?:['’]?ll| will)\b|Let me\b)", re.I
)


def command_error_detail(stdout: bytes, stderr: bytes) -> str:
    stderr_text = stderr.decode(errors="replace").strip()
    if stderr_text:
        return stderr_text
    stdout_text = stdout.decode(errors="replace").strip()
    if stdout_text:
        try:
            payload = json.loads(stdout_text)
            code = str(payload.get("code") or "bsk_error")
            message = str(payload.get("message") or payload.get("hint") or "command failed")
            return f"{code}: {message}"
        except (json.JSONDecodeError, AttributeError):
            return stdout_text
    return "bsk command failed"


@dataclass(frozen=True)
class BrowserProbeOutput:
    answer: str = ""
    citations: list[str] = field(default_factory=list)
    sources: list[SourceRecord] = field(default_factory=list)
    source_capture_status: SourceCaptureStatus = SourceCaptureStatus.NONE_EXPOSED
    source_capture_diagnostic: str | None = None
    login_required: bool = False
    failure: FailureKind | None = None
    diagnostic: str | None = None


@dataclass(frozen=True)
class NormalizedPrompt:
    original: str
    sent: str
    changed: bool


def normalize_for_browser(prompt: str) -> NormalizedPrompt:
    replacements = {
        "≥": ">=",
        "≤": "<=",
        "—": "-",
        "–": "-",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "\u00a0": " ",
    }
    sent = "".join(replacements.get(character, character) for character in prompt)
    return NormalizedPrompt(original=prompt, sent=sent, changed=sent != prompt)


def select_main_answer(platform: str, candidates: list[str]) -> str:
    usable = [candidate.strip() for candidate in candidates if is_valid_answer(candidate)]
    if not usable:
        return ""
    if platform in {"perplexity", "yuanbao"}:
        return max(usable, key=len)
    return usable[-1]


def is_valid_answer(candidate: str) -> bool:
    lines = [line.strip() for line in candidate.splitlines() if line.strip()]
    if not lines:
        return False
    meaningful = [line for line in lines if line.casefold() not in TRANSIENT_ANSWER_LINES]
    return bool(meaningful)


def comparable_text(value: str) -> str:
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE).casefold()


def collapse_overlapping_stream_prefix(value: str, *, minimum_prefix: int = 40) -> str:
    """Remove a duplicated leading stream fragment from a rendered answer."""
    anchor = value[:24]
    restart = value.find(anchor, max(minimum_prefix, len(anchor)))
    if restart >= 0 and value[restart - 1].isalnum():
        return value[restart:]
    for boundary in range(minimum_prefix, len(value) // 2 + 1):
        if value[boundary:].startswith(value[:boundary]):
            return value[boundary:]
    return value


def is_prompt_echo(candidate: str, prompt: str) -> bool:
    return bool(candidate.strip()) and comparable_text(candidate) == comparable_text(prompt)


def page_is_rate_limited(page: dict) -> bool:
    text = str(page.get("page_text") or "").casefold()
    return bool(page.get("rate_limited")) or any(marker.casefold() in text for marker in RATE_LIMIT_MARKERS)


def rate_limit_diagnostic(page: dict) -> str:
    text = str(page.get("page_text") or "")
    reset_hint = re.search(
        r"距离限制重置还剩\s*\d+\s*小时(?:\s*\d+\s*分钟)?",
        text,
    )
    if reset_hint:
        return f"platform quota or rate limit detected: {reset_hint.group(0)}"
    return "platform quota or rate limit detected"


def page_requires_human_verification(page: dict) -> bool:
    text = str(page.get("page_text") or "")
    return any(marker in text for marker in HUMAN_VERIFICATION_MARKERS)


def is_incomplete_preamble(platform: str, answer: str) -> bool:
    return (
        platform in {"chatgpt", "doubao"}
        and len(answer) < 300
        and bool(PREAMBLE_PATTERN.match(answer))
    )


def is_context_contamination(platform: str, prompt: str, answer: str) -> bool:
    return (
        platform == "chatgpt"
        and bool(CONTAMINATION_RE.search(answer))
        and not bool(CONTAMINATION_RE.search(prompt))
    )


def answers_after_baseline(candidates: list[str], baseline: list[str]) -> list[str]:
    """Return only answer nodes that are new or changed since prompt submission."""
    baseline_counts: dict[str, int] = {}
    for answer in baseline:
        normalized = answer.strip()
        baseline_counts[normalized] = baseline_counts.get(normalized, 0) + 1
    delta: list[str] = []
    for answer in candidates:
        normalized = answer.strip()
        if baseline_counts.get(normalized, 0):
            baseline_counts[normalized] -= 1
        else:
            delta.append(answer)
    return delta


def answer_links_for_text(page: dict, answer: str) -> list[dict[str, str]]:
    target = answer.strip()
    entries = page.get("answer_entries", [])
    for entry in reversed(entries if isinstance(entries, list) else []):
        if str(entry.get("text") or "").strip() == target:
            return list(entry.get("links", []))
    links = page.get("answer_links", [])
    return list(links) if isinstance(links, list) else []


def submission_confirmed(
    platform: str,
    before_url: str,
    after_url: str,
    *,
    answer_started: bool = False,
) -> bool:
    marker = PLATFORMS[platform]["conversation_marker"]
    return answer_started or bool(after_url and after_url != before_url and marker in after_url)


class BskCliClient:
    def __init__(self, browser_instance_id: str, *, poll_interval: float = 2.0, allow_unconfigured: bool = False):
        if not browser_instance_id and not allow_unconfigured:
            raise ValueError("browser_instance_id is required")
        self.browser_instance_id = browser_instance_id
        self.poll_interval = poll_interval
        self._session_tabs: dict[str, int] = {}
        self._buyer_locales: dict[str, str] = {}
        self._locale_verified: dict[str, bool] = {}

    def _args_with_pinned_tab(self, args: tuple[str, ...]) -> tuple[str, ...]:
        if "--tab-id" in args or "--session" not in args:
            return args
        session_index = args.index("--session") + 1
        if session_index >= len(args):
            return args
        tab_id = self._session_tabs.get(args[session_index])
        return args + ("--tab-id", str(tab_id)) if tab_id is not None else args

    async def _run_json(self, *args: str, timeout: float = 30.0) -> dict | list:
        args = self._args_with_pinned_tab(args)
        process = await asyncio.create_subprocess_exec(
            "bsk",
            *args,
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        if process.returncode != 0:
            raise RuntimeError(command_error_detail(stdout, stderr))
        return json.loads(stdout.decode("utf-8"))

    async def start(self, platform: str) -> str:
        if not self.browser_instance_id:
            raise RuntimeError(
                "ChatGPT sampling is suspended: configure LOOK4GEO_CHATGPT_BROWSER_ID "
                "from the dedicated sampling Chrome profile; it must differ from "
                "LOOK4GEO_BROWSER_ID. No shared browser fallback is allowed."
            )
        args = ["session", "start", "--browser", self.browser_instance_id]
        if platform != "gemini":
            args.append("--no-focus")
        result = await self._run_json(*args)
        return str(result["session_id"])

    async def probe(
        self, session_id: str, platform: str, prompt: str, timeout: float
    ) -> BrowserProbeOutput:
        config = PLATFORMS[platform]
        if platform == "chatgpt":
            await self._set_chatgpt_english_locale(session_id)
        navigation = await self._run_json(
            "navigate", config["url"], "--session", session_id, timeout=timeout
        )
        if isinstance(navigation, dict) and navigation.get("tab_id") is not None:
            self._session_tabs[session_id] = int(navigation["tab_id"])
        if platform == "chatgpt" and not await self._chatgpt_english_locale_ready(session_id):
            return BrowserProbeOutput(
                failure=FailureKind.EXTRACTION_FAILED,
                diagnostic="ChatGPT browser/page locale not confirmed; sample excluded from requested buyer baseline",
            )
        await self._dismiss_blocking_overlays(session_id, platform)
        observation = await self._run_json("observe", "--session", session_id, timeout=timeout)
        page_text = str(observation.get("text", ""))
        if page_requires_human_verification({"page_text": page_text}):
            return BrowserProbeOutput(
                login_required=True,
                diagnostic="human verification required",
            )
        if any(
            marker in page_text for marker in config.get("blocking_login_markers", ())
        ):
            return BrowserProbeOutput(login_required=True)
        textbox_ref = self._find_textbox_ref(page_text, config["textbox"])
        if not textbox_ref and not await self._wait_composer_available(
            session_id, config["composer_selector"]
        ):
            final_url = str(navigation.get("final_url", ""))
            if "sign_in" in final_url or any(
                marker in page_text for marker in config["login_markers"]
            ):
                return BrowserProbeOutput(login_required=True)
            raise RuntimeError(f"message textbox not found for {platform}")
        if platform == "perplexity" and not await self._ensure_perplexity_standard_search(
            session_id
        ):
            return BrowserProbeOutput(
                failure=FailureKind.SEND_FAILED,
                diagnostic="Perplexity standard search mode could not be confirmed",
            )
        if platform == "gemini":
            flash_confirmed = False
            for _ in range(3):
                try:
                    flash_confirmed = await self._ensure_gemini_flash(session_id)
                except RuntimeError:
                    flash_confirmed = False
                if flash_confirmed:
                    break
                await asyncio.sleep(self.poll_interval)
            if not flash_confirmed:
                return BrowserProbeOutput(
                    failure=FailureKind.SEND_FAILED,
                    diagnostic="Gemini Flash mode could not be confirmed",
                )

        baseline_page = await self._answer_page(session_id, config["answer_selector"])
        baseline = [str(value) for value in baseline_page.get("answers", [])]
        await self._enter_prompt(session_id, platform, textbox_ref, prompt, timeout=timeout)
        if not await self._wait_submission_ready(session_id, platform):
            return BrowserProbeOutput(
                failure=FailureKind.SEND_FAILED,
                diagnostic="composer remained disabled before submission",
            )
        await self._submit_prompt(session_id, platform, textbox_ref, timeout=timeout)

        deadline = asyncio.get_running_loop().time() + timeout
        previous = ""
        stable_rounds = 0
        saw_answer_candidate = False
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(self.poll_interval)
            page = await self._answer_page(session_id, config["answer_selector"])
            if page_requires_human_verification(page):
                return BrowserProbeOutput(
                    login_required=True,
                    diagnostic="human verification required",
                )
            if any(
                marker in str(page.get("page_text") or "")
                for marker in config.get("blocking_login_markers", ())
            ):
                return BrowserProbeOutput(
                    login_required=True,
                    diagnostic="login required after submission",
                )
            if page_is_rate_limited(page):
                return BrowserProbeOutput(
                    failure=FailureKind.RATE_LIMITED,
                    diagnostic=rate_limit_diagnostic(page),
                )
            page_text = str(page.get("page_text") or "").casefold()
            if platform == "gemini" and any(
                marker in page_text for marker in GEMINI_TRANSIENT_ERROR_MARKERS
            ):
                return BrowserProbeOutput(
                    failure=FailureKind.EXTRACTION_FAILED,
                    diagnostic="Gemini returned a transient platform error",
                )
            candidates = [str(value) for value in page.get("answers", [])]
            delta = answers_after_baseline(candidates, baseline)
            delta = [candidate for candidate in delta if not is_prompt_echo(candidate, prompt)]
            saw_answer_candidate = saw_answer_candidate or bool(delta)
            current_text = select_main_answer(platform, delta)
            if not current_text:
                continue
            if platform == "gemini":
                current_text = collapse_overlapping_stream_prefix(current_text)
            if is_incomplete_preamble(platform, current_text) or is_context_contamination(
                platform, prompt, current_text
            ):
                previous = current_text
                stable_rounds = 0
                continue
            lowered = current_text.casefold()
            if platform == "gemini" and any(
                marker in lowered for marker in GEMINI_TRANSIENT_ERROR_MARKERS
            ):
                return BrowserProbeOutput(
                    failure=FailureKind.EXTRACTION_FAILED,
                    diagnostic="Gemini returned a transient platform error",
                )
            if platform == "gemini" and any(
                marker in lowered for marker in GEMINI_REFUSAL_MARKERS
            ):
                return BrowserProbeOutput(
                    failure=FailureKind.EXTRACTION_FAILED,
                    diagnostic="Gemini returned a refusal response",
                )
            if "429" in lowered or "rate limit" in lowered or "请求过于频繁" in current_text:
                return BrowserProbeOutput(
                    failure=FailureKind.RATE_LIMITED, diagnostic="platform rate limit detected"
                )
            if page.get("generating"):
                stable_rounds = 0
            elif current_text == previous:
                stable_rounds += 1
                if stable_rounds >= 2:
                    answer_links = answer_links_for_text(page, current_text)
                    if not answer_links:
                        answer_links = [
                            {"url": str(url), "title": None}
                            for url in page.get("links", [])
                        ]
                    answer_links.extend(
                        {"url": url, "title": None}
                        for url in extract_text_urls(current_text)
                    )
                    try:
                        sources, source_status, source_diagnostic = (
                            await self._collect_visible_sources(
                                session_id, platform, answer_links
                            )
                        )
                    except Exception as error:
                        sources = []
                        source_status = SourceCaptureStatus.FAILED
                        source_diagnostic = str(error)
                    return BrowserProbeOutput(
                        answer=current_text,
                        citations=[
                            source.url
                            for source in sources
                            if source.source_role == SourceRole.CITED
                        ],
                        sources=sources,
                        source_capture_status=source_status,
                        source_capture_diagnostic=source_diagnostic,
                    )
            else:
                stable_rounds = 0
            previous = current_text
        if saw_answer_candidate:
            return BrowserProbeOutput(
                failure=FailureKind.EXTRACTION_FAILED,
                diagnostic="new answer candidates never became valid and stable",
            )
        raise asyncio.TimeoutError

    async def _enter_prompt(
        self,
        session_id: str,
        platform: str,
        textbox_ref: str | None,
        prompt: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        if platform == "gemini":
            selector = PLATFORMS[platform]["composer_selector"]
            target = ("--ref", textbox_ref) if textbox_ref else ("--selector", selector)
            await self._run_json(
                "click", textbox_ref or selector, "--session", session_id,
                timeout=timeout,
            )
            await self._run_json(
                "press", "Meta+A", *target,
                "--session", session_id, timeout=timeout,
            )
            await self._run_json(
                "press", "Backspace", *target,
                "--session", session_id, timeout=timeout,
            )
            for character in prompt:
                key = (
                    "Space" if character == " "
                    else "Shift+Enter" if character == "\n"
                    else character
                )
                await self._run_json(
                    "press", key, *target,
                    "--session", session_id, timeout=timeout,
                )
            if (await self._composer_text(session_id, selector)).strip() != prompt.strip():
                raise RuntimeError("Gemini native prompt entry could not be verified")
            return
        if platform == "grok":
            candidate_ref = textbox_ref
            selector = PLATFORMS[platform]["composer_selector"]
            for _ in range(3):
                if candidate_ref is None:
                    observation = await self._run_json(
                        "observe", "--session", session_id, timeout=timeout
                    )
                    candidate_ref = self._find_textbox_ref(
                        str(observation.get("text", ""))
                        if isinstance(observation, dict)
                        else "",
                        PLATFORMS[platform]["textbox"],
                    )
                try:
                    if candidate_ref:
                        await self._run_json(
                            "fill", candidate_ref, "--value", prompt,
                            "--session", session_id, timeout=timeout,
                        )
                    else:
                        await self._run_json(
                            "fill", "--selector", selector, "--value", prompt,
                            "--session", session_id, timeout=timeout,
                        )
                    if (await self._composer_text(session_id, selector)).strip() == prompt.strip():
                        return
                except RuntimeError:
                    pass
                try:
                    await self._run_json(
                        "fill",
                        "--selector",
                        selector,
                        "--value",
                        prompt,
                        "--session",
                        session_id,
                        timeout=timeout,
                    )
                    if (
                        await self._composer_text(session_id, selector)
                    ).strip() == prompt.strip():
                        return
                except RuntimeError:
                    pass
                try:
                    await self._run_json(
                        "press", "Meta+A", "--selector", selector,
                        "--session", session_id, timeout=timeout,
                    )
                    await self._run_json(
                        "press", "Backspace", "--selector", selector,
                        "--session", session_id, timeout=timeout,
                    )
                    for character in prompt:
                        key = (
                            "Space" if character == " "
                            else "Shift+Enter" if character == "\n"
                            else "Shift+=" if character == "+"
                            else character
                        )
                        await self._run_json(
                            "press", key, "--selector", selector,
                            "--session", session_id, timeout=timeout,
                        )
                    if (
                        await self._composer_text(session_id, selector)
                    ).strip() == prompt.strip():
                        return
                except RuntimeError:
                    pass
                insertion = await self._run_json(
                    "evaluate",
                    "(() => { const nodes = [...document.querySelectorAll("
                    + json.dumps(selector)
                    + ")]; const target = nodes.find(node => "
                    "node.getAttribute('aria-label') === 'Ask Grok anything') || "
                    "nodes.find(node => node.getClientRects().length); "
                    "if (!target) return ''; target.focus(); "
                    "document.execCommand('selectAll', false, null); "
                    "document.execCommand('insertText', false, "
                    + json.dumps(prompt)
                    + "); return target.innerText || target.textContent || target.value || ''; })()",
                    "--session",
                    session_id,
                    timeout=timeout,
                )
                if str(self._result_value(insertion) or "").strip() == prompt.strip():
                    return
                candidate_ref = None
            raise RuntimeError("Grok prompt entry could not be verified after 3 attempts")

        if textbox_ref:
            try:
                await self._run_json(
                    "fill", textbox_ref, "--value", prompt, "--session", session_id, timeout=timeout
                )
                if platform != "gemini":
                    return
                value = await self._composer_text(
                    session_id, PLATFORMS[platform]["composer_selector"]
                )
                if value.strip() == prompt.strip():
                    return
            except RuntimeError:
                if platform == "gemini":
                    value = await self._composer_text(
                        session_id, PLATFORMS[platform]["composer_selector"]
                    )
                    if value.strip() == prompt.strip():
                        return

        selector = PLATFORMS[platform]["composer_selector"]
        await self._run_json("click", selector, "--session", session_id, timeout=timeout)
        await self._run_json(
            "press", "Meta+A", "--selector", selector, "--session", session_id, timeout=timeout
        )
        await self._run_json("press", "Backspace", "--session", session_id, timeout=timeout)
        if platform == "perplexity":
            try:
                await self._run_json(
                    "fill", "--selector", selector, "--no-clear", "--value", prompt,
                    "--session", session_id, timeout=timeout
                )
                return
            except RuntimeError as error:
                if "fill could not verify the expected value" in str(error):
                    value = await self._composer_text(session_id, selector)
                    if value.strip() == prompt.strip():
                        return
        expression = (
            "(() => { const nodes = [...document.querySelectorAll("
            + json.dumps(selector)
            + ")]; const target = nodes.find(node => node.getClientRects().length); "
            "if (!target) return false; target.focus(); "
            "document.execCommand('selectAll', false, null); const inserted = "
            "document.execCommand('insertText', false, "
            + json.dumps(prompt)
            + "); if (target) target.dispatchEvent(new InputEvent('input', "
            "{bubbles:true,inputType:'insertText',data:"
            + json.dumps(prompt)
            + "})); return inserted; })()"
        )
        await self._run_json("evaluate", expression, "--session", session_id, timeout=timeout)

    async def _dismiss_blocking_overlays(self, session_id: str, platform: str) -> bool:
        if platform not in {"doubao", "baidu"}:
            return False
        marker = "下载豆包电脑版" if platform == "doubao" else "全新上线任务模式"
        expression = (
            "(() => { const marker = " + json.dumps(marker, ensure_ascii=False) + "; "
            "const candidates = [...document.querySelectorAll('[role=dialog],body > div')]; "
            "const promo = candidates.find(node => (node.innerText || '').includes(marker)); "
            "if (!promo) return false; const controls = [...promo.querySelectorAll('button')]; "
            "const close = controls.find(node => /^(关闭|close)$/i.test(((node.getAttribute('aria-label') "
            "|| '') + ' ' + (node.innerText || '')).trim())) || controls[0]; if (!close) return false; "
            "close.click(); return true; })()"
        )
        result = await self._run_json(
            "evaluate", expression, "--session", session_id, timeout=30
        )
        return bool(self._result_value(result))

    async def _submit_prompt(
        self,
        session_id: str,
        platform: str,
        textbox_ref: str | None,
        *,
        timeout: float = 30.0,
    ) -> None:
        if platform == "gemini":
            state_expression = (
                "(() => { const composers = [...document.querySelectorAll(" +
                json.dumps(PLATFORMS["gemini"]["composer_selector"]) +
                ")]; const composer = composers.find(node => node.getClientRects().length); "
                "const text = composer ? (composer.innerText || composer.textContent || "") : ""; "
                "const stop = [...document.querySelectorAll('button')].some(button => "
                "/stop/i.test((button.getAttribute('aria-label') || '') + ' ' + "
                "(button.innerText || ''))); const conversation = /^\\/app\\/.+/.test(location.pathname); "
                "const answer = document.querySelector('message-content .markdown'); "
                "const userQuery = document.querySelector('user-query, .query-text, .user-query'); "
                "return {submitted: stop || conversation || Boolean(answer) || Boolean(userQuery) || !text.trim(), "
                "composerEmpty: !text.trim(), conversation, answer: Boolean(answer), "
                "userQuery: Boolean(userQuery)}; })()"
            )
            for attempt in range(3):
                observation = await self._run_json(
                    "observe", "--session", session_id, timeout=timeout
                )
                send_ref = self._find_button_ref(
                    str(observation.get("text", "")) if isinstance(observation, dict) else "",
                    ("Send message", "发送消息", "发送", "发送提示"),
                )
                try:
                    if attempt == 0:
                        await self._run_json(
                            "click",
                            send_ref
                            or (
                                'button[aria-label="发送"], button[aria-label="发送消息"], '
                                'button[aria-label*="发送" i], '
                                'button[aria-label="Send prompt"], button[aria-label*="Send" i]'
                            ),
                            "--session", session_id, timeout=timeout
                        )
                    else:
                        await self._run_json(
                            "press",
                            "Enter",
                            "--selector",
                            PLATFORMS[platform]["composer_selector"],
                            "--session",
                            session_id,
                            timeout=timeout,
                        )
                except RuntimeError:
                    await self._run_json(
                        "press",
                        "Enter",
                        "--selector",
                        PLATFORMS[platform]["composer_selector"],
                        "--session",
                        session_id,
                        timeout=timeout,
                    )
                for _ in range(10):
                    await asyncio.sleep(self.poll_interval)
                    result = await self._run_json(
                        "evaluate", state_expression, "--session", session_id, timeout=timeout
                    )
                    state = self._result_value(result)
                    if state is True or (
                        isinstance(state, dict) and state.get("submitted")
                    ):
                        return
                    visible = await self._run_json(
                        "observe", "--session", session_id, timeout=timeout
                    )
                    visible_text = str(visible.get("text", ""))
                    if "You said" in visible_text or "Gemini said" in visible_text:
                        return
            raise RuntimeError("Gemini submission state did not change after 3 attempts")
        if platform == "chatgpt":
            state_expression = (
                "(() => { const composer = [...document.querySelectorAll("
                + json.dumps(PLATFORMS["chatgpt"]["composer_selector"])
                + ")].find(node => node.getClientRects().length); "
                "const text = composer ? (composer.innerText || composer.textContent || '') : ''; "
                "const generating = [...document.querySelectorAll('button')].some(button => "
                "button.getClientRects().length && /停止生成|Stop generating|Stop response/i.test("
                "(button.getAttribute('aria-label') || '') + ' ' + (button.innerText || ''))); "
                "return {submitted: generating || Boolean(composer && !text.trim())}; })()"
            )
            for attempt in range(3):
                try:
                    if attempt == 0:
                        await self._run_json(
                            "click",
                            'button[aria-label*="发送" i], button[aria-label*="Send" i]',
                            "--session", session_id, timeout=timeout,
                        )
                    else:
                        await self._run_json(
                            "press", "Enter", "--selector",
                            PLATFORMS[platform]["composer_selector"],
                            "--session", session_id, timeout=timeout,
                        )
                except RuntimeError:
                    if attempt == 2:
                        raise
                for _ in range(3):
                    result = await self._run_json(
                        "evaluate", state_expression, "--session", session_id,
                        timeout=timeout,
                    )
                    state = self._result_value(result)
                    if isinstance(state, dict) and state.get("submitted"):
                        return
                    await asyncio.sleep(self.poll_interval)
            raise RuntimeError("ChatGPT submission state did not change after 3 attempts")
        if platform == "perplexity":
            try:
                await self._run_json(
                    "click", 'button[aria-label="提交"], button[aria-label="Submit"]',
                    "--session", session_id, timeout=timeout
                )
                return
            except RuntimeError:
                pass
        if platform in {"chatgpt", "perplexity"}:
            await self._run_json(
                "press",
                "Enter",
                "--selector",
                PLATFORMS[platform]["composer_selector"],
                "--session",
                session_id,
                timeout=timeout,
            )
            return
        if platform == "grok":
            await self._run_json(
                "press",
                "Enter",
                "--selector",
                PLATFORMS[platform]["composer_selector"],
                "--session",
                session_id,
                timeout=timeout,
            )
            return
        if textbox_ref:
            await self._run_json(
                "press", "Enter", "--ref", textbox_ref, "--session", session_id, timeout=timeout
            )
        else:
            await self._run_json(
                "press", "Enter", "--selector", PLATFORMS[platform]["composer_selector"],
                "--session", session_id, timeout=timeout
            )

    async def _composer_text(self, session_id: str, selector: str) -> str:
        expression = (
            "(() => { const node = document.querySelector(" + json.dumps(selector) + "); "
            "return node ? (node.innerText || node.textContent || node.value || '') : ''; })()"
        )
        result = await self._run_json(
            "evaluate", expression, "--session", session_id, timeout=30
        )
        return str(self._result_value(result) or "")

    async def _set_chatgpt_english_locale(self, session_id: str) -> None:
        user_agent_result = await self._run_json(
            "evaluate", "navigator.userAgent", "--session", session_id
        )
        user_agent = self._result_value(user_agent_result)
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise RuntimeError("ChatGPT English locale setup failed: browser user agent unavailable")
        await self._run_json(
            "emulate", "--ua", user_agent, "--accept-language",
            self._buyer_locales.get(session_id, "en-US") + "," + self._buyer_locales.get(session_id, "en-US").split("-")[0],
            "--session", session_id,
        )

    async def _chatgpt_english_locale_ready(self, session_id: str) -> bool:
        result = await self._run_json(
            "evaluate",
            "({navigatorLanguage:navigator.language,documentLanguage:document.documentElement.lang})",
            "--session", session_id,
        )
        state = self._result_value(result)
        language = self._buyer_locales.get(session_id, "en-US").split("-")[0]
        verified = bool(
            isinstance(state, dict)
            and str(state.get("navigatorLanguage") or "").casefold().split("-")[0] == language
            and str(state.get("documentLanguage") or "").casefold().split("-")[0] == language
        )
        self._locale_verified[session_id] = verified
        return verified

    async def _composer_available(self, session_id: str, selector: str) -> bool:
        expression = f"Boolean(document.querySelector({json.dumps(selector)}))"
        result = await self._run_json("evaluate", expression, "--session", session_id, timeout=30)
        return bool(self._result_value(result))

    async def _wait_composer_available(
        self, session_id: str, selector: str, *, rounds: int = 10
    ) -> bool:
        for _ in range(rounds):
            if await self._composer_available(session_id, selector):
                return True
            await asyncio.sleep(self.poll_interval)
        return False

    async def _wait_submission_ready(
        self, session_id: str, platform: str, *, rounds: int = 10
    ) -> bool:
        expression = (
            "(() => { const buttons = [...document.querySelectorAll('button')].filter(b => "
            "/发送|Send|提问|Ask Grok/i.test((b.getAttribute('aria-label') || '') + ' ' + "
            "(b.getAttribute('title') || '') + ' ' + (b.innerText || '')) || b.type === 'submit'); "
            "return {found: buttons.length > 0, ready: buttons.some(b => !b.disabled && "
            "b.getAttribute('aria-disabled') !== 'true')}; })()"
        )
        for _ in range(rounds):
            result = await self._run_json(
                "evaluate", expression, "--session", session_id, timeout=30
            )
            state = self._result_value(result)
            if isinstance(state, dict) and (not state.get("found") or state.get("ready")):
                return True
            await asyncio.sleep(self.poll_interval)
        return False

    async def _ensure_gemini_flash(self, session_id: str) -> bool:
        observation = await self._run_json(
            "observe", "--session", session_id, timeout=30
        )
        page_text = str(observation.get("text", ""))
        if re.search(r"Open mode picker, currently [^\"\n]*Flash", page_text, re.I):
            return True
        picker_ref = self._find_control_ref(
            page_text, ("Open mode picker, currently",)
        )
        if picker_ref is None:
            return False
        await self._run_json("click", picker_ref, "--session", session_id, timeout=30)
        flash_ref = None
        for _ in range(5):
            menu = await self._run_json("observe", "--session", session_id, timeout=30)
            flash_ref = self._find_control_ref(
                str(menu.get("text", "")), ("3.8 Flash", "Flash")
            )
            if flash_ref is not None:
                break
            await asyncio.sleep(self.poll_interval)
        if flash_ref is None:
            return False
        await self._run_json("click", flash_ref, "--session", session_id, timeout=30)
        for _ in range(5):
            verified = await self._run_json(
                "observe", "--session", session_id, timeout=30
            )
            if re.search(
                r"Open mode picker, currently [^\"\n]*Flash",
                str(verified.get("text", "")),
                re.I,
            ):
                return True
            await asyncio.sleep(self.poll_interval)
        return False

    async def _current_url(self, session_id: str) -> str:
        result = await self._run_json(
            "evaluate", "location.href", "--session", session_id, timeout=30
        )
        value = self._result_value(result)
        return str(value or "")

    async def _ensure_perplexity_standard_search(self, session_id: str) -> bool:
        expression = (
            "(() => { const buttons = [...document.querySelectorAll('button')]; "
            "const standard = buttons.find(button => "
            "/^(搜索|Search)$/i.test((button.innerText || '').trim())); "
            "if (!standard) return {found:false, active:false}; "
            "if (standard.getAttribute('aria-pressed') !== 'true') standard.click(); "
            "return {found:true, active:standard.getAttribute('aria-pressed') === 'true'}; })()"
        )
        for _ in range(3):
            result = await self._run_json(
                "evaluate", expression, "--session", session_id, timeout=30
            )
            state = self._result_value(result)
            if isinstance(state, dict) and state.get("found") and state.get("active"):
                return True
            await asyncio.sleep(self.poll_interval)
        return False

    async def _answer_page(self, session_id: str, selector: str) -> dict:
        expression = (
            "(() => { const nodes = [...document.querySelectorAll("
            + json.dumps(selector)
            + ")]; const bodyText = document.body ? (document.body.innerText || '') : ''; "
            "const buttons = [...document.querySelectorAll('button')]; "
            "return {answers:nodes.map(n => (n.innerText || '').trim()).filter(Boolean),"
            "answer_entries:nodes.map(n => ({text:(n.innerText || '').trim(),links:"
            "[...n.querySelectorAll('a[href]')].map(a => ({url:a.href,title:"
            "(a.innerText || a.textContent || '').trim() || null}))})).filter(entry => entry.text),"
            "answer_links:nodes.flatMap(n => [...n.querySelectorAll('a[href]')].map(a => "
            "({url:a.href,title:(a.innerText || a.textContent || '').trim() || null}))),"
            "links:[...new Set(nodes.flatMap(n => [...n.querySelectorAll('a[href]')].map(a => a.href)))],"
            "generating:buttons.some(b => /停止生成|Stop generating|Stop responding/i.test("
            "(b.getAttribute('aria-label') || '') + ' ' + (b.innerText || ''))),"
            "rate_limited:/429|rate limit|请求过于频繁|已达到免费搜索次数上限|使用权限将在几小时后重置|free searches limit|距离限制重置还剩|upgrade to supergrok/i.test(bodyText),"
            "page_text:bodyText}; })()"
        )
        result = await self._run_json("evaluate", expression, "--session", session_id, timeout=30)
        value = self._result_value(result)
        return value if isinstance(value, dict) else {"answers": [], "links": []}

    async def _collect_visible_sources(
        self,
        session_id: str,
        platform: str,
        answer_links: list[dict[str, str]],
    ) -> tuple[list[SourceRecord], SourceCaptureStatus, str | None]:
        config = PLATFORMS[platform]
        excluded_domains = frozenset(config["excluded_source_domains"])
        candidates: list[SourceRecord] = []
        for link in answer_links:
            record = self._source_record(
                link,
                role=SourceRole.CITED,
                origin=SourceEvidenceOrigin.ANSWER_DOM,
                excluded_domains=excluded_domains,
            )
            if record is not None:
                candidates.append(record)

        if config.get("answer_sources_only"):
            sources = merge_sources(candidates)
            return (
                sources,
                SourceCaptureStatus.CAPTURED
                if sources
                else SourceCaptureStatus.NONE_EXPOSED,
                None,
            )

        if platform == "gemini":
            try:
                cards = await self._collect_gemini_source_cards(session_id)
            except Exception as error:
                return merge_sources(candidates), SourceCaptureStatus.FAILED, str(error)
            for card in cards:
                record = self._source_record(
                    card,
                    role=SourceRole.SURFACED,
                    origin=SourceEvidenceOrigin.SOURCE_PANEL,
                    excluded_domains=excluded_domains,
                )
                if record is not None:
                    candidates.append(record)
            sources = merge_sources(candidates)
            return (
                sources,
                SourceCaptureStatus.CAPTURED
                if sources
                else SourceCaptureStatus.NONE_EXPOSED,
                None,
            )

        has_source_rule = bool(
            config["source_trigger_labels"] or config["source_trigger_selectors"]
        )
        if not has_source_rule:
            sources = merge_sources(candidates)
            if sources:
                return sources, SourceCaptureStatus.CAPTURED, None
            return (
                [],
                SourceCaptureStatus.UNSUPPORTED,
                f"visible source panel is not configured for {platform}",
            )

        open_state: dict | None = None
        open_error: Exception | None = None
        for _ in range(2):
            try:
                open_state = await self._open_source_panel(session_id, platform)
                open_error = None
                break
            except Exception as error:
                open_error = error
        if open_error is not None:
            return merge_sources(candidates), SourceCaptureStatus.FAILED, str(open_error)
        if not isinstance(open_state, dict) or not open_state.get("found"):
            sources = merge_sources(candidates)
            status = (
                SourceCaptureStatus.CAPTURED
                if sources
                else SourceCaptureStatus.NONE_EXPOSED
            )
            return sources, status, None
        if not open_state.get("opened"):
            return (
                merge_sources(candidates),
                SourceCaptureStatus.FAILED,
                str(open_state.get("diagnostic") or "source panel could not be opened"),
            )

        previous_cards: list[dict] | None = None
        stable_cards: list[dict] = []
        for _ in range(4):
            panel_page = await self._source_panel_page(session_id, platform)
            cards = list(panel_page.get("cards", [])) if isinstance(panel_page, dict) else []
            if previous_cards is not None and cards == previous_cards:
                stable_cards = cards
                break
            previous_cards = cards
            stable_cards = cards
            await asyncio.sleep(self.poll_interval)

        for card in stable_cards:
            record = self._source_record(
                card,
                role=SourceRole.SURFACED,
                origin=SourceEvidenceOrigin.SOURCE_PANEL,
                excluded_domains=excluded_domains,
            )
            if record is not None:
                candidates.append(record)
        sources = merge_sources(candidates)
        status = (
            SourceCaptureStatus.CAPTURED
            if sources
            else SourceCaptureStatus.NONE_EXPOSED
        )
        return sources, status, None

    async def _collect_gemini_source_cards(self, session_id: str) -> list[dict]:
        observation = await self._run_json(
            "observe", "--session", session_id, timeout=30
        )
        source_refs = [
            ref
            for ref, label in CONTROL_REF_PATTERN.findall(str(observation.get("text", "")))
            if "View source details" in label
        ]
        cards: list[dict] = []
        for index, source_ref in enumerate(source_refs):
            await self._run_json(
                "press", "Enter", "--ref", source_ref,
                "--session", session_id, timeout=30,
            )
            panel_page: dict = {"panel_found": False, "cards": []}
            for _ in range(4):
                panel_page = await self._source_panel_page(session_id, "gemini")
                if panel_page.get("panel_found"):
                    break
                await asyncio.sleep(self.poll_interval)
            if not panel_page.get("panel_found"):
                raise RuntimeError(f"Gemini source dialog {index + 1} did not open")
            cards.extend(list(panel_page.get("cards", [])))
            await self._run_json("press", "Escape", "--session", session_id, timeout=30)
        return cards

    @staticmethod
    def _source_record(
        candidate: dict,
        *,
        role: SourceRole,
        origin: SourceEvidenceOrigin,
        excluded_domains: frozenset[str],
    ) -> SourceRecord | None:
        normalized_url = normalize_source_url(
            str(candidate.get("url") or ""), excluded_domains=excluded_domains
        )
        if normalized_url is None:
            return None
        return SourceRecord(
            url=normalized_url,
            title=str(candidate.get("title") or "").strip() or None,
            domain=urlsplit(normalized_url).hostname or "",
            snippet=str(candidate.get("snippet") or "").strip() or None,
            source_role=role,
            evidence_origin=origin,
            linked_in_answer=role == SourceRole.CITED,
        )

    async def _open_source_panel(self, session_id: str, platform: str) -> dict:
        config = PLATFORMS[platform]
        expression = (
            "(() => { const selectors = "
            + json.dumps(list(config["source_trigger_selectors"]))
            + "; const labels = "
            + json.dumps(list(config["source_trigger_labels"]), ensure_ascii=False)
            + "; const answerSelector = "
            + json.dumps(config["answer_selector"])
            + "; const allowDocumentFallback = "
            + json.dumps(bool(config.get("document_source_fallback", True)))
            + "; const answers = [...document.querySelectorAll(answerSelector)]; "
            "const root = answers.at(-1) || document; let trigger = null; "
            "for (const selector of selectors) { trigger = root.querySelector(selector) || "
            "(allowDocumentFallback ? document.querySelector(selector) : null); if (trigger) break; } "
            "if (!trigger && labels.length) { const controls = [...root.querySelectorAll("
            "'button,[role=button],a,[onclick]'), ...(allowDocumentFallback ? document.querySelectorAll("
            "'button,[role=button],a,[onclick]') : [])]; trigger = controls.find(node => { const text = "
            "((node.getAttribute('aria-label') || '') + ' ' + (node.innerText || '')).trim(); "
            "return labels.some(label => text.includes(label)); }); } "
            "if (!trigger) return {found:false,opened:false}; "
            "try { trigger.click(); return {found:true,opened:true}; } "
            "catch (error) { return {found:true,opened:false,diagnostic:String(error)}; } })()"
        )
        result = await self._run_json(
            "evaluate", expression, "--session", session_id, timeout=30
        )
        value = self._result_value(result)
        return value if isinstance(value, dict) else {"found": False, "opened": False}

    async def _source_panel_page(self, session_id: str, platform: str) -> dict:
        config = PLATFORMS[platform]
        expression = (
            "(() => { const panelSelectors = "
            + json.dumps(list(config["source_panel_selectors"]))
            + "; const panelLabels = "
            + json.dumps(list(config["source_panel_labels"]), ensure_ascii=False)
            + "; const cardSelectors = "
            + json.dumps(list(config["source_card_selectors"]))
            + "; const urlAttributes = "
            + json.dumps(list(config["source_url_attributes"]))
            + "; let panels = panelSelectors.flatMap(selector => "
            "[...document.querySelectorAll(selector)]); if (!panels.length && panelLabels.length) { "
            "const labelled = [...document.querySelectorAll('h1,h2,h3,h4,[role=heading]')].filter("
            "node => panelLabels.some(label => (node.innerText || '').includes(label))); "
            "for (const heading of labelled) { let node = heading; for (let depth = 0; node && depth < 6; "
            "depth += 1, node = node.parentElement) { if (cardSelectors.some(selector => "
            "node.querySelector(selector))) { panels.push(node); break; } } } } "
            "panels = panels.filter(panel => panel.getClientRects().length && "
            "(!panelLabels.length || panelLabels.some(label => (panel.innerText || '').includes(label)))); "
            "if (!panels.length) return {panel_found:false,cards:[]}; const cards = panels.flatMap(panel => "
            "cardSelectors.flatMap(selector => [...panel.querySelectorAll(selector)])); "
            "return {panel_found:true,cards:cards.map(card => { const anchor = "
            "card.matches('a[href]') ? card : card.querySelector('a[href]'); const urlNode = "
            "card.matches('[href],[data-url]') ? card : card.querySelector('[href],[data-url]'); "
            "const url = urlAttributes.map(attribute => attribute === 'href' ? urlNode?.href : "
            "urlNode?.getAttribute(attribute)).find(Boolean) || (anchor ? anchor.href : ''); return {"
            "url, title:((card.querySelector('h1,h2,h3,h4,[role=heading]')"
            " || anchor || card).innerText || '').trim() || null, snippet:(card.innerText || '')"
            ".trim() || null}; })}; })()"
        )
        result = await self._run_json(
            "evaluate", expression, "--session", session_id, timeout=30
        )
        value = self._result_value(result)
        return value if isinstance(value, dict) else {"panel_found": False, "cards": []}

    @staticmethod
    def _result_value(result):
        if not isinstance(result, dict):
            return result
        if "value" in result:
            return result["value"]
        nested = result.get("result")
        if isinstance(nested, dict) and "value" in nested:
            return nested["value"]
        return nested if nested is not None else result

    async def stop(self, session_id: str) -> None:
        try:
            await self._run_json("session", "stop", session_id)
        finally:
            self._session_tabs.pop(session_id, None)

    @staticmethod
    def _find_textbox_ref(
        page_text: str, expected_label: str | tuple[str, ...]
    ) -> str | None:
        expected = (expected_label,) if isinstance(expected_label, str) else expected_label
        for ref, label in REF_PATTERN.findall(page_text):
            if label in expected:
                return ref
        return None

    @staticmethod
    def _find_button_ref(page_text: str, expected_labels: tuple[str, ...]) -> str | None:
        for ref, label in BUTTON_REF_PATTERN.findall(page_text):
            if label in expected_labels:
                return ref
        return None

    @staticmethod
    def _find_control_ref(page_text: str, label_fragments: tuple[str, ...]) -> str | None:
        for ref, label in CONTROL_REF_PATTERN.findall(page_text):
            if any(fragment in label for fragment in label_fragments):
                return ref
        return None


class BrowserSkillAdapter(ProbeAdapter):
    name = "browser_skill"

    def __init__(self, *, client, artifact_root: Path, default_timeout: float = 180.0):
        self.client = client
        self.artifact_root = Path(artifact_root)
        self.default_timeout = default_timeout
        self.operation_lock = BrowserOperationLock(
            self.artifact_root.parent / ".browser_skill.lock"
        )

    async def health(self, platform: str) -> AdapterHealth:
        if platform == "chatgpt" and isinstance(self.client, BskCliClient) and not self.client.browser_instance_id:
            return AdapterHealth(False, "ChatGPT sampling suspended: configure a dedicated LOOK4GEO_CHATGPT_BROWSER_ID distinct from LOOK4GEO_BROWSER_ID")
        return AdapterHealth(platform in PLATFORMS, "configured" if platform in PLATFORMS else "unsupported")

    async def login(self, platform: str) -> dict:
        return {
            "status": "user_action_required",
            "platform": platform,
            "instructions": f"Complete login at {PLATFORMS[platform]['url']} in the visible Agent Window.",
        }

    async def run(self, platform: str, request: ProbeRequest) -> PlatformAttempt:
        async with self.operation_lock:
            return await self._run_exclusive(platform, request)

    async def _run_exclusive(
        self, platform: str, request: ProbeRequest
    ) -> PlatformAttempt:
        if platform not in PLATFORMS:
            return PlatformAttempt(
                platform=platform,
                adapter=self.name,
                status=JobStatus.FAILED,
                diagnostic=f"unsupported platform: {platform}",
            )
        session_id: str | None = None
        locale_verified: bool | None = None
        normalized = normalize_for_browser(request.prompt)
        try:
            total_timeout = float(
                request.options.get(
                    "timeout",
                    PLATFORMS[platform].get("default_timeout", self.default_timeout),
                )
            )
            max_attempts = 3 if platform == "gemini" else 1
            retryable = {FailureKind.EXTRACTION_FAILED, FailureKind.TIMEOUT}
            deadline = asyncio.get_running_loop().time() + total_timeout
            for attempt_index in range(max_attempts):
                attempt_timeout = (
                    deadline - asyncio.get_running_loop().time()
                    if platform == "gemini"
                    else total_timeout
                )
                if attempt_timeout <= 0:
                    output = BrowserProbeOutput(
                        failure=FailureKind.TIMEOUT,
                        diagnostic=f"timeout after {total_timeout:g}s total budget",
                    )
                    break
                session_id = await self.client.start(platform)
                locale_verified = None
                if isinstance(self.client, BskCliClient) and platform == "chatgpt":
                    self.client._buyer_locales[session_id] = request.buyer_baseline.locale
                try:
                    output = await self.client.probe(
                        session_id,
                        platform,
                        normalized.sent,
                        attempt_timeout,
                    )
                except asyncio.TimeoutError:
                    output = BrowserProbeOutput(
                        failure=FailureKind.TIMEOUT,
                        diagnostic="timeout",
                    )
                finally:
                    if isinstance(self.client, BskCliClient):
                        locale_verified = self.client._locale_verified.pop(session_id, None)
                        self.client._buyer_locales.pop(session_id, None)
                    try:
                        await self.client.stop(session_id)
                    except Exception:
                        pass
                    session_id = None
                if (
                    output.failure not in retryable
                    or attempt_index == max_attempts - 1
                ):
                    break
            if output.login_required:
                return PlatformAttempt(
                    platform=platform,
                    adapter=self.name,
                    status=JobStatus.WAITING_FOR_LOGIN,
                    diagnostic=output.diagnostic or "login required",
                    failure=FailureKind.LOGIN_REQUIRED,
                    query_original=normalized.original,
                    query_sent=normalized.sent,
                    query_normalized=normalized.changed,
                )
            if output.failure is not None:
                return PlatformAttempt(
                    locale_verified=locale_verified,
                    platform=platform,
                    adapter=self.name,
                    status=JobStatus.FAILED,
                    diagnostic=output.diagnostic or output.failure.value,
                    failure=output.failure,
                    query_original=normalized.original,
                    query_sent=normalized.sent,
                    query_normalized=normalized.changed,
                )
            return PlatformAttempt(
                locale_verified=locale_verified,
                platform=platform,
                adapter=self.name,
                status=JobStatus.SUCCEEDED,
                raw_answer=output.answer,
                normalized_answer=output.answer.strip(),
                citations=citations_from_sources(output.sources),
                sources=output.sources,
                source_capture_status=output.source_capture_status,
                source_capture_diagnostic=output.source_capture_diagnostic,
                query_original=normalized.original,
                query_sent=normalized.sent,
                query_normalized=normalized.changed,
            )
        except asyncio.TimeoutError:
            return PlatformAttempt(
                platform=platform,
                adapter=self.name,
                status=JobStatus.FAILED,
                diagnostic="timeout",
                failure=FailureKind.TIMEOUT,
                query_original=normalized.original,
                query_sent=normalized.sent,
                query_normalized=normalized.changed,
            )
        except Exception as error:
            diagnostic = str(error)
            failure = FailureKind.UNKNOWN
            if (
                platform in {"gemini", "chatgpt"}
                and "submission state did not change" in diagnostic.casefold()
            ):
                failure = FailureKind.SEND_FAILED
            return PlatformAttempt(
                platform=platform,
                adapter=self.name,
                status=JobStatus.FAILED,
                diagnostic=diagnostic,
                failure=failure,
                query_original=normalized.original,
                query_sent=normalized.sent,
                query_normalized=normalized.changed,
            )
        finally:
            if session_id is not None:
                try:
                    await self.client.stop(session_id)
                except Exception:
                    pass

    async def cancel(self, job_id: str) -> None:
        return None
