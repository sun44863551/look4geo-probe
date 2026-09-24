from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .base import ProbeAdapter
from .types import AdapterHealth
from ..models import Citation, FailureKind, JobStatus, PlatformAttempt, ProbeRequest

PLATFORMS = {
    "doubao": {
        "url": "https://www.doubao.com/chat/?channel=sysceo&from_login=1",
        "textbox": ("发送消息", "输入消息", "问问豆包"),
        "composer_selector": 'textarea, div[role="textbox"], div[contenteditable="true"]',
        "login_markers": ("登录", "扫码登录"),
        "conversation_marker": "/chat/",
        "answer_selector": '[class*="assistant"], [class*="markdown"], [data-message-author-role="assistant"]',
    },
    "chatgpt": {
        "url": "https://chatgpt.com/",
        "textbox": ("给 ChatGPT 发消息",),
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
        "answer_selector": 'message-content, [data-response-id], [class*="response"], [class*="markdown"]',
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
    usable = [candidate.strip() for candidate in candidates if candidate.strip()]
    if not usable:
        return ""
    if platform == "perplexity":
        return max(usable, key=len)
    return usable[-1]


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
        textbox_ref = self._find_textbox_ref(page_text, config["textbox"])
        if not textbox_ref:
            final_url = str(navigation.get("final_url", ""))
            if "sign_in" in final_url or any(
                marker in page_text for marker in config["login_markers"]
            ):
                return BrowserProbeOutput(login_required=True)
            raise RuntimeError(f"message textbox not found for {platform}")

        before_url = str(navigation.get("final_url") or navigation.get("url") or config["url"])
        sent = False
        for _ in range(3):
            await self._enter_prompt(session_id, platform, textbox_ref, prompt, timeout=timeout)
            await self._submit_prompt(
                session_id, platform, textbox_ref, timeout=timeout
            )
            await asyncio.sleep(self.poll_interval)
            after_url = await self._current_url(session_id)
            answer_page = await self._answer_page(session_id, config["answer_selector"])
            answer_started = bool(
                select_main_answer(platform, [str(value) for value in answer_page.get("answers", [])])
            )
            if submission_confirmed(
                platform, before_url, after_url, answer_started=answer_started
            ):
                sent = True
                break
        if not sent:
            return BrowserProbeOutput(
                failure=FailureKind.SEND_FAILED,
                diagnostic="conversation URL did not change after 3 attempts",
            )

        deadline = asyncio.get_running_loop().time() + timeout
        previous = ""
        stable_rounds = 0
        empty_rounds = 0
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(self.poll_interval)
            page = await self._answer_page(session_id, config["answer_selector"])
            candidates = [str(value) for value in page.get("answers", [])]
            current_text = select_main_answer(platform, candidates)
            if not current_text:
                empty_rounds += 1
                if empty_rounds >= 10:
                    return BrowserProbeOutput(
                        failure=FailureKind.EXTRACTION_FAILED,
                        diagnostic="answer area remained empty for 10 polls",
                    )
                continue
            empty_rounds = 0
            lowered = current_text.casefold()
            if "429" in lowered or "rate limit" in lowered or "请求过于频繁" in current_text:
                return BrowserProbeOutput(
                    failure=FailureKind.RATE_LIMITED, diagnostic="platform rate limit detected"
                )
            if current_text == previous:
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
        raise asyncio.TimeoutError

    async def _enter_prompt(
        self,
        session_id: str,
        platform: str,
        textbox_ref: str,
        prompt: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        try:
            await self._run_json(
                "fill", textbox_ref, "--value", prompt, "--session", session_id, timeout=timeout
            )
            return
        except RuntimeError:
            if platform not in {"chatgpt", "perplexity"}:
                raise

        selector = PLATFORMS[platform]["composer_selector"]
        await self._run_json("click", selector, "--session", session_id, timeout=timeout)
        await self._run_json(
            "press", "Meta+A", "--selector", selector, "--session", session_id, timeout=timeout
        )
        await self._run_json("press", "Backspace", "--session", session_id, timeout=timeout)
        expression = f"document.execCommand('insertText', false, {json.dumps(prompt)})"
        await self._run_json("evaluate", expression, "--session", session_id, timeout=timeout)

    async def _submit_prompt(
        self,
        session_id: str,
        platform: str,
        textbox_ref: str,
        *,
        timeout: float = 30.0,
    ) -> None:
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
        await self._run_json(
            "press", "Enter", "--ref", textbox_ref, "--session", session_id, timeout=timeout
        )

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
            + ")]; return {answers:nodes.map(n => (n.innerText || '').trim()).filter(Boolean),"
            "links:[...new Set(nodes.flatMap(n => [...n.querySelectorAll('a[href]')].map(a => a.href)))]}; })()"
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
