"""Customer-scoped collection across explicitly assigned browser instances."""
from __future__ import annotations

import asyncio
import fcntl
import inspect
import hashlib
import json
import os
import re
import shlex
import signal
from contextlib import ExitStack
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .adapters.browser_skill import PLATFORMS
from .models import BuyerBaseline, StrictModel

REPO = Path(__file__).resolve().parents[2]
POLICY = 'ordinary_chat_or_search_only'


class BatchSchedule(StrEnum):
    SAME_PLATFORM = 'same-platform'
    PLATFORM_PER_INSTANCE = 'platform-per-instance'


def identifier(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', value):
        raise ValueError('Identifiers must use letters, numbers, dots, underscores or hyphens')
    return value


class BatchQuestion(StrictModel):
    id: str
    prompt: str = Field(min_length=1)

    _id = field_validator('id')(identifier)

    @field_validator('prompt')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Question cannot be blank')
        return value.strip()


class BatchSpec(StrictModel):
    customer_id: str
    batch_id: str
    schedule: BatchSchedule
    browsers: list[str] = Field(min_length=1)
    platforms: list[str] = Field(min_length=1)
    questions: list[BatchQuestion] = Field(min_length=1)
    assignments: dict[str, str] = Field(default_factory=dict)
    buyer_baseline: BuyerBaseline = BuyerBaseline.ENGLISH
    gemini_mode: Literal['auto', 'flash', 'flash-lite'] = 'auto'
    timeout: float = Field(default=420, ge=1, le=1800)

    _customer = field_validator('customer_id', 'batch_id')(identifier)

    @model_validator(mode='after')
    def unambiguous(self):
        for browser in self.browsers:
            identifier(browser)
        for values in (self.browsers, self.platforms, [q.id for q in self.questions]):
            if len(values) != len(set(values)):
                raise ValueError('Duplicate instance, platform or question identifiers')
        if set(self.platforms) - PLATFORMS.keys():
            raise ValueError('Unknown platform')
        if self.schedule == BatchSchedule.PLATFORM_PER_INSTANCE:
            if set(self.assignments) != set(self.browsers):
                raise ValueError('Every instance needs an explicit platform assignment')
            if sorted(self.assignments.values()) != sorted(self.platforms):
                raise ValueError('Assign each platform to exactly one instance')
        elif self.assignments:
            raise ValueError('same-platform does not accept fixed assignments')
        return self

    def plan(self):
        cells = []
        for platform in self.platforms:
            browsers = self.browsers if self.schedule == BatchSchedule.SAME_PLATFORM else [
                b for b in self.browsers if self.assignments[b] == platform]
            for question in self.questions:
                for browser in browsers:
                    cells.append(dict(customer_id=self.customer_id, batch_id=self.batch_id,
                        schedule=self.schedule.value, browser_id=browser, platform=platform,
                        question_id=question.id, prompt=question.prompt,
                        cell_id=hashlib.sha256(json.dumps([browser, platform, question.id]).encode()).hexdigest()))
        return dict(customer_id=self.customer_id, batch_id=self.batch_id,
                    schedule=self.schedule.value, sampling_mode_policy=POLICY,
                    sample_count=len(cells), cells=cells)


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path: Path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def complete(record):
    result = record.get('result', {})
    attempts = result.get('attempts', [])
    return (result.get('prompt') == record['prompt'] and len(attempts) == 1
        and attempts[0].get('platform') == record['platform']
        and attempts[0].get('query_sent') == record['prompt']
        and attempts[0].get('status') == 'succeeded'
        and bool((attempts[0].get('raw_answer') or '').strip())
        and attempts[0].get('source_capture_status') in {'captured', 'none_exposed'})


async def connected_instances():
    proc = await asyncio.create_subprocess_exec('bsk', 'browsers', '--json',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), 30)
    if proc.returncode:
        raise ValueError(f'Browser inventory unavailable: {err.decode(errors="replace")}')
    return [r['instance_id'] for r in json.loads(out) if not r.get('unresponsive')]


class BatchRunner:
    def __init__(self, spec: BatchSpec, output: Path, *, executor=None,
                 inventory=connected_instances, lock_root: Path | None = None, retry_failed: bool = False):
        self.spec = spec
        self.output = Path(output).resolve()
        self.executor = executor or self._execute
        self.inventory = inventory
        self.retry_failed = retry_failed
        self.lock_root = lock_root or REPO / 'data/batch-instance-locks'
        self.cells = spec.plan()['cells']
        self.records = {}

    def _event(self, kind, **fields):
        with (self.output/'timeline.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(dict(at=now(), kind=kind, **fields), ensure_ascii=False)+'\n')

    async def _execute(self, cell, lane):
        env = {k: v for k, v in os.environ.items() if not k.startswith('LOOK4GEO_')}
        env['LOOK4GEO_LOCAL_ENV'] = str(lane/'local.env')
        proc = await asyncio.create_subprocess_exec(str(REPO/'scripts/probe-local'), 'run',
            cell['prompt'], '--mode', 'manual', '--platform', cell['platform'],
            '--buyer-baseline', self.spec.buyer_baseline.value,
            '--timeout', str(self.spec.timeout), '--json', env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), self.spec.timeout+180)
        except BaseException:
            if proc.returncode is None:
                proc.send_signal(signal.SIGINT)
                try:
                    await asyncio.wait_for(proc.communicate(), 30)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.communicate()
            raise
        prefix = self.output/'cells'/cell['cell_id']
        prefix.with_name(prefix.name+'.stdout').write_bytes(stdout)
        prefix.with_name(prefix.name+'.stderr').write_bytes(stderr)
        if proc.returncode:
            return {'status': 'runner_error', 'diagnostic': stderr.decode(errors='replace')}
        return json.loads(stdout)

    async def _cell(self, cell):
        key = cell['cell_id']
        path = self.output/'cells'/f'{key}.json'
        pending = self.output/'cells'/f'{key}.pending.json'
        if path.exists():
            record = json.loads(path.read_text())
            if any(record.get(k) != v for k, v in cell.items()):
                raise ValueError(f'Checkpoint ownership mismatch: {key}')
            self.records[key] = record
            if complete(record) or not self.retry_failed:
                self._event('reuse' if complete(record) else 'blocked', cell_id=key)
                return complete(record)
            if pending.exists():
                self._event('blocked', cell_id=key, diagnostic='Unresolved pending action; inspect before retry')
                return False
            history = self.output/'history'
            history.mkdir(exist_ok=True)
            number = len(list(history.glob(f'{key}--*.json')))+1
            path.replace(history/f'{key}--{number}.json')
            for suffix in ('stdout', 'stderr'):
                log = self.output/'cells'/f'{key}.{suffix}'
                if log.exists():
                    log.replace(history/f'{key}--{number}.{suffix}')
        if pending.exists():
            self._event('blocked', cell_id=key, diagnostic='Prior operation interrupted; inspect before retry')
            return False
        started = now()
        save(pending, dict(**cell, started_at=started))
        self._event('start', **cell)
        try:
            result = await self.executor(cell, self.output/'instances'/cell['browser_id'])
        except Exception as error:
            result = {'status': 'runner_error', 'diagnostic': str(error)}
        record = dict(**cell, started_at=started, finished_at=now(), result=result)
        save(path, record)
        pending.unlink()
        self.records[key] = record
        valid = complete(record)
        self._event('finish', cell_id=key, successful=valid)
        return valid

    def _report(self):
        samples = [self.records[c['cell_id']] for c in self.cells if c['cell_id'] in self.records]
        good = sum(complete(r) for r in samples)
        report = dict(**self.spec.plan(), status='succeeded' if good == len(self.cells) else 'paused',
                      completed_samples=len(samples), successful_samples=good, samples=samples)
        save(self.output/'report.json', report)
        lines = [f'# {self.spec.customer_id} / {self.spec.batch_id}', '',
                 f'调度：{self.spec.schedule.value}；成功 {good}/{len(self.cells)}；状态 {report["status"]}。', '',
                 '仅普通对话/搜索，禁止 Work/Computer。各实例内部串行；失败暂停该实例，不自动补采。', '',
                 '| 实例 | 平台 | 题目 | 结果 | 原始记录 |', '|---|---|---|---|---|']
        for cell in self.cells:
            record = self.records.get(cell['cell_id'])
            state = 'succeeded' if record and complete(record) else 'failed' if record else 'not_collected'
            lines.append(f'| {cell["browser_id"]} | {cell["platform"]} | {cell["question_id"]} | {state} | [JSON](cells/{cell["cell_id"]}.json) |')
        (self.output/'REPORT.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
        return report

    async def run(self):
        self.output.mkdir(parents=True, exist_ok=True)
        self.lock_root.mkdir(parents=True, exist_ok=True)
        with ExitStack() as locks:
            output_lock = locks.enter_context((self.output/'.batch.lock').open('a+'))
            try:
                fcntl.flock(output_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError('Output directory already belongs to an active batch') from error
            # A browser cannot be assigned to two customer batches concurrently.
            for browser in sorted(self.spec.browsers):
                handle = locks.enter_context((self.lock_root/f'{browser}.lock').open('a+'))
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ValueError(f'Instance already belongs to an active batch: {browser}') from error
            manifest = self.output/'spec.json'
            payload = self.spec.model_dump(mode='json')
            if manifest.exists() and json.loads(manifest.read_text()) != payload:
                raise ValueError('Output belongs to a different batch specification')
            if not manifest.exists() and any(p.name != '.batch.lock' for p in self.output.iterdir()):
                raise ValueError('Output is not an empty batch directory')
            inventory = self.inventory()
            connected = await inventory if inspect.isawaitable(inventory) else inventory
            if missing := set(self.spec.browsers)-set(connected):
                raise ValueError(f'Instance disconnected; no substitution allowed: {sorted(missing)}')
            save(manifest, payload)
            (self.output/'cells').mkdir(exist_ok=True)
            for browser in self.spec.browsers:
                lane = self.output/'instances'/browser
                lane.mkdir(parents=True, exist_ok=True)
                env = dict(LOOK4GEO_BROWSER_ID=browser, LOOK4GEO_CHATGPT_BROWSER_ID=browser,
                    LOOK4GEO_GEMINI_BROWSER_ID=browser, LOOK4GEO_LOCKED_BROWSER_ID=browser,
                    LOOK4GEO_CAMOUFOX_ENABLED='0', LOOK4GEO_GEMINI_MODE=self.spec.gemini_mode,
                    LOOK4GEO_RUN_ROOT=str(lane), LOOK4GEO_BSK_TRACE=str(lane/'bsk-trace.jsonl'))
                (lane/'local.env').write_text(''.join(f'{k}={shlex.quote(v)}\n' for k,v in env.items()))
            self._event('batch_start', schedule=self.spec.schedule.value)
            try:
                if self.spec.schedule == BatchSchedule.SAME_PLATFORM:
                    paused = set()
                    for platform in self.spec.platforms:
                        for question in self.spec.questions:
                            wave = [c for c in self.cells if c['platform']==platform
                                    and c['question_id']==question.id and c['browser_id'] not in paused]
                            async with asyncio.TaskGroup() as group:
                                tasks = [group.create_task(self._cell(c)) for c in wave]
                            outcomes = [task.result() for task in tasks]
                            paused.update(c['browser_id'] for c,ok in zip(wave,outcomes) if not ok)
                else:
                    async def lane(browser):
                        for cell in self.cells:
                            if cell['browser_id']==browser and not await self._cell(cell):
                                break
                    async with asyncio.TaskGroup() as group:
                        for browser in self.spec.browsers:
                            group.create_task(lane(browser))
                self._event('batch_finish')
            finally:
                report = self._report()
            return report
