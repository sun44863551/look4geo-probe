from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

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

# 2026-11-01: bsk >= 0.4.0 (protocol 1.7) 起，下列「写操作」必须带 cooperate 上下文。
# 读操作（navigate / observe / snapshot / get-html / browsers / session start）不在其列。
_CONTEXT_REQUIRED = frozenset({
    "click",
    "evaluate",
    "fill",
    "press",
    "select",
    "upload",
    "reload",
    "hover",
    "scroll-to",
    "wheel",
    "drag",
    "focus",
    "blur",
    "navigate-back",
    "navigate-forward",
    # 2026-11-01 实测补充：`emulate`（UA / Accept-Language 注入）同样需要上下文，
    # 漏掉它会让 ChatGPT 的 locale 注入整段失败（`permission_denied`）。
    "emulate",
    "dialog",
    "tab",
    "window",
    # 2026-11-01 实测：**只有新窗口的第一次导航**免上下文；此后每次 `navigate` 都要上下文。
    # 因此把 navigate 也纳入：拿不到上下文时（窗口还是空白页）仍按原样执行，第一次必然成功。
    "navigate",
})

# 2026-11-06: 0.4.2 只要求「脚本 / 未定向按键 / 视觉点击」带顶层 observation_id；
# **定向**动作（带 --selector / --ref）复用 context 即可 —— 强行带观测会在页面滚动/流式输出时
# 报 `The strict observation changed before this action`。
_NEEDS_OBSERVATION = frozenset({"evaluate", "emulate", "navigate"})
_NEEDS_OBSERVATION_UNTARGETED = frozenset({"press", "click"})


class BrowserControlInterrupted(Exception):
    """Do not fall back to another input action when ownership/effect is uncertain."""


def _control_interruption(raw: bytes, message: str) -> bool:
    try:
        payload = json.loads(raw)
        details = payload.get("data") or {}
        reason = str(details.get("reason") or payload.get("code") or "")
        if details.get("effect_state") in {"unknown", "applied"} or reason in {
            "user_aborted", "user_activity_revoked", "human_in_control", "user_intervened",
        }:
            return True
    except (ValueError, AttributeError, TypeError):
        pass
    return bool(re.search(r"user activity revoked|user_aborted|user aborted|human is in control", message, re.I))


async def _communicate_bsk(process: asyncio.subprocess.Process, timeout: float) -> tuple[bytes, bytes]:
    try:
        return await asyncio.wait_for(process.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        # Reap only the CLI child that this operation owns, not the daemon.
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.communicate()
        raise

# 上下文失效 / 页面自行变化 / 扩展瞬时抖动 —— 这三类都值得重新 sync 后重放一次。
_RETRYABLE_RE = re.compile(
    r"requires the context|observed page or control|observed content changed"
    r"|synchronize|Receiving end does not exist"
    # 2026-11-06: bsk 0.4.2（扩展 protocol 1.8）新增「严格观测」语义 ——
    # 每次写动作都要一次**未使用**的 cooperate sync，脚本类动作还要显式带 observation_id。
    r"|unused strict observation|valid control context"
    r"|strict observation changed|page frame changed"
    r"|observation changed before this action",
    re.IGNORECASE,
)

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
        "composer_selector": '[data-testid="chat_input_input"] .tiptap[contenteditable="true"]',
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
        # 2026-10-10 (CG-14): the composer is no longer a `#prompt-textarea` textarea —
        # ChatGPT now renders a ProseMirror `div[contenteditable]` with no id. Keep the
        # legacy selector first, then fall back to the current structures.
        "composer_selector": '#prompt-textarea, div.ProseMirror[contenteditable="true"], div[contenteditable="true"][role="textbox"]',
        "login_markers": ("登录", "注册"),
        "conversation_marker": "/c/",
        # 2026-10-10 (CG-14): assistant turns lost `data-message-author-role` and the
        # `.markdown` class; the answer body is now the (unhashed) semantic hook
        # `data-markdown-text-style="assistant-message"`. Keep the legacy selectors
        # last for backward compatibility.
        "answer_selector": '[data-markdown-text-style="assistant-message"], [data-message-author-role="assistant"] [data-dil-widget-copy-target], [data-message-author-role="assistant"] .markdown',
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
        "textbox": (
            "输入消息",
            "有问题尽管问我",
            "给元宝发送消息",
            "Ask me anything. Use Shift+Enter for a new line.",
        ),
        "composer_selector": 'textarea, div[role="textbox"], div[contenteditable="true"], div[data-slate-editor="true"]',
        "login_markers": ("登录", "微信扫码登录", "上次登录"),
        "blocking_login_markers": (
            "微信登录",
            "请使用微信扫描二维码登录",
            "Log In with WeChat",
            "Please scan the QR code",
        ),
        "conversation_marker": "/chat/",
        "answer_selector": '[data-role="assistant"], [class*="assistant"], [class*="markdown"], [class*="answer"]',
    },
    "baidu": {
        "url": "https://chat.baidu.com/",
        "textbox": (),
        "composer_selector": "textarea.ci-textarea",
        "login_markers": ("请登录", "登录同步历史对话"),
        "conversation_marker": "/search/",
        "answer_selector": ".ai-entry",
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
        "source_trigger_labels": ("来源", "参考资料", "参考", "篇资料", "网页"),
        "source_trigger_selectors": ('[data-testid*="source"]',),
        "source_panel_selectors": ('[role="dialog"]',),
        "source_panel_labels": ("来源", "参考资料", "篇资料"),
        "inline_source_summary": True,
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
        "source_trigger_labels": ("Sources", "引用来源"),
        "source_trigger_selectors": ('[data-toolbar-type="citation"]',),
        "source_panel_selectors": (".agent-dialogue-references",),
        "source_panel_labels": ("Reference Source", "引用来源"),
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
    r"(@e\d+)\s+(?:button|menuitem(?:radio)?)\s+\"([^\"]+)\""
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
    "too many requests",
    "问答过频",
    "暂时限制你访问对话记录",
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
    "我只会生成文本，你提出的问题超出了我的程序逻辑范畴",
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
    "请选择所有符合上文描述的图片",
    "并拖拽到下方",
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
    conversation_url: str | None = None
    answer: str = ""
    citations: list[str] = field(default_factory=list)
    sources: list[SourceRecord] = field(default_factory=list)
    source_capture_status: SourceCaptureStatus = SourceCaptureStatus.NONE_EXPOSED
    source_capture_diagnostic: str | None = None
    login_required: bool = False
    failure: FailureKind | None = None
    diagnostic: str | None = None
    retryable: bool = True


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
    if platform in {"perplexity", "yuanbao", "baidu"}:
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
        platform in {"chatgpt", "doubao", "yuanbao"}
        and len(answer) < 300
        and bool(PREAMBLE_PATTERN.match(answer))
    )


def is_attachment_only_answer(platform: str, answer: str) -> bool:
    """An exported file name is not a usable conversational answer."""
    return (
        platform == "yuanbao"
        and len(answer.strip()) < 100
        and bool(re.search(r"\.(?:xlsx?|csv|pdf|docx?|pptx?)\s*$", answer.strip(), re.I))
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

    async def browser_health(self) -> AdapterHealth:
        try:
            browsers = await self._run_json("browsers", timeout=10)
        except (RuntimeError, asyncio.TimeoutError) as error:
            return AdapterHealth(False, str(error))
        connected = isinstance(browsers, list) and any(
            item.get("instance_id") == self.browser_instance_id
            and not item.get("unresponsive")
            for item in browsers
        )
        return AdapterHealth(
            connected,
            "connected" if connected else f"browser {self.browser_instance_id} is not connected; open its dedicated profile",
        )

    def _args_with_pinned_tab(self, args: tuple[str, ...]) -> tuple[str, ...]:
        if args[:1] == ("tab",) or "--tab-id" in args or "--session" not in args:
            return args
        session_index = args.index("--session") + 1
        if session_index >= len(args):
            return args
        tab_id = self._session_tabs.get(args[session_index])
        return args + ("--tab-id", str(tab_id)) if tab_id is not None else args

    # ---- 2026-11-01: bsk >= 0.4.0 (protocol 1.7) 兼容层 ----
    # 新协议下所有「写操作」都必须携带 cooperate 上下文（--context），
    # 该上下文由 `bsk cooperate --session S sync` 返回，且**每次只授权一个动作**。
    # 本层只补协议握手，不触碰题面 / 有效性闸门 / 提及判据 / locale 注入，故不影响与基线的可比性。
    async def _cooperate_context(
        self, session_id: str, timeout: float, tab_id: int | None = None
    ) -> tuple[str, str]:
        """取一次 cooperate 上下文；任何失败都返回空串（退化为旧行为，不阻断）。

        2026-11-01 实测两点：
        1. `bsk cooperate sync` **支持 `--tab-id`** —— 上下文必须与后续动作指向**同一个 tab**，
           否则动作会报 `This action requires the context returned by the latest cooperate sync`。
        2. sync 本身也会因「页面还在加载 / 观察被取代」短暂失败
           （`This context belongs to a different or superseded observation`）⇒ 原地重试 2–3 次。
        """
        base = ["bsk", "cooperate", "--session", session_id, "sync"]
        if tab_id is not None:
            base += ["--tab-id", str(tab_id)]
        for attempt in range(3):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *base,
                    "--json",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out, _ = await _communicate_bsk(proc, min(timeout, 30.0))
                if proc.returncode == 0:
                    data = json.loads(out.decode("utf-8"), strict=False)
                    state = data.get("state") or {}
                    if data.get("ready") is False or state.get("mode") == "human":
                        raise BrowserControlInterrupted("Browser control not ready; wait for hand-back before continuing")
                    context_id = str(data.get("context_id") or "")
                    if context_id:
                        return context_id, str(data.get("observation_id") or "")
                elif _control_interruption(out, command_error_detail(out, b"")):
                    raise BrowserControlInterrupted(command_error_detail(out, b""))
            except BrowserControlInterrupted:
                raise
            except (OSError, asyncio.TimeoutError, ValueError):
                pass
            if attempt < 2:
                await asyncio.sleep(1.2)
        return "", ""

    async def _run_json(self, *args: str, timeout: float = 30.0) -> dict | list:
        navigation_host = urlsplit(args[1]).hostname if len(args) >= 2 and args[0] == "navigate" else None
        if ("--wait-until" not in args
                and navigation_host in {"chatgpt.com", "gemini.google.com", "www.perplexity.ai"}):
            # The caller separately verifies the composer and mode; unrelated
            # network resources need not finish before those checks begin.
            args += ("--wait-until", "domcontentloaded")
        args = self._args_with_pinned_tab(args)
        mutating = bool(args) and args[0] in _CONTEXT_REQUIRED and "--context" not in args and args[:2] != ("tab", "list")
        # 2026-11-01: 流式回答期间页面每帧都在变，`evaluate` 的整页校验几乎必然失效 ⇒
        # 只读型 evaluate 给更大的重试预算（8 次）；其余写命令保持 3 次，避免重复提交。
        attempts = 8 if (bool(args) and args[0] == "evaluate") else 3
        last_error = ""
        for attempt in range(attempts):
            call = args
            if mutating and "--session" in call:
                session_id = call[call.index("--session") + 1]
                control_tab = self._session_tabs.get(session_id)
                task_tab_action = call[:2] in {("tab", "close"), ("tab", "select")}
                if task_tab_action and len(call) > 2 and call[2].isdigit():
                    control_tab = int(call[2])
                context_id, observation_id = await self._cooperate_context(
                    session_id, timeout, control_tab
                )
                if context_id:
                    call = call + ("--context", context_id)
                    # 2026-11-06: 脚本/键盘/视觉点击类动作必须显式带顶层 observation_id
                    positional_click_target = call[0] == "click" and len(call) > 1 and not call[1].startswith("--")
                    untargeted = not (positional_click_target or "--selector" in call or "--ref" in call)
                    if observation_id and (
                        call[0] in _NEEDS_OBSERVATION
                        or task_tab_action
                        or (call[0] in _NEEDS_OBSERVATION_UNTARGETED and untargeted)
                    ):
                        call = call + ("--observation", observation_id)
            process = await asyncio.create_subprocess_exec(
                "bsk",
                *call,
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await _communicate_bsk(process, timeout)
            if os.environ.get("LOOK4GEO_BSK_TRACE"):
                try:
                    with open(os.environ["LOOK4GEO_BSK_TRACE"], "a", encoding="utf-8") as fh:
                        fh.write(json.dumps({
                            "cmd": list(call),
                            "rc": process.returncode,
                            "err": command_error_detail(stdout, stderr)[:400],
                        }, ensure_ascii=False) + "\n")
                except Exception:
                    pass
            if process.returncode != 0:
                last_error = command_error_detail(stdout, stderr)
                if _control_interruption(stdout, last_error) or _control_interruption(stderr, last_error):
                    raise BrowserControlInterrupted(last_error)
                # 上下文失效 / 页面自行变化 / 扩展瞬时抖动 ⇒ 重新 sync 后重放同一动作
                navigation_disconnected = (
                    navigation_host == "chatgpt.com" and "net::ERR_CONNECTION_CLOSED" in last_error
                )
                observation_invalidated = (
                    args[0] in {"observe", "evaluate"}
                    and "document changed during observation; observe again" in last_error.casefold()
                )
                if attempt < attempts - 1 and (
                    _RETRYABLE_RE.search(last_error) or navigation_disconnected or observation_invalidated
                ):
                    await asyncio.sleep(1.2)
                    continue
                raise RuntimeError(last_error)
            return json.loads(stdout.decode("utf-8"))
        raise RuntimeError(last_error or "bsk command failed")

    async def start(self, platform: str) -> str:
        if not self.browser_instance_id:
            raise RuntimeError(
                "ChatGPT sampling is suspended: configure LOOK4GEO_CHATGPT_BROWSER_ID "
                "from the probe Chrome profile. No daily-browser fallback is allowed."
            )
        args = ["session", "start", "--browser", self.browser_instance_id]
        result = await self._run_json(*args)
        session_id = str(result["session_id"])
        if platform != "chatgpt":
            return session_id
        # 2026-11-01: bsk 0.4.0 起的 Agent Window 初始页没有扩展内容脚本
        # （`evaluate` 直接报 `Could not establish connection. Receiving end does not exist.`）。
        # ChatGPT 分支在 start 之后**立刻**要 `evaluate navigator.userAgent`（locale 注入），
        # 所以必须先把 tab 落到真实站点上。这里只做"落到目标域"，随后 probe() 仍按原逻辑导航。
        # 2026-11-01 补：首次导航偶发 `net::ERR_CONNECTION_CLOSED`（网络瞬断），原地重试 3 次。
        try:
            navigation = await self._run_json(
                "navigate", PLATFORMS[platform]["url"], "--session", session_id, timeout=60.0,
            )
            if isinstance(navigation, dict) and navigation.get("tab_id") is not None:
                self._session_tabs[session_id] = int(navigation["tab_id"])
        except BaseException:
            try:
                await self.stop(session_id)
            except Exception:
                pass
            raise
        return session_id

    async def probe(
        self, session_id: str, platform: str, prompt: str, timeout: float
    ) -> BrowserProbeOutput:
        config = PLATFORMS[platform]
        url = config["url"]
        navigation_wait = ()
        if platform == "gemini":
            account_slot = os.environ.get("LOOK4GEO_GEMINI_ACCOUNT_SLOT", "").strip()
            if account_slot:
                if not re.fullmatch(r"[0-9]+", account_slot):
                    raise RuntimeError("LOOK4GEO_GEMINI_ACCOUNT_SLOT must be a numeric Google account slot")
                url = f"https://gemini.google.com/u/{account_slot}/app"
        if platform == "chatgpt":
            await self._set_chatgpt_english_locale(session_id)
            navigation_wait = ("--wait-until", "commit")
        navigation = await self._run_json(
            "navigate", url, *navigation_wait, "--session", session_id, timeout=timeout
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
        if platform == "chatgpt" and page_is_rate_limited({"page_text": page_text}):
            return BrowserProbeOutput(failure=FailureKind.RATE_LIMITED, diagnostic=rate_limit_diagnostic({"page_text": page_text}))
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
            mode_confirmed = False
            for _ in range(3):
                try:
                    mode_confirmed = await self._ensure_gemini_mode(session_id)
                except RuntimeError:
                    mode_confirmed = False
                if mode_confirmed:
                    break
                await asyncio.sleep(self.poll_interval)
            if not mode_confirmed:
                return BrowserProbeOutput(
                    failure=FailureKind.SEND_FAILED,
                    diagnostic="Configured Gemini mode could not be confirmed",
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
        saw_attachment_only_answer = False
        gemini_reply_announced = False
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(self.poll_interval)
            page = await self._answer_page(session_id, config["answer_selector"])
            if platform == "doubao":
                observation = await self._run_json("observe", "--session", session_id, timeout=30)
                if page_requires_human_verification({"page_text": observation.get("text", "")}):
                    return BrowserProbeOutput(
                        login_required=True,
                        diagnostic="Doubao requires manual picture verification in its browser profile",
                    )
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
                # Preserve only the answer delta for this submission, never old history.
                captured = select_main_answer(platform, [
                    text for text in answers_after_baseline(
                        [str(value) for value in page.get("answers", [])], baseline
                    ) if not is_prompt_echo(text, prompt)
                ]) if platform == "chatgpt" else ""
                try:
                    conversation_url = await self._current_url(session_id)
                except Exception:
                    conversation_url = None
                return BrowserProbeOutput(
                    answer=captured,
                    conversation_url=conversation_url,
                    source_capture_status=SourceCaptureStatus.FAILED,
                    source_capture_diagnostic="Rate limit interrupted capture; visible text is unverified evidence, not a complete sample",
                    failure=FailureKind.RATE_LIMITED,
                    diagnostic=rate_limit_diagnostic(page),
                )
            if platform == "chatgpt" and page.get("account_changed"):
                return BrowserProbeOutput(
                    failure=FailureKind.SEND_FAILED,
                    diagnostic="ChatGPT reports the account changed during submission; confirm the sampling account before retrying",
                    retryable=False,
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
            if platform == "gemini":
                gemini_reply_announced |= bool(re.search(
                    r"(?im)^\s*(?:Gemini replied|Gemini 已回复|Gemini 已回答)\s*$",
                    page_text,
                ))
            if not current_text:
                continue
            if platform == "gemini":
                current_text = collapse_overlapping_stream_prefix(current_text)
            if is_attachment_only_answer(platform, current_text):
                saw_attachment_only_answer = True
                previous = current_text
                stable_rounds = 0
                continue
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
            if page.get("generating") or (
                platform == "chatgpt" and page.get("completion_confirmed") is False
            ):
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
                    try:
                        conversation_url = await self._current_url(session_id)
                    except Exception:
                        conversation_url = None
                    return BrowserProbeOutput(
                        conversation_url=conversation_url,
                        answer=current_text,
                        citations=[citation.url for citation in citations_from_sources(sources)],
                        sources=sources,
                        source_capture_status=source_status,
                        source_capture_diagnostic=source_diagnostic,
                    )
            else:
                stable_rounds = 0
            previous = current_text
        if gemini_reply_announced and not saw_answer_candidate:
            try:
                conversation_url = await self._current_url(session_id)
            except RuntimeError:
                conversation_url = None
            return BrowserProbeOutput(
                conversation_url=conversation_url,
                failure=FailureKind.EXTRACTION_FAILED,
                diagnostic="Gemini announced a reply but no answer appeared before timeout",
                source_capture_status=SourceCaptureStatus.FAILED,
                source_capture_diagnostic="No visible answer to associate with sources",
                retryable=False,
            )
        if saw_answer_candidate:
            return BrowserProbeOutput(
                failure=FailureKind.EXTRACTION_FAILED,
                diagnostic=(
                    "platform returned only an attachment, not a text answer"
                    if saw_attachment_only_answer
                    else "new answer candidates never became valid and stable"
                ),
            )
        return BrowserProbeOutput(
            failure=FailureKind.TIMEOUT,
            diagnostic=f"no new answer matched {config['answer_selector']} within {timeout:g}s",
        )

    async def _enter_prompt(
        self,
        session_id: str,
        platform: str,
        textbox_ref: str | None,
        prompt: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        if platform == "chatgpt":
            selector = PLATFORMS["chatgpt"]["composer_selector"]
            # 2026-11-06: bsk 0.4.2（扩展 protocol 1.8）引入 strict shared-control ——
            # 对 composer 的**真实鼠标点击**会被判定为人工接管（mode=human），
            # 之后所有写动作一律 `User activity revoked this action`；
            # 用 `evaluate` + execCommand 注入文本也会被记成 human input。
            # 唯一稳定且不改题面的路径：URL `?q=` 预填（页面自身写入 composer）+ press Enter。
            # 文本仍是逐字原文（仅 URL 编码），提交动作是键盘事件，不会被判为人工。
            prefill_url = "https://chatgpt.com/?q=" + quote(prompt, safe="")
            for _ in range(3):
                await self._run_json(
                    "navigate", prefill_url, "--wait-until", "domcontentloaded",
                    "--session", session_id, timeout=timeout,
                )
                await asyncio.sleep(self.poll_interval + 1.5)
                if (await self._composer_text(session_id, selector)).strip() == prompt.strip():
                    return
            raise RuntimeError("ChatGPT prompt entry via ?q= prefill could not be verified")
        if platform == "chatgpt_legacy_exec_command":
            selector = PLATFORMS["chatgpt"]["composer_selector"]
            # CG-14 (2026-10-10): the composer is now a ProseMirror `div[contenteditable]`,
            # and `bsk fill` is unreliable against it — its default clear-first pass trips
            # "the fill target changed during the action" (React rebuilds the editable node),
            # while `--no-clear` appends instead of replacing (residue then breaks the exact
            # match) and its own confirmation can fail even when the text landed. Type through
            # `evaluate` + `execCommand` instead: focus, select all, insert, then verify by
            # reading the composer. That is the same deterministic path used for other
            # contenteditable platforms below.
            expression = (
                "(() => { const nodes = [...document.querySelectorAll("
                + json.dumps(selector)
                + ")]; const target = nodes.find(node => node.getClientRects().length); "
                "if (!target) return ''; target.focus(); "
                "document.execCommand('selectAll', false, null); "
                "document.execCommand('insertText', false, "
                + json.dumps(prompt)
                + "); target.dispatchEvent(new InputEvent('input', {bubbles:true,"
                "inputType:'insertText',data:" + json.dumps(prompt) + "})); "
                "return target.innerText || target.textContent || ''; })()"
            )
            await asyncio.sleep(2.0)  # 让首屏渲染稳定，避免 click 撞上 "page frame changed"
            for _ in range(3):
                # Gemini 首页有持续动画，strict observation 极易被刷掉；
                # 定向 click 自身带 3 次重试仍可能全灭，这里再加一层外层重试。
                last_error: Exception | None = None
                for _try in range(4):
                    try:
                        await self._run_json(
                            "click", selector, "--session", session_id, timeout=timeout
                        )
                        last_error = None
                        break
                    except RuntimeError as exc:
                        last_error = exc
                        if "observation" not in str(exc) and "frame changed" not in str(exc):
                            raise
                        await asyncio.sleep(1.0)
                if last_error is not None:
                    raise last_error
                await self._run_json(
                    "evaluate", expression, "--session", session_id, timeout=timeout
                )
                if (await self._composer_text(session_id, selector)).strip() == prompt.strip():
                    return
                await asyncio.sleep(self.poll_interval)
            raise RuntimeError("ChatGPT prompt entry could not be verified after 3 attempts")
        if platform == "doubao":
            selector = PLATFORMS[platform]["composer_selector"]
            expression = (
                "(() => { const nodes = [...document.querySelectorAll("
                + json.dumps(selector)
                + ")]; const target = nodes.find(n => n.getClientRects().length); "
                "if (!target) return ''; target.focus(); "
                "const range = document.createRange(); range.selectNodeContents(target); "
                "const selection = window.getSelection(); selection.removeAllRanges(); "
                "selection.addRange(range); document.execCommand('insertText', false, "
                + json.dumps(prompt)
                + "); return target.innerText || ''; })()"
            )
            for _ in range(3):
                await self._dismiss_blocking_overlays(session_id, platform)
                await self._run_json("evaluate", expression, "--session", session_id, timeout=timeout)
                # The editor can be replaced during initial hydration. Read the
                # current node, not the detached node used by insertText.
                if (await self._composer_text(session_id, selector)).strip() == prompt.strip():
                    return
                await asyncio.sleep(self.poll_interval)
            raise RuntimeError("Doubao prompt entry could not be verified after 3 attempts")
        if platform == "gemini":
            selector = PLATFORMS[platform]["composer_selector"]
            # 2026-11-07: Gemini 前端换成 Quill 编辑器（`ql-editor`）。
            # 直接 `fill` 只写 DOM、不进 Quill 模型 —— 回车与发送按钮都是空转（no-op），
            # 页面停在草稿态。实测唯一稳定序列：
            #   ① 真点一次编辑器让 Quill 取得焦点（不会触发 shared-control 的 human 接管）
            #   ② fill 写入文本
            #   ③ 打一个 space 再 Backspace —— 用真实按键触发 Quill 的 input 同步
            #   ④ 校验 innerText 与题面逐字相等后才返回，交给 _submit_prompt 按 Enter
            for _ in range(3):
                await self._run_json(
                    "click", selector, "--session", session_id, timeout=timeout
                )
                await asyncio.sleep(0.6)
                await self._run_json(
                    "fill", "--selector", selector, "--value", prompt,
                    "--session", session_id, timeout=timeout,
                )
                await asyncio.sleep(0.4)
                await self._run_json(
                    "press", "space", "--selector", selector,
                    "--session", session_id, timeout=timeout
                )
                await asyncio.sleep(0.25)
                await self._run_json(
                    "press", "Backspace", "--selector", selector,
                    "--session", session_id, timeout=timeout
                )
                await asyncio.sleep(0.25)
                if (await self._composer_text(session_id, selector)).strip() == prompt.strip():
                    return
                await asyncio.sleep(self.poll_interval)
            raise RuntimeError("Gemini Quill prompt entry could not be verified")
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
        markers = ("下载豆包电脑版", "下载电脑版 使用完整功能") if platform == "doubao" else ("全新上线任务模式",)
        expression = (
            "(() => { const markers = " + json.dumps(markers, ensure_ascii=False) + "; "
            "const candidates = [...document.querySelectorAll('[role=dialog],body > div')]; "
            "const promo = candidates.find(node => markers.some(marker => (node.innerText || '').includes(marker))); "
            "if (!promo) return false; const controls = [...promo.querySelectorAll('button')]; "
            "const close = controls.find(node => /^(关闭|close|下次提醒我|暂不体验|稍后再说)$/i.test("
            "node.getAttribute('aria-label') || (node.innerText || '').trim())); if (!close) return false; "
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
        if platform == "doubao":
            await self._dismiss_blocking_overlays(session_id, platform)
            observation = await self._run_json("observe", "--session", session_id, timeout=timeout)
            send_ref = self._find_button_ref(str(observation.get("text", "")), ("Chat input send", "发送"))
            if not send_ref:
                raise RuntimeError("Doubao send button is unavailable after prompt entry")
            await self._run_json(
                "click", send_ref,
                "--session", session_id, timeout=timeout,
            )
            return
        if platform == "gemini":
            state_expression = (
                "(() => { const composers = [...document.querySelectorAll(" +
                json.dumps(PLATFORMS["gemini"]["composer_selector"]) +
                ")]; const composer = composers.find(node => node.getClientRects().length); "
                "const text = composer ? (composer.innerText || composer.textContent || '') : ''; "
                "const stop = [...document.querySelectorAll('button')].some(button => "
                "/stop/i.test((button.getAttribute('aria-label') || '') + ' ' + "
                "(button.innerText || ''))); const conversation = /^\\/(?:u\\/\\d+\\/)?app\\/.+/.test(location.pathname); "
                "const answer = document.querySelector('message-content .markdown'); "
                "const userQuery = document.querySelector('user-query, .query-text, .user-query'); "
                "const platformError = (document.body?.innerText || '').match("
                "/Something went wrong(?:\\s*\\(\\d+\\))?|出了点问题|出错了/i); "
                "return {submitted: stop || conversation || Boolean(answer) || Boolean(userQuery) || Boolean(composer && !text.trim()), "
                "platform_error:platformError?.[0] || null, "
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
                            "press", "Enter", "--selector",
                            PLATFORMS[platform]["composer_selector"],
                            "--session", session_id, timeout=timeout,
                        )
                    else:
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
                    if isinstance(state, dict) and state.get("platform_error"):
                        raise RuntimeError("Gemini platform error: " + str(state["platform_error"]))
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
            before = await self._composer_text(
                session_id, PLATFORMS["chatgpt"]["composer_selector"]
            )
            if not before.strip():
                raise RuntimeError("ChatGPT prompt entry could not be verified before submission")
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
                        # CG-14: the composer's send control lost its `data-testid`;
                        # it is now an unlabelled-by-testid button whose aria-label is
                        # locale dependent. Match by testid first, then by aria-label.
                        await self._run_json(
                            "click",
                            '[data-testid="send-button"], button[aria-label="发送"], '
                            'button[aria-label="Send"], button[aria-label*="送信"]',
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
                "click",
                'button[aria-label="Submit"], button[type="submit"]',
                "--session", session_id, timeout=timeout,
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
        for _ in range(6):
            result = await self._run_json(
                "evaluate",
                "({navigatorLanguage:navigator.language,documentLanguage:document.documentElement?.lang,"
                "documentReady:document.readyState!=='loading'})",
                "--session", session_id,
            )
            state = self._result_value(result)
            if isinstance(state, dict) and state.get("documentReady") is not False:
                break
            await asyncio.sleep(self.poll_interval)
        language = self._buyer_locales.get(session_id, "en-US").split("-")[0]
        verified = bool(
            isinstance(state, dict)
            and state.get("documentReady") is not False
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

    async def _ensure_gemini_mode(self, session_id: str) -> bool:
        # Flash-Lite is a separate mode. Substring matching silently admitted
        # it both in the current-mode label and in the list of menu choices.
        mode = os.environ.get("LOOK4GEO_GEMINI_MODE", "flash").strip().casefold()
        if mode not in {"auto", "flash", "flash-lite"}:
            raise RuntimeError("LOOK4GEO_GEMINI_MODE must be auto, flash or flash-lite")
        mode_name = r"(?:Gemini\s+)?(?:\d+(?:\.\d+)*\s+)?Flash"
        if mode == "auto":
            mode_name = r"(?:Gemini\s+)?Auto"
        if mode == "flash-lite":
            mode_name += r"[\s\-‐‑–—]+Lite"
        current_mode = re.compile(
            rf'Open mode picker, currently\s+{mode_name}(?:\s+\[has-submenu\])?\s*"',
            re.I,
        )
        choice_prefix = r"(?:Quick answers\s+)?" if mode == "flash-lite" else ""
        mode_choice = re.compile(
            rf"^(?:Selected\s+)?{choice_prefix}{mode_name}(?![\s\-‐‑–—]*Lite\b)(?=$|\s)", re.I
        )
        picker_ref = None
        readiness_deadline = asyncio.get_running_loop().time() + 90
        for _ in range(45):
            observation = await self._run_json(
                "observe", "--session", session_id, timeout=30
            )
            page_text = str(observation.get("text", ""))
            if current_mode.search(page_text):
                return True
            picker_ref = self._find_control_ref(
                page_text, ("Open mode picker, currently",)
            )
            if picker_ref is not None or asyncio.get_running_loop().time() >= readiness_deadline:
                break
            await asyncio.sleep(self.poll_interval)
        if picker_ref is None:
            return False
        await self._run_json("click", picker_ref, "--session", session_id, timeout=30)
        mode_ref = None
        for _ in range(5):
            menu = await self._run_json("observe", "--session", session_id, timeout=30)
            mode_ref = next((
                ref for ref, label in CONTROL_REF_PATTERN.findall(str(menu.get("text", "")))
                if mode_choice.match(label)
            ), None)
            if mode_ref is not None:
                break
            await asyncio.sleep(self.poll_interval)
        if mode_ref is None:
            return False
        await self._run_json("click", mode_ref, "--session", session_id, timeout=30)
        for _ in range(5):
            verified = await self._run_json(
                "observe", "--session", session_id, timeout=30
            )
            if current_mode.search(str(verified.get("text", ""))):
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
            # Baidu splits a turn into multiple markdown blocks. Its enclosing
            # answer container also includes the expanded search results.
            "const answerBlocks = n => "
            + ("[...n.querySelectorAll('.ai-entry-block.ai-markdown')]" if selector == ".ai-entry" else "[n]")
            + "; const geminiDomText = root => { const parts=[]; "
            "const walk=n=>{if(n.nodeType===3){parts.push(n.nodeValue||'');return;} "
            "if(n.nodeType!==1 || /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(n.tagName) || "
            "(n!==root && n.getAttribute('aria-hidden')==='true'))return; "
            "if(n.tagName==='BR'){parts.push('\\n');return;} "
            "const block=/^(P|DIV|LI|H[1-6]|BLOCKQUOTE|PRE|TR)$/.test(n.tagName); "
            "if(block)parts.push('\\n\\n'); for(const child of n.childNodes)walk(child); "
            "if(block)parts.push('\\n\\n');}; walk(root); "
            "return parts.join('').replace(/\\n{3,}/g,'\\n\\n').trim();}; "
            "const answerText = n => answerBlocks(n).map(b => {const shown=(b.innerText||'').trim(); "
            "if(location.hostname!=='gemini.google.com' || !b.closest('model-response')) return shown; "
            "const stored=geminiDomText(b); if(!shown) return stored; "
            "const shownCompact=shown.replace(/\\s+/g,' '), storedCompact=stored.replace(/\\s+/g,' '); "
            "return storedCompact.length > shownCompact.length && storedCompact.startsWith(shownCompact) "
            "? stored : shown;}).filter(Boolean).join('\\n\\n'); "
            "const answerLinks = n => answerBlocks(n).flatMap(b => [...b.querySelectorAll('a[href]')]); "
            "const buttons = [...document.querySelectorAll('button')]; "
            # A stable search summary is not a completed ChatGPT turn. The live
            # web UI exposes completion on the enclosing turn, even while the
            # stop-button label disappears between research and answer phases.
            "const latestAnswer = nodes.at(-1); "
            "const chatgptTurn = location.hostname === 'chatgpt.com' ? "
            "latestAnswer?.closest('[data-talvt-turn-state]') : null; "
            "const completionConfirmed = location.hostname === 'chatgpt.com' ? "
            "chatgptTurn?.getAttribute('data-talvt-turn-state') === 'complete' : null; "
            "const yuanbaoAnswers = [...document.querySelectorAll('.agent-chat__bubble--ai')]; "
            "const latestYuanbaoAnswer = yuanbaoAnswers.at(-1); "
            "const yuanbaoStillGenerating = location.hostname === 'yuanbao.tencent.com' && "
            "Boolean(latestYuanbaoAnswer) && "
            "!latestYuanbaoAnswer.querySelector('[aria-label=重新生成]'); "
            "const accountChanged = location.hostname === 'chatgpt.com' && "
            "[...document.querySelectorAll('[role=alert]')].some(n => "
            "/^Your account changed[.!]?\\s+Send your message again\\b/i.test((n.innerText || '').trim())); "
            "return {completion_confirmed:completionConfirmed,account_changed:accountChanged,"
            "answers:nodes.map(answerText).filter(Boolean),"
            "answer_entries:nodes.map(n => ({text:answerText(n),links:"
            "answerLinks(n).map(a => ({url:a.href,title:"
            "(a.innerText || a.textContent || '').trim() || null}))})).filter(entry => entry.text),"
            "answer_links:nodes.flatMap(n => answerLinks(n).map(a => "
            "({url:a.href,title:(a.innerText || a.textContent || '').trim() || null}))),"
            "links:[...new Set(nodes.flatMap(n => answerLinks(n).map(a => a.href)))],"
            "generating:yuanbaoStillGenerating || buttons.some(b => "
            "/停止生成|停止响应|Stop generating|Stop responding|Stop response/i.test("
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

        if platform == "chatgpt":
            try:
                popovers = await self._collect_chatgpt_popovers(
                    session_id, frozenset(source.url for source in candidates)
                )
            except Exception as error:
                return merge_sources(candidates), SourceCaptureStatus.FAILED, str(error)
            if popovers is not None:
                cards, diagnostic = popovers
                for card in cards:
                    record = self._source_record(
                        card, role=SourceRole.CITED if card.get("inline_citation") else SourceRole.SURFACED,
                        origin=SourceEvidenceOrigin.SOURCE_PANEL,
                        excluded_domains=excluded_domains,
                    )
                    if record is not None:
                        candidates.append(record)
                return (merge_sources(candidates),
                        SourceCaptureStatus.FAILED if diagnostic else SourceCaptureStatus.CAPTURED,
                        diagnostic)

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
            diagnostics = []
            for collect in (self._collect_gemini_view_sources, self._collect_gemini_source_cards):
                try:
                    cards = await collect(session_id)
                except BrowserControlInterrupted as error:
                    return merge_sources(candidates), SourceCaptureStatus.FAILED, str(error)
                except Exception as error:
                    diagnostics.append(str(error))
                    continue
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
            status = SourceCaptureStatus.NONE_EXPOSED
            if diagnostics:
                status = SourceCaptureStatus.FAILED
            elif sources:
                status = SourceCaptureStatus.CAPTURED
            return sources, status, "; ".join(diagnostics) or None

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
            expected_count = int(open_state.get("expected_count") or 0) if isinstance(open_state, dict) else 0
            if expected_count:
                return (
                    sources,
                    SourceCaptureStatus.FAILED,
                    f"answer advertises {expected_count} references but the source trigger was not located",
                )
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
        panel_found = False
        expected_count = int(open_state.get("expected_count") or 0)
        if platform == "grok" and expected_count:
            panel_page = await self._expand_grok_source_sections(session_id, expected_count)
            panel_found = bool(panel_page.get("panel_found"))
            expected_count = max(expected_count, int(panel_page.get("expected_count") or 0))
            stable_cards = list(panel_page.get("cards", []))
        else:
            for _ in range(8):
                panel_page = await self._source_panel_page(session_id, platform)
                panel_found = panel_found or bool(panel_page.get("panel_found"))
                expected_count = max(expected_count, int(panel_page.get("expected_count") or 0))
                cards = list(panel_page.get("cards", [])) if isinstance(panel_page, dict) else []
                valid_count = sum(
                    self._source_record(
                        card,
                        role=SourceRole.SURFACED,
                        origin=SourceEvidenceOrigin.SOURCE_PANEL,
                        excluded_domains=excluded_domains,
                    ) is not None
                    for card in cards
                )
                was_stable = previous_cards is not None and cards == previous_cards
                previous_cards = cards
                stable_cards = cards
                if expected_count and valid_count >= expected_count:
                    break
                if not expected_count and panel_found and was_stable:
                    break
                await asyncio.sleep(self.poll_interval)

        valid_cards = 0
        panel_records: list[SourceRecord] = []
        rejected_titles: list[str] = []
        for card in stable_cards:
            record = self._source_record(
                card,
                role=SourceRole.SURFACED,
                origin=SourceEvidenceOrigin.SOURCE_PANEL,
                excluded_domains=excluded_domains,
            )
            if record is not None:
                candidates.append(record)
                panel_records.append(record)
                valid_cards += 1
            else:
                rejected_titles.append(str(card.get("title") or "untitled")[:120])
        sources = merge_sources(candidates)
        unique_panel_cards = len(merge_sources(panel_records))
        if not panel_found or (expected_count and valid_cards < expected_count):
            diagnostic = (
                f"source panel advertised {expected_count} references; collected {valid_cards} valid cards and {unique_panel_cards} distinct URLs from {len(stable_cards)} rendered cards"
                if expected_count else "source trigger opened but no source panel could be located"
            )
            if rejected_titles:
                diagnostic += (
                    f"; rejected {len(rejected_titles)} malformed, unsafe or excluded URLs"
                    f" (titles: {', '.join(rejected_titles[:3])})"
                )
            return sources, SourceCaptureStatus.FAILED, diagnostic
        status = (
            SourceCaptureStatus.CAPTURED
            if sources
            else SourceCaptureStatus.NONE_EXPOSED
        )
        return sources, status, (
            f"inspected {valid_cards}/{expected_count} advertised source cards; stored {len(sources)} unique sources"
            if expected_count else None
        )

    async def _collect_chatgpt_popovers(self, session_id: str, known_urls: frozenset[str] = frozenset()):
        """Inspect each DIL citation popover, including chips without anchor URLs.

        A link preview has no Sources heading. Follow aria-controls rather than
        accepting unrelated dialogs (for example the floating composer).
        """
        selector = PLATFORMS["chatgpt"]["answer_selector"]
        scope = ("const answers=[...document.querySelectorAll(" + json.dumps(selector)
                 + ")]; const root=answers.at(-1); "
                 "const triggers=root ? [...root.querySelectorAll('[data-d-component=popover-trigger][aria-haspopup=dialog]')] : []; ")
        value = self._result_value(await self._run_json(
            "evaluate", "(() => { " + scope + "return {count:triggers.length,targets:triggers.map(t=>({"
            "href:t.href||t.closest('a[href]')?.href||t.querySelector('a[href]')?.href||null,"
            "link_preview:t.getAttribute('role')==='presentation',"
            "expected_count:1+Number((t.innerText||'').match(/\\+\\s*(\\d+)/)?.[1]||0),"
            "expects_more:/\\+\\s*\\d+|\\b(?:[2-9]|\\d{2,})\\s+(?:sources?|references?|links?)\\b|"
            "more\\s+(?:sources?|references?|links?)|更多(?:来源|引用)/i.test("
            "(t.getAttribute('aria-label')||'')+' '+(t.innerText||''))"
            "}))}; })()",
            "--session", session_id, timeout=30))
        count = int(value.get("count") or 0) if isinstance(value, dict) else 0
        if not count:
            return None
        targets = value.get("targets") or []
        cards, missing = [], []
        for index in range(count):
            target = targets[index] if index < len(targets) else {}
            direct = normalize_source_url(str(target.get("href") or ""))
            if target.get("link_preview") and direct in known_urls and not target.get("expects_more"):
                continue
            if "href" in target and not target["href"]:
                grouped, complete, interrupted = await self._collect_chatgpt_source_group(
                    session_id, scope, index, int(target.get("expected_count") or 1)
                )
                cards.extend(grouped)
                if not complete:
                    missing.append(index)
                if interrupted:
                    missing.extend(range(index + 1, count))
                    break
                continue
            opened = self._result_value(await self._run_json(
                "evaluate", "(() => { " + scope + f"const t=triggers[{index}]; "
                "if(!t) return false; if(t.getAttribute('aria-expanded')!=='true') t.click(); return true; })()",
                "--session", session_id, timeout=30))
            found = []
            if opened:
                for _ in range(8):
                    page = self._result_value(await self._run_json(
                        "evaluate", "(() => { " + scope + f"const t=triggers[{index}]; "
                        "const p=document.getElementById(t?.getAttribute('aria-controls')); "
                        "if(!p || !p.getClientRects().length) return []; "
                        "return [...p.querySelectorAll('a[href]')].map(a=>({url:a.href,"
                        "title:(a.innerText||a.textContent||'').trim()||null,"
                        "snippet:(a.closest('[data-d-component=row]')||a.parentElement).innerText?.trim()||null})); })()",
                        "--session", session_id, timeout=30))
                    if isinstance(page, list) and page:
                        found = page
                        break
                    await asyncio.sleep(self.poll_interval)
            if found:
                cards.extend(found)
            else:
                # An inline link can also have a hover preview. If that exact
                # citation is already captured, an unavailable preview does
                # not erase it. Multi-source or URL-less chips still require
                # the panel, so partial evidence remains explicitly failed.
                if direct not in known_urls or target.get("expects_more"):
                    missing.append(index)
        diagnostic = f"ChatGPT citation popovers unresolved: {missing} of {count}" if missing else None
        return cards, diagnostic

    async def _collect_chatgpt_source_group(self, session_id: str, scope: str, index: int, expected_count: int):
        """Resolve the actual page destinations of a grouped inline citation."""
        prefix = "(() => { " + scope + f"const t=triggers[{index}]; "
        cards = []
        try:
            group_found = self._result_value(await self._run_json(
                "evaluate", prefix +
                "if(!t)return false; t.scrollIntoView({block:'center',behavior:'instant'}); "
                f"t.setAttribute('data-look4geo-source-group','{index}'); "
                "return true; })()",
                "--session", session_id,
            ))
            selector = f'[data-look4geo-source-group="{index}"]'
            if group_found is not True or not await self._chatgpt_source_target_ready(session_id, selector):
                return cards, False, False
            await self._run_json("hover", "--selector", selector, "--settle", "500ms", "--session", session_id)
            for _ in range(2):
                await self._run_json("click", "--selector", selector, "--session", session_id)
                expanded = self._result_value(await self._run_json(
                    "evaluate", prefix + "return t?.getAttribute('aria-expanded')==='true'; })()",
                    "--session", session_id,
                ))
                if expanded is not False:
                    break
            panel = None
            for _ in range(4):
                observed = self._result_value(await self._run_json(
                    "evaluate", prefix +
                    "const p=document.getElementById(t?.getAttribute('aria-controls')); "
                    "if(t?.getAttribute('aria-expanded')!=='true'||!p?.getClientRects().length)return null; "
                    "return {links:[...p.querySelectorAll('a[href]')].map(a=>({url:a.href,title:a.innerText.trim()})),"
                    "buttons:[...p.querySelectorAll('button[data-d-component=pressable]')].map((b,i)=>{"
                    f"b.setAttribute('data-look4geo-source-card','{index}-'+i);"
                    "return {title:b.innerText.trim().split('\\n')[1]||b.getAttribute('aria-label')};})}; })()",
                    "--session", session_id,
                ))
                if isinstance(observed, dict) and isinstance(observed.get("links"), list) and isinstance(observed.get("buttons"), list):
                    panel = observed
                    if len(panel["links"]) + len(panel["buttons"]) >= expected_count:
                        break
                await asyncio.sleep(self.poll_interval)
            if panel is None:
                panel = {"links": [], "buttons": []}
            cards.extend({**link, "inline_citation": True} for link in panel["links"])
            complete = len(panel["links"]) + len(panel["buttons"]) >= expected_count
            for card_index, button in enumerate(panel["buttons"]):
                url, cleaned = await self._chatgpt_source_button_url(session_id, f"{index}-{card_index}")
                if url:
                    cards.append({"url": url, "title": button["title"], "inline_citation": True})
                else:
                    complete = False
                if not cleaned:
                    return cards, False, True
            await self._run_json(
                "evaluate", prefix + "if(t?.getAttribute('aria-expanded')==='true')t.click(); return true; })()",
                "--session", session_id,
            )
            return cards, complete, False
        except (BrowserControlInterrupted, RuntimeError, asyncio.TimeoutError):
            # The click may have taken effect. Leave all further actions to
            # session cleanup and keep the evidence already read.
            return cards, False, True

    async def _chatgpt_source_target_ready(self, session_id: str, selector: str) -> bool:
        expression = (
            "(() => { const t=document.querySelector(" + json.dumps(selector) + "); "
            "if(!t?.getClientRects().length)return false; const r=t.getBoundingClientRect(); "
            "const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2); "
            "return !!hit&&(hit===t||t.contains(hit)); })()"
        )
        for _ in range(4):
            if self._result_value(await self._run_json("evaluate", expression, "--session", session_id)) is True:
                return True
            await asyncio.sleep(self.poll_interval)
        return False

    async def _chatgpt_source_button_url(self, session_id: str, card_id: str) -> tuple[str | None, bool]:
        tab_args = ("tab", "list", "--scope", "agent", "--session", session_id)
        selector = f'[role=dialog][data-state=open] [data-look4geo-source-card="{card_id}"]'
        await self._run_json(
            "evaluate", "document.querySelector(" + json.dumps(selector) +
            ")?.scrollIntoView({block:'nearest',behavior:'instant'}) ?? null",
            "--session", session_id,
        )
        if not await self._chatgpt_source_target_ready(session_id, selector):
            return None, True
        before = await self._run_json(*tab_args)
        existing = {tab["tab_id"] for tab in before["tabs"]}
        await self._run_json(
            "click", "--selector", selector, "--session", session_id,
        )
        opened = {}
        urls = set()
        for _ in range(5):
            after = await self._run_json(*tab_args)
            observed = {tab["tab_id"]: str(tab.get("url") or "") for tab in after["tabs"] if tab["tab_id"] not in existing}
            settled = bool(observed) and observed == opened
            opened = observed
            urls = {url for value in opened.values() if (url := normalize_source_url(value))}
            if settled and all(normalize_source_url(value) for value in opened.values()):
                break
            await asyncio.sleep(self.poll_interval)
        url = next(iter(urls)) if len(urls) == 1 else None
        try:
            for tab_id in reversed(opened):
                await self._run_json("tab", "close", str(tab_id), "--session", session_id)
        except RuntimeError as error:
            answer_tab = self._session_tabs.get(session_id)
            if answer_tab is None or "Actions require an owned task tab" not in str(error):
                return url, False
            try:
                await self._run_json("tab", "select", str(answer_tab), "--session", session_id)
            except (BrowserControlInterrupted, RuntimeError, asyncio.TimeoutError):
                return url, False
        except (BrowserControlInterrupted, asyncio.TimeoutError):
            return url, False
        return url, True

    async def _expand_grok_source_sections(self, session_id: str, expected_count: int) -> dict:
        """Grok hides search results in separate, lazily rendered accordions."""
        page: dict = {"panel_found": False, "cards": [], "collapsed_count": 0}
        for _ in range(20):
            page = await self._source_panel_page(session_id, "grok")
            if not page.get("panel_found") or not page.get("collapsed_count"):
                break
            result = await self._run_json(
                "evaluate",
                "(() => { const panel = [...document.querySelectorAll('aside')]"
                ".find(node => node.getClientRects().length && "
                "(node.innerText || '').trim().startsWith('Sources')); "
                "const button = panel?.querySelector('button[aria-controls][aria-expanded=\"false\"]'); "
                "if (!button) return false; button.click(); return true; })()",
                "--session", session_id, timeout=30,
            )
            if not self._result_value(result):
                break
            await asyncio.sleep(self.poll_interval)
        for _ in range(4):
            page = await self._source_panel_page(session_id, "grok")
            if len(page.get("cards", [])) >= expected_count or not page.get("collapsed_count"):
                break
            await asyncio.sleep(self.poll_interval)
        return page

    async def _collect_gemini_source_cards(self, session_id: str) -> list[dict]:
        observation = await self._run_json(
            "observe", "--session", session_id, timeout=30
        )
        source_refs = [
            ref
            for ref, label in CONTROL_REF_PATTERN.findall(str(observation.get("text", "")))
            if any(text in label for text in ("View source details", "查看引用来源详情", "查看来源"))
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
            dialog_cards = list(panel_page.get("cards", []))
            if not dialog_cards:
                raise RuntimeError(f"Gemini source dialog {index + 1} opened but no source links were read")
            cards.extend(dialog_cards)
            await self._run_json("press", "Escape", "--session", session_id, timeout=30)
        return cards

    async def _collect_gemini_view_sources(self, session_id: str) -> list[dict]:
        """Read the answer's separate View sources sidebar, including scrolled cards."""
        observation = await self._run_json("observe", "--session", session_id, timeout=30)
        controls = CONTROL_REF_PATTERN.findall(str(observation.get("text", "")))
        menu_refs = [ref for ref, label in controls if label in {"Show more options", "更多选项"}]
        if not menu_refs:
            return []
        await self._run_json("press", "Enter", "--ref", menu_refs[-1], "--session", session_id, timeout=30)
        for _ in range(4):
            observation = await self._run_json("observe", "--session", session_id, timeout=30)
            source_ref = next((
                ref for ref, label in CONTROL_REF_PATTERN.findall(str(observation.get("text", "")))
                if label in {"View sources", "查看来源", "查看信源"}
            ), None)
            if source_ref:
                await self._run_json("press", "Enter", "--ref", source_ref, "--session", session_id, timeout=30)
                break
            await asyncio.sleep(self.poll_interval)
        else:
            return []

        read_page = (
            "(() => { const panel = document.querySelector('context-sidebar .all-sources'); "
            "if (!panel || !panel.getClientRects().length) return {panel_found:false,cards:[]}; "
            "const cards = [...panel.querySelectorAll('a[href]')].map(anchor => ({"
            "url:anchor.href,title:(anchor.querySelector('.title')?.innerText || "
            "anchor.innerText || '').trim() || null,snippet:null})); "
            "const at_end = panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 3; "
            "if (!at_end) panel.scrollTop = Math.min(panel.scrollHeight, "
            "panel.scrollTop + Math.max(200,panel.clientHeight - 80)); "
            "return {panel_found:true,cards,at_end,scroll_top:panel.scrollTop,"
            "scroll_height:panel.scrollHeight}; })()"
        )
        cards: list[dict] = []
        reached_end = False
        try:
            page = {"panel_found": False}
            for _ in range(4):
                page = self._result_value(await self._run_json(
                    "evaluate", read_page, "--session", session_id, timeout=30
                ))
                if isinstance(page, dict) and page.get("panel_found") and page.get("cards"):
                    break
                await asyncio.sleep(self.poll_interval)
            if not isinstance(page, dict) or not page.get("panel_found"):
                raise RuntimeError("Gemini View sources sidebar did not open")
            for _ in range(30):
                cards.extend(page.get("cards") or [])
                if page.get("at_end"):
                    reached_end = True
                    break
                await asyncio.sleep(self.poll_interval)
                page = self._result_value(await self._run_json(
                    "evaluate", read_page, "--session", session_id, timeout=30
                ))
                if not isinstance(page, dict) or not page.get("panel_found"):
                    raise RuntimeError("Gemini View sources sidebar closed before reading completed")
            if not reached_end:
                raise RuntimeError("Gemini Sources sidebar did not reach the end")
            if not cards:
                raise RuntimeError("Gemini Sources sidebar opened but no source links were read")
            return list({str(card.get("url") or ""): card for card in cards}.values())
        finally:
            close_sidebar = (
                "(() => { const sidebar = document.querySelector('context-sidebar'); "
                "const button = sidebar?.querySelector('button[aria-label=\"Close sidebar\"]'); "
                "if (button?.getClientRects().length) button.click(); return true; })()"
            )
            await self._run_json("evaluate", close_sidebar, "--session", session_id, timeout=30)

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
        if platform == "grok":
            expression = (
                "(() => { const triggers = [...document.querySelectorAll("
                "'[role=button][aria-label*=source i]')].filter(node => node.getClientRects().length); "
                "const trigger = triggers.at(-1); if (!trigger) "
                "return {found:false,opened:false,expected_count:0}; "
                "const match = (trigger.getAttribute('aria-label') || '').match(/(\\d+)\\s+sources?/i); "
                "const expected_count = match ? Number(match[1]) : 0; "
                "const panel = [...document.querySelectorAll('aside')].find(node => "
                "node.getClientRects().length && (node.innerText || '').trim().startsWith('Sources')); "
                "if (panel) return {found:true,opened:true,expected_count}; "
                "try { trigger.click(); return {found:true,opened:true,expected_count}; } "
                "catch (error) { return {found:true,opened:false,expected_count,diagnostic:String(error)}; } })()"
            )
            result = await self._run_json("evaluate", expression, "--session", session_id, timeout=30)
            value = self._result_value(result)
            return value if isinstance(value, dict) else {"found": False, "opened": False}
        if platform == "baidu":
            expression = (
                "(() => { const root = [...document.querySelectorAll('.ai-entry')].at(-1); "
                "if (!root) return {found:false,opened:false,expected_count:0}; "
                "const heading = root.querySelector('.thinking-steps-title-extra')?.innerText || ''; "
                r"const match = heading.match(/共参考\s*(\d+)\s*篇资料/); "
                "const expected_count = match ? Number(match[1]) : 0; "
                "const list = root.querySelector('ol[class*=_reference_]'); "
                "if (list?.getClientRects().length) return {found:true,opened:true,expected_count}; "
                "const trigger = root.querySelector('.thinking-steps-title-extra')?.closest('[class*=_main-header_]'); "
                "if (!trigger) return {found:expected_count>0,opened:false,expected_count}; "
                "try { trigger.click(); return {found:true,opened:true,expected_count}; } "
                "catch (error) { return {found:true,opened:false,expected_count,diagnostic:String(error)}; } })()"
            )
            result = await self._run_json(
                "evaluate", expression, "--session", session_id, timeout=30
            )
            value = self._result_value(result)
            return value if isinstance(value, dict) else {"found": False, "opened": False}
        if platform == "yuanbao":
            expression = (
                "(() => { const triggers = [...document.querySelectorAll('[data-toolbar-type=citation]')]"
                ".filter(node => node.getClientRects().length); const trigger = triggers.at(-1); "
                "if (!trigger) return {found:false,opened:false,expected_count:0}; "
                "const label = trigger.getAttribute('aria-label') || ''; "
                r"const match = label.match(/引用\s*(\d+)\s*篇资料/); "
                "const expected_count = match ? Number(match[1]) : 0; "
                "const panel = document.querySelector('#chatReferenceList.agent-dialogue-references'); "
                "if (panel?.getClientRects().length) return {found:true,opened:true,expected_count}; "
                "try { trigger.click(); return {found:true,opened:true,expected_count}; } "
                "catch (error) { return {found:true,opened:false,expected_count,diagnostic:String(error)}; } })()"
            )
            result = await self._run_json(
                "evaluate", expression, "--session", session_id, timeout=30
            )
            value = self._result_value(result)
            return value if isinstance(value, dict) else {"found": False, "opened": False}
        if platform == "doubao":
            expression = (
                "(() => { const answers = [...document.querySelectorAll("
                "'[data-testid=message_text_content]')]; "
                "const container = answers.at(-1)?.closest('[data-testid=message_content]'); "
                "const blocks = [...(container?.querySelectorAll("
                "'[data-plugin-identifier*=search_query_result_block]') || [])]; "
                "if (!blocks.length) return {found:false,opened:false,expected_count:0}; "
                "let expected_count = 0; let missing = 0; "
                "for (const block of blocks) { const trigger = block.querySelector('[data-copy-ignore]'); "
                r"const text = trigger?.innerText || ''; const match = text.match(/参考\s*(\d+)\s*篇资料/); "
                "expected_count += match ? Number(match[1]) : 0; "
                "if (block.querySelectorAll('a[data-thinking-box-tool-call][href]').length) continue; "
                "if (!trigger) { missing++; continue; } trigger.click(); } "
                "return {found:true,opened:missing===0,expected_count,"
                "diagnostic:missing ? missing+' source blocks have no expand control' : null}; })()"
            )
            result = await self._run_json(
                "evaluate", expression, "--session", session_id, timeout=30
            )
            value = self._result_value(result)
            return value if isinstance(value, dict) else {"found": False, "opened": False}
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
            "const root = answers.at(-1) || document; "
            + self._source_scope_js()
            + "let trigger = null; "
            "for (const selector of selectors) { trigger = roots.flatMap(r => [...r.querySelectorAll(selector)])"
            ".find(n => n.getClientRects().length) || "
            "(allowDocumentFallback ? document.querySelector(selector) : null); if (trigger) break; } "
            "if (!trigger && labels.length) { const controls = [...new Set(roots.flatMap(r => [...r.querySelectorAll("
            "'button,[role=button],a,[onclick],summary,div,span')])), "
            "...(allowDocumentFallback ? document.querySelectorAll("
            "'button,[role=button],a,[onclick]') : [])]; trigger = controls.find(node => { const text = "
            "((node.getAttribute('aria-label') || '') + ' ' + (node.innerText || '')).trim(); "
            "return node.getClientRects().length && text.length < 240 && labels.some(label => text.includes(label)); }); } "
            "const expected_count = Math.max(0,...roots.map(r => sourceCount(r.innerText || ''))); "
            "if (!trigger) return {found:expected_count > 0,opened:false,expected_count,"
            "diagnostic:expected_count ? 'reference count is visible but source trigger was not located' : null}; "
            "try { if (trigger.getAttribute('aria-expanded') !== 'true') trigger.click(); "
            "return {found:true,opened:true,expected_count}; } "
            "catch (error) { return {found:true,opened:false,expected_count,diagnostic:String(error)}; } })()"
        )
        result = await self._run_json(
            "evaluate", expression, "--session", session_id, timeout=30
        )
        value = self._result_value(result)
        return value if isinstance(value, dict) else {"found": False, "opened": False}

    @staticmethod
    def _source_scope_js() -> str:
        """Include sibling reference blocks, stopping before earlier answer turns."""
        pattern = r"参考\s*(\d+)\s*篇资料|(\d+)\s*(?:sources|个来源|个网页)|(?:来源|引用来源)\s*[（(]?\s*(\d+)"
        return (
            "const roots = []; let scope = root; "
            "for (let depth = 0; scope && scope !== document.body && depth < 6; depth++,scope = scope.parentElement) { "
            "if (scope !== root && scope.querySelectorAll(answerSelector).length > 1) break; roots.push(scope); } "
            "if (!roots.length) roots.push(root); "
            "const countPattern = new RegExp(" + json.dumps(pattern) + ", 'i'); "
            "const sourceCount = text => { const m = text.match(countPattern); "
            "return m ? Number(m.slice(1).find(Boolean)) : 0; }; "
        )

    async def _source_panel_page(self, session_id: str, platform: str) -> dict:
        if platform == "grok":
            expression = (
                "(() => { const panel = [...document.querySelectorAll('aside')].find(node => "
                "node.getClientRects().length && (node.innerText || '').trim().startsWith('Sources')); "
                "if (!panel) return {panel_found:false,cards:[],collapsed_count:0}; "
                "const cards = [...panel.querySelectorAll('a.block[href]')].map(anchor => ({"
                "url:anchor.href,title:(anchor.innerText || '').trim() || null,snippet:null})); "
                "const collapsed_count = panel.querySelectorAll('button[aria-controls][aria-expanded=\"false\"]').length; "
                "return {panel_found:true,cards,collapsed_count}; })()"
            )
            result = await self._run_json("evaluate", expression, "--session", session_id, timeout=30)
            value = self._result_value(result)
            return value if isinstance(value, dict) else {"panel_found": False, "cards": []}
        if platform == "gemini":
            expression = (
                "(() => { const panel = [...document.querySelectorAll('[role=dialog]')]"
                ".find(node => node.getClientRects().length && node.querySelector('a[href]')); "
                "if (!panel) return {panel_found:false,cards:[]}; "
                "const cards = [...panel.querySelectorAll('a[href]')].map(anchor => ({"
                "url:anchor.href,title:(anchor.innerText || '').trim() || null,"
                "snippet:null})); return {panel_found:true,cards}; })()"
            )
            result = await self._run_json(
                "evaluate", expression, "--session", session_id, timeout=30
            )
            value = self._result_value(result)
            return value if isinstance(value, dict) else {"panel_found": False, "cards": []}
        if platform == "baidu":
            expression = (
                "(() => { const root = [...document.querySelectorAll('.ai-entry')].at(-1); "
                "const heading = root?.querySelector('.thinking-steps-title-extra')?.innerText || ''; "
                r"const match = heading.match(/共参考\s*(\d+)\s*篇资料/); "
                "const expected_count = match ? Number(match[1]) : 0; "
                "const list = root?.querySelector('ol[class*=_reference_]'); "
                "if (!list?.getClientRects().length) return {panel_found:false,expected_count,cards:[]}; "
                "const cards = [...list.querySelectorAll('li[data-long-press-ext-info]')].map(item => { "
                "let info = {}; try { info = JSON.parse(item.getAttribute('data-long-press-ext-info') || '{}'); } "
                "catch {} return {url:info.link || '',title:info.linkTitle || item.innerText.trim(),snippet:null}; }); "
                "return {panel_found:true,expected_count,cards}; })()"
            )
            result = await self._run_json(
                "evaluate", expression, "--session", session_id, timeout=30
            )
            value = self._result_value(result)
            return value if isinstance(value, dict) else {"panel_found": False, "cards": []}
        if platform == "yuanbao":
            expression = (
                "(() => { const panel = document.querySelector('#chatReferenceList.agent-dialogue-references'); "
                "if (!panel?.getClientRects().length) return {panel_found:false,expected_count:0,cards:[]}; "
                "const heading = panel.querySelector('.agent-dialogue-references__header-txt')?.innerText || ''; "
                r"const match = heading.match(/[（(]\s*(\d+)\s*[）)]/); "
                "const expected_count = match ? Number(match[1]) : 0; "
                "const cards = [...panel.querySelectorAll('.agent-dialogue-references__list > .agent-dialogue-references__item')]"
                ".map(item => { const link = item.querySelector('[data-url]'); "
                "return {url:link?.getAttribute('data-url') || '', "
                "title:(item.querySelector('h4')?.innerText || '').trim() || null, "
                "snippet:(item.innerText || '').trim() || null}; }); "
                "return {panel_found:true,expected_count,cards}; })()"
            )
            result = await self._run_json(
                "evaluate", expression, "--session", session_id, timeout=30
            )
            value = self._result_value(result)
            return value if isinstance(value, dict) else {"panel_found": False, "cards": []}
        if platform == "doubao":
            expression = (
                "(() => { const answers = [...document.querySelectorAll("
                "'[data-testid=message_text_content]')]; "
                "const container = answers.at(-1)?.closest('[data-testid=message_content]'); "
                "const blocks = [...(container?.querySelectorAll("
                "'[data-plugin-identifier*=search_query_result_block]') || [])]; "
                "let expected_count = 0; const cards = []; "
                "for (const block of blocks) { const text = block.querySelector('[data-copy-ignore]')?.innerText || ''; "
                r"const match = text.match(/参考\s*(\d+)\s*篇资料/); "
                "expected_count += match ? Number(match[1]) : 0; "
                "for (const anchor of block.querySelectorAll('a[data-thinking-box-tool-call][href]')) { "
                "cards.push({url:anchor.href,title:(anchor.innerText || '').trim() || null,"
                "snippet:null}); } } "
                "return {panel_found:blocks.length>0,expected_count,cards}; })()"
            )
            result = await self._run_json(
                "evaluate", expression, "--session", session_id, timeout=30
            )
            value = self._result_value(result)
            return value if isinstance(value, dict) else {"panel_found": False, "cards": []}
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
            + "; const answerSelector = "
            + json.dumps(config["answer_selector"])
            + "; const answers = [...document.querySelectorAll(answerSelector)]; "
            "const root = answers.at(-1) || document; "
            + self._source_scope_js()
            + "const inlineSummary = "
            + json.dumps(bool(config.get("inline_source_summary")))
            + "; let panels = panelSelectors.flatMap(selector => "
            "[...document.querySelectorAll(selector)]); if (!panels.length && panelLabels.length) { "
            "const labelled = [...new Set(roots.flatMap(r => [...r.querySelectorAll("
            "'h1,h2,h3,h4,[role=heading],summary,button,div,span')]))].filter("
            "node => (node.innerText || '').length < 240 && panelLabels.some(label => (node.innerText || '').includes(label))); "
            "for (const heading of labelled) { let node = heading; for (let depth = 0; node && depth < 6; "
            "depth += 1, node = node.parentElement) { if (!roots.some(r => r === node || r.contains(node))) break; "
            "if (cardSelectors.some(selector => "
            "node.querySelector(selector))) { panels.push(node); break; } } } } "
            "if (!panels.length && inlineSummary) { const inline = roots.find(r => "
            "sourceCount(r.innerText || '') > 0 && r.querySelector('a[href]')); if (inline) panels.push(inline); } "
            "panels = panels.filter(panel => panel.getClientRects().length && "
            "(!panelLabels.length || panelLabels.some(label => (panel.innerText || '').includes(label)))); "
            "const expected_count = Math.max(0,...roots.map(r => sourceCount(r.innerText || ''))); "
            "if (!panels.length) return {panel_found:false,cards:[],expected_count}; "
            "const cards = [...new Set(panels.flatMap(panel => "
            "cardSelectors.flatMap(selector => [...panel.querySelectorAll(selector)])))]; "
            "return {panel_found:true,expected_count,cards:cards.map(card => { const anchor = "
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

    def _cooldown_path(self) -> Path:
        browser_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(getattr(self.client, "browser_instance_id", "configured")))
        return self.artifact_root / f".chatgpt-cooldown-{browser_id}.json"

    def _cooldown_remaining(self) -> float:
        path = self._cooldown_path()
        if not path.exists():
            return 0
        try:
            return max(0, float(json.loads(path.read_text())["retry_after_epoch"]) - time.time())
        except (ValueError, KeyError, OSError, TypeError):
            return float("inf")  # Corrupt pause state must not silently unlock an account.

    async def health(self, platform: str) -> AdapterHealth:
        if platform == "chatgpt" and self._cooldown_remaining():
            return AdapterHealth(False, "ChatGPT rate-limit cooldown active; wait, do not auto-unblock")
        if platform == "chatgpt" and isinstance(self.client, BskCliClient) and not self.client.browser_instance_id:
            return AdapterHealth(False, "ChatGPT sampling suspended: configure LOOK4GEO_CHATGPT_BROWSER_ID for the probe Chrome profile")
        if platform in PLATFORMS and hasattr(self.client, "browser_health"):
            return await self.client.browser_health()
        return AdapterHealth(platform in PLATFORMS, "configured" if platform in PLATFORMS else "unsupported")

    async def login(self, platform: str) -> dict:
        return {
            "status": "user_action_required",
            "platform": platform,
            "instructions": f"Complete login at {PLATFORMS[platform]['url']} in the visible Agent Window.",
        }

    async def run(self, platform: str, request: ProbeRequest) -> PlatformAttempt:
        async with self.operation_lock:
            if platform == "chatgpt" and self._cooldown_remaining():
                return PlatformAttempt(platform=platform, adapter=self.name, status=JobStatus.FAILED,
                    failure=FailureKind.RATE_LIMITED, diagnostic="ChatGPT cooldown active; no browser request sent")
            result = await self._run_exclusive(platform, request)
            if platform == "chatgpt" and result.failure == FailureKind.RATE_LIMITED:
                self.artifact_root.mkdir(parents=True, exist_ok=True)
                self._cooldown_path().write_text(json.dumps({
                    "retry_after_epoch": time.time() + 1800,
                    "reason": result.diagnostic,
                    "note": "30 minute local backoff, not platform quota reset time",
                }))
            return result

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
            max_attempts = 3 if platform == "gemini" else 2 if platform in {
                "baidu", "yuanbao", "doubao", "deepseek"
            } else 1
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
                except RuntimeError as error:
                    diagnostic = str(error)
                    if platform == "gemini" and diagnostic.startswith("Gemini platform error:"):
                        output = BrowserProbeOutput(
                            failure=FailureKind.SEND_FAILED,
                            diagnostic=diagnostic,
                            retryable=False,
                        )
                    elif any(marker in diagnostic.casefold() for marker in (
                        "err_network_changed",
                        "session not registered",
                        "message textbox not found",
                    )):
                        output = BrowserProbeOutput(
                            failure=FailureKind.EXTRACTION_FAILED,
                            diagnostic=diagnostic,
                        )
                    else:
                        raise
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
                    not output.retryable
                    or output.failure not in retryable
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
                    raw_answer=output.answer,
                    normalized_answer=output.answer.strip(),
                    conversation_url=output.conversation_url,
                    source_capture_status=output.source_capture_status,
                    source_capture_diagnostic=output.source_capture_diagnostic,
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
                conversation_url=output.conversation_url,
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
                and (
                    "submission state did not change" in diagnostic.casefold()
                    or "prompt entry could not be verified" in diagnostic.casefold()
                )
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
