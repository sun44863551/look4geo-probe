from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .base import ProbeAdapter
from .types import AdapterHealth
from ..models import Citation, FailureKind, JobStatus, PlatformAttempt, ProbeRequest
from ..validity import CONTAMINATION_RE

PLATFORMS = {
    "doubao": {
        "url": "https://www.doubao.com/chat/?channel=sysceo&from_login=1",
        "textbox": ("发送消息", "输入消息", "问问豆包"),
        "composer_selector": 'textarea, div[role="textbox"], div[contenteditable="true"]',
        "login_markers": ("登录", "扫码登录"),
        "conversation_marker": "/chat/",
        "answer_selector": '[data-testid="message_text_content"]',
    },
    "chatgpt": {
        "url": "https://chatgpt.com/",
        "textbox": ("给 ChatGPT 发消息", "询问 ChatGPT"),
        "composer_selector": 'div[contenteditable="true"]',
        "login_markers": ("登录", "注册"),
        "conversation_marker": "/c/",
        "answer_selector": '[class*="MarkdownRoot-"]',
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
    "qwen": {
        "url": "https://chat.qwen.ai/",
        "textbox": ("输入消息", "How can I help you today?", "Ask anything"),
        "composer_selector": 'textarea, div[role="textbox"], div[contenteditable="true"], div[data-slate-editor="true"]',
        "login_markers": ("登录", "Sign in", "Continue with Google"),
        "conversation_marker": "/c/",
        "answer_selector": '[data-message-author-role="assistant"], [class*="assistant"], [class*="markdown"], article',
    },
    "gemini": {
        "url": "https://gemini.google.com/app",
        "textbox": ("输入提示", "Enter a prompt", "向 Gemini 提问"),
        "composer_selector": 'rich-textarea textarea, textarea, div[role="textbox"], div[contenteditable="true"]',
        "login_markers": ("登录", "Sign in", "Continue with Google"),
        "conversation_marker": "/app/",
        "answer_selector": "model-response-content message-content",
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
        "textbox": ("Ask anything", "向 Grok 提问", "输入消息"),
        "composer_selector": 'textarea, div[role="textbox"], div[contenteditable="true"]',
        "login_markers": ("登录", "Sign in", "Continue with X", "Continue with Google"),
        "conversation_marker": "/c/",
        "answer_selector": '[data-message-author-role="assistant"], [class*="assistant"], [class*="response"], [class*="markdown"]',
    },
}
REF_PATTERN = re.compile(r"(@e\d+)\s+textbox\s+\"([^\"]+)\"")
URL_PATTERN = re.compile(r"https?://[^\s<>\])}]+")
TRANSIENT_ANSWER_LINES = {
    "正在搜索网络",
    "跳过",
    "搜索中",
    "思考中",
    "generating",
    "searching the web",
    "skip",
}
RATE_LIMIT_MARKERS = (
    "429",
    "rate limit",
    "请求过于频繁",
    "已达到免费搜索次数上限",
    "使用权限将在几小时后重置",
    "free searches limit",
)
PREAMBLE_PATTERN = re.compile(
    r"^(?:我会|我先|我将|I(?:['’]?ll| will)\b|Let me\b)", re.I
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
    if platform == "perplexity":
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


def is_prompt_echo(candidate: str, prompt: str) -> bool:
    return bool(candidate.strip()) and comparable_text(candidate) == comparable_text(prompt)


def page_is_rate_limited(page: dict) -> bool:
    text = str(page.get("page_text") or "").casefold()
    return bool(page.get("rate_limited")) or any(marker.casefold() in text for marker in RATE_LIMIT_MARKERS)


def is_incomplete_preamble(platform: str, answer: str) -> bool:
    return platform == "chatgpt" and len(answer) < 300 and bool(PREAMBLE_PATTERN.match(answer))


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
    def __init__(self, browser_instance_id: str, *, poll_interval: float = 2.0):
        if not browser_instance_id:
            raise ValueError("browser_instance_id is required")
        self.browser_instance_id = browser_instance_id
        self.poll_interval = poll_interval

    async def _run_json(self, *args: str, timeout: float = 30.0) -> dict | list:
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
        result = await self._run_json(
            "session", "start", "--browser", self.browser_instance_id, "--no-focus"
        )
        return str(result["session_id"])

    async def probe(
        self, session_id: str, platform: str, prompt: str, timeout: float
    ) -> BrowserProbeOutput:
        config = PLATFORMS[platform]
        navigation = await self._run_json(
            "navigate", config["url"], "--session", session_id, timeout=timeout
        )
        observation = await self._run_json("observe", "--session", session_id, timeout=timeout)
        page_text = str(observation.get("text", ""))
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
            if page_is_rate_limited(page):
                return BrowserProbeOutput(
                    failure=FailureKind.RATE_LIMITED,
                    diagnostic="platform quota or rate limit detected",
                )
            candidates = [str(value) for value in page.get("answers", [])]
            delta = answers_after_baseline(candidates, baseline)
            delta = [candidate for candidate in delta if not is_prompt_echo(candidate, prompt)]
            saw_answer_candidate = saw_answer_candidate or bool(delta)
            current_text = select_main_answer(platform, delta)
            if not current_text:
                continue
            if is_incomplete_preamble(platform, current_text) or is_context_contamination(
                platform, prompt, current_text
            ):
                previous = current_text
                stable_rounds = 0
                continue
            lowered = current_text.casefold()
            if "429" in lowered or "rate limit" in lowered or "请求过于频繁" in current_text:
                return BrowserProbeOutput(
                    failure=FailureKind.RATE_LIMITED, diagnostic="platform rate limit detected"
                )
            if page.get("generating"):
                stable_rounds = 0
            elif current_text == previous:
                stable_rounds += 1
                if stable_rounds >= 2:
                    return BrowserProbeOutput(
                        answer=current_text,
                        citations=list(
                            dict.fromkeys(
                                [str(url) for url in page.get("links", [])]
                                + URL_PATTERN.findall(current_text)
                            )
                        ),
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
        if textbox_ref:
            try:
                await self._run_json(
                    "fill", textbox_ref, "--value", prompt, "--session", session_id, timeout=timeout
                )
                return
            except RuntimeError:
                pass

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
        expression = f"document.execCommand('insertText', false, {json.dumps(prompt)})"
        await self._run_json("evaluate", expression, "--session", session_id, timeout=timeout)

    async def _submit_prompt(
        self,
        session_id: str,
        platform: str,
        textbox_ref: str | None,
        *,
        timeout: float = 30.0,
    ) -> None:
        if platform == "chatgpt":
            try:
                await self._run_json(
                    "click", 'button[aria-label="发送"], button[aria-label="Send prompt"]',
                    "--session", session_id, timeout=timeout
                )
                return
            except RuntimeError:
                pass
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

    async def _current_url(self, session_id: str) -> str:
        result = await self._run_json(
            "evaluate", "location.href", "--session", session_id, timeout=30
        )
        value = self._result_value(result)
        return str(value or "")

    async def _answer_page(self, session_id: str, selector: str) -> dict:
        expression = (
            "(() => { const nodes = [...document.querySelectorAll("
            + json.dumps(selector)
            + ")]; const bodyText = document.body ? (document.body.innerText || '') : ''; "
            "const buttons = [...document.querySelectorAll('button')]; "
            "return {answers:nodes.map(n => (n.innerText || '').trim()).filter(Boolean),"
            "links:[...new Set(nodes.flatMap(n => [...n.querySelectorAll('a[href]')].map(a => a.href)))],"
            "generating:buttons.some(b => /停止生成|Stop generating|Stop responding/i.test("
            "(b.getAttribute('aria-label') || '') + ' ' + (b.innerText || ''))),"
            "rate_limited:/429|rate limit|请求过于频繁|已达到免费搜索次数上限|使用权限将在几小时后重置|free searches limit/i.test(bodyText)}; })()"
        )
        result = await self._run_json("evaluate", expression, "--session", session_id, timeout=30)
        value = self._result_value(result)
        return value if isinstance(value, dict) else {"answers": [], "links": []}

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
        await self._run_json("session", "stop", session_id)

    @staticmethod
    def _find_textbox_ref(
        page_text: str, expected_label: str | tuple[str, ...]
    ) -> str | None:
        expected = (expected_label,) if isinstance(expected_label, str) else expected_label
        for ref, label in REF_PATTERN.findall(page_text):
            if label in expected:
                return ref
        return None


class BrowserSkillAdapter(ProbeAdapter):
    name = "browser_skill"

    def __init__(self, *, client, artifact_root: Path, default_timeout: float = 180.0):
        self.client = client
        self.artifact_root = Path(artifact_root)
        self.default_timeout = default_timeout

    async def health(self, platform: str) -> AdapterHealth:
        return AdapterHealth(platform in PLATFORMS, "configured" if platform in PLATFORMS else "unsupported")

    async def login(self, platform: str) -> dict:
        return {
            "status": "user_action_required",
            "platform": platform,
            "instructions": f"Complete login at {PLATFORMS[platform]['url']} in the visible Agent Window.",
        }

    async def run(self, platform: str, request: ProbeRequest) -> PlatformAttempt:
        if platform not in PLATFORMS:
            return PlatformAttempt(
                platform=platform,
                adapter=self.name,
                status=JobStatus.FAILED,
                diagnostic=f"unsupported platform: {platform}",
            )
        session_id: str | None = None
        normalized = normalize_for_browser(request.prompt)
        try:
            session_id = await self.client.start(platform)
            output = await self.client.probe(
                session_id,
                platform,
                normalized.sent,
                float(request.options.get("timeout", self.default_timeout)),
            )
            if output.login_required:
                return PlatformAttempt(
                    platform=platform,
                    adapter=self.name,
                    status=JobStatus.WAITING_FOR_LOGIN,
                    diagnostic="login required",
                    failure=FailureKind.LOGIN_REQUIRED,
                    query_original=normalized.original,
                    query_sent=normalized.sent,
                    query_normalized=normalized.changed,
                )
            if output.failure is not None:
                return PlatformAttempt(
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
                platform=platform,
                adapter=self.name,
                status=JobStatus.SUCCEEDED,
                raw_answer=output.answer,
                normalized_answer=output.answer.strip(),
                citations=[Citation(url=url) for url in output.citations],
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
            return PlatformAttempt(
                platform=platform,
                adapter=self.name,
                status=JobStatus.FAILED,
                diagnostic=str(error),
                failure=FailureKind.UNKNOWN,
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
