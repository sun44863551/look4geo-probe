import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from look4geo_probe.cli import app


def config(schedule='same-platform'):
    return dict(customer_id='customer-A', batch_id='batch-1', schedule=schedule,
                browsers=['a', 'b', 'c'], platforms=['chatgpt', 'gemini', 'perplexity'],
                assignments={'a': 'chatgpt', 'b': 'gemini', 'c': 'perplexity'} if schedule == 'platform-per-instance' else {},
                questions=[{'id': 'Q1', 'prompt': 'first question'}, {'id': 'Q2', 'prompt': 'second question'}])


def answer(cell):
    return {'job_id': cell['cell_id'], 'status': 'succeeded', 'prompt': cell['prompt'],
            'attempts': [{'platform': cell['platform'], 'status': 'succeeded',
                          'raw_answer': 'answer', 'query_sent': cell['prompt'],
                          'source_capture_status': 'none_exposed'}]}


def test_cli_batch_plan_supports_both_shapes_without_browser_access(tmp_path):
    for mode, count in [('same-platform', 18), ('platform-per-instance', 6)]:
        path = tmp_path / f'{mode}.json'
        path.write_text(json.dumps(config(mode)))
        result = CliRunner().invoke(app, ['batch', '--spec', str(path), '--plan', '--json'])
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload['sample_count'] == count
        assert payload['customer_id'] == 'customer-A'
        assert payload['sampling_mode_policy'] == 'ordinary_chat_or_search_only'


@pytest.mark.asyncio
@pytest.mark.parametrize('schedule', ['same-platform', 'platform-per-instance'])
async def test_parallel_schedules_keep_one_operation_per_browser_and_resume(tmp_path, schedule):
    from look4geo_probe.batch import BatchRunner, BatchSpec
    spec = BatchSpec.model_validate(config(schedule))
    active = {}
    waves = []
    calls = []
    barriers = {}
    arrivals = {}
    async def execute(cell, lane):
        browser = cell['browser_id']
        assert browser not in active
        active[browser] = cell
        calls.append(cell)
        key = (cell['platform'], cell['question_id']) if schedule == 'same-platform' else cell['question_id']
        arrivals.setdefault(key, []).append(cell)
        if len(arrivals[key]) == 3:
            group = arrivals[key]
            assert len({c['browser_id'] for c in group}) == 3
            assert len({c['platform'] for c in group}) == (1 if schedule == 'same-platform' else 3)
            waves.append(group)
        barrier = barriers.setdefault(key, asyncio.Barrier(3))
        await asyncio.wait_for(barrier.wait(), 1)
        del active[browser]
        return answer(cell)
    root = tmp_path / 'run'
    runner = BatchRunner(spec, root, executor=execute, inventory=lambda: ['a', 'b', 'c'], lock_root=tmp_path/'locks')
    report = await runner.run()
    expected = 18 if schedule == 'same-platform' else 6
    assert report['successful_samples'] == expected
    assert len(calls) == expected
    assert len(waves) == expected // 3
    assert all(c['customer_id'] == 'customer-A' and c['batch_id'] == 'batch-1' for c in calls)
    assert (root / 'REPORT.md').exists()
    resumed = await runner.run()
    assert resumed['successful_samples'] == expected
    assert len(calls) == expected, 'Successful cells must not be resubmitted'
    for browser in spec.browsers:
        env = (root / 'instances' / browser / 'local.env').read_text()
        assert f'LOOK4GEO_BROWSER_ID={browser}\n' in env
        assert f'LOOK4GEO_GEMINI_BROWSER_ID={browser}\n' in env
        assert 'LOOK4GEO_CAMOUFOX_ENABLED=0' in env
        assert str(root / 'instances' / browser) in env


@pytest.mark.asyncio
async def test_batch_rejects_different_customer_resume_and_offline_instance(tmp_path):
    from look4geo_probe.batch import BatchRunner, BatchSpec
    spec = BatchSpec.model_validate(config())
    calls = []
    async def execute(cell, lane):
        calls.append(cell)
        return answer(cell)
    root = tmp_path / 'run'
    with pytest.raises(ValueError, match='disconnected'):
        await BatchRunner(spec, root, executor=execute, inventory=lambda: ['a', 'b'], lock_root=tmp_path/'locks').run()
    assert not calls
    await BatchRunner(spec, root, executor=execute, inventory=lambda: spec.browsers, lock_root=tmp_path/'locks').run()
    other = spec.model_copy(update={'customer_id': 'other'})
    with pytest.raises(ValueError, match='specification'):
        await BatchRunner(other, root, executor=execute, inventory=lambda: spec.browsers, lock_root=tmp_path/'locks').run()
    assert len(calls) == 18


@pytest.mark.asyncio
async def test_failed_or_interrupted_cells_are_not_blindly_replayed(tmp_path):
    from look4geo_probe.batch import BatchRunner, BatchSpec
    spec = BatchSpec.model_validate(config('platform-per-instance'))
    calls = []
    async def execute(cell, lane):
        calls.append(cell)
        return {'status': 'failed', 'attempts': [{'platform': cell['platform'], 'status': 'failed',
                    'diagnostic': 'Sampling mode blocked: Work active'}]}
    runner = BatchRunner(spec, tmp_path/'run', executor=execute, inventory=lambda: spec.browsers, lock_root=tmp_path/'locks')
    result = await runner.run()
    assert result['successful_samples'] == 0
    assert len(calls) == 3, 'A failed cell must pause its instance lane'
    await runner.run()
    assert len(calls) == 3, 'Failures need explicit investigation before retry, not automatic resume'


@pytest.mark.parametrize('changes', [
    {'browsers': ['a','a','c']}, {'questions': [{'id':'../Q', 'prompt':'x'}]},
    {'assignments': {'a':'chatgpt', 'b':'gemini'}},
    {'assignments': {'a':'chatgpt', 'b':'chatgpt', 'c':'perplexity'}},
    {'platforms': ['work']},
])
def test_batch_rejects_unsafe_or_ambiguous_assignment(changes):
    from look4geo_probe.batch import BatchSpec
    payload = config('platform-per-instance') | changes
    with pytest.raises(ValueError):
        BatchSpec.model_validate(payload)


@pytest.mark.asyncio
async def test_fixed_assignment_lane_advances_without_waiting_for_slower_platform(tmp_path):
    from look4geo_probe.batch import BatchRunner, BatchSpec
    spec = BatchSpec.model_validate(config('platform-per-instance'))
    advanced = asyncio.Event()
    async def execute(cell, lane):
        if cell['browser_id'] == 'a' and cell['question_id'] == 'Q2':
            advanced.set()
        if cell['browser_id'] == 'b' and cell['question_id'] == 'Q1':
            await asyncio.wait_for(advanced.wait(), 1)
        return answer(cell)
    result = await BatchRunner(spec, tmp_path/'run', executor=execute,
        inventory=lambda: spec.browsers, lock_root=tmp_path/'locks').run()
    assert result['successful_samples'] == 6


@pytest.mark.asyncio
async def test_two_customer_batches_cannot_share_an_instance(tmp_path):
    from look4geo_probe.batch import BatchRunner, BatchSpec
    spec = BatchSpec.model_validate(config('platform-per-instance'))
    started, finish = asyncio.Event(), asyncio.Event()
    async def execute(cell, lane):
        started.set()
        await finish.wait()
        return answer(cell)
    task = asyncio.create_task(BatchRunner(spec, tmp_path/'one', executor=execute,
        inventory=lambda: spec.browsers, lock_root=tmp_path/'locks').run())
    await started.wait()
    try:
        with pytest.raises(ValueError, match='active batch'):
            await BatchRunner(spec.model_copy(update={'customer_id':'other'}), tmp_path/'two',
                inventory=lambda: spec.browsers, lock_root=tmp_path/'locks').run()
    finally:
        finish.set()
        await task


@pytest.mark.asyncio
async def test_explicit_retry_retains_original_failed_record(tmp_path):
    from look4geo_probe.batch import BatchRunner, BatchSpec
    spec = BatchSpec.model_validate(config('platform-per-instance'))
    async def failed(cell, lane):
        return {'status':'failed', 'attempts':[]}
    root = tmp_path/'run'
    options = dict(inventory=lambda: spec.browsers, lock_root=tmp_path/'locks')
    await BatchRunner(spec, root, executor=failed, **options).run()
    async def recovered(cell, lane):
        return answer(cell)
    result = await BatchRunner(spec, root, executor=recovered, retry_failed=True, **options).run()
    assert result['successful_samples'] == 6
    old = list((root/'history').glob('*.json'))
    assert len(old) == 3
    assert all(json.loads(p.read_text())['result']['status'] == 'failed' for p in old)
