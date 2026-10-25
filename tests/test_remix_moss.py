"""Real subprocess protocol fixtures; no network, accounts or model downloads."""
import asyncio
import hashlib
import json
import os
import sys

import pytest

from api.services.remix import moss, providers


@pytest.fixture
def models(tmp_path):
    root = tmp_path / 'models'
    for _, repo, _ in moss.COMPONENTS:
        folder = root / repo
        folder.mkdir(parents=True)
        files = []
        for name in ['config.json', 'tokenizer_config.json', 'model.safetensors']:
            raw = b'fixture'
            (folder / name).write_bytes(raw)
            files.append(dict(path=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        (folder / '.bcut-manifest.json').write_text(json.dumps(dict(repo=repo, revision='a' * 40, files=files)))
    return root


@pytest.fixture
def worker(tmp_path):
    def create(mode='ok'):
        script = tmp_path / 'worker'
        script.write_text('#!' + sys.executable + '\n' + 'MODE = ' + repr(mode) + '\n' + '''
import sys,json,hashlib,pathlib,os,time
for line in sys.stdin:
    req=json.loads(line);method=req['method'];params=req['params']
    if method=='worker.hello':
        result={'contractVersion':2,'backends':[{'id':'mlx','available':MODE!='no-metal','devices':['metal']}]}
    elif method=='model.load':
        assert set(params['bundle']['components'])=={'asr','aligner'}
        result={'loaded':True}
    elif method=='job.run':
        staging=pathlib.Path(params['staging'])
        (staging.parent/'worker.pid').write_text(str(os.getpid()))
        if MODE=='slow':
            time.sleep(30)
        if MODE=='events':
            for _ in range(100):
                print(json.dumps({'event':'job.progress','params':{}}),flush=True)
                time.sleep(.01)
        if MODE=='crash':sys.exit(3)
        if MODE=='error':
            print(json.dumps({'id':req['id'],'error':{'code':'TEST','message':'private worker diagnostic'}}),flush=True)
            continue
        assert params['options']['diarize'] is False
        data={'schema':'baocut.asr-result/v1','outcome':'transcribed','timescale':1000000,
              'segments':[{'start':100000,'end':1800000,'text':'你好。测试。','words':[
                  {'start':100000,'end':700000,'text':'你好。'},
                  {'start':900000,'end':1800000,'text':'测试。'}]}]}
        target=staging/'result.json';raw=json.dumps(data,ensure_ascii=False).encode();target.write_bytes(raw)
        if MODE=='path':target=staging.parent/'outside.json';target.write_bytes(raw)
        if MODE=='symlink':
            outside=staging.parent/'outside.json';outside.write_bytes(raw);target.unlink();target.symlink_to(outside)
        result={'outcome':'completed','output':{'path':str(target),'byteLength':len(raw),
                'sha256':'0'*64 if MODE=='digest' else hashlib.sha256(raw).hexdigest()}}
        print(json.dumps({'event':'job.phase','params':{}}),flush=True)
    else:sys.exit(2)
    print(json.dumps({'id':req['id'],'result':result}),flush=True)
''')
        script.chmod(0o700)
        return str(script)
    return create


def audio(tmp_path):
    path = tmp_path / 'audio.wav'
    path.write_bytes(b'fixture audio')
    return path


def assert_reaped(tmp_path):
    pid = int((tmp_path / 'worker.pid').read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not list(tmp_path.glob('moss-*'))


@pytest.mark.asyncio
async def test_real_worker_protocol_to_provider_and_cleanup(models, worker, tmp_path, monkeypatch):
    monkeypatch.setenv('MC_REMIX_ASR', 'moss')
    monkeypatch.setenv('MC_REMIX_MOSS_MODELS', str(models))
    monkeypatch.setenv('MC_REMIX_MOSS_WORKER', worker())
    monkeypatch.delenv('MC_REMIX_API_KEY', raising=False)
    status = providers.status()
    assert status['asr_ready'] and status['asr_model'] == moss.MODEL_NAME
    assert not status['cloud_audio'] and not status['model_ready']
    result = await providers.transcribe(audio(tmp_path), 2, tmp_path)
    assert result == [dict(start=.1, end=.7, text='你好。'), dict(start=.9, end=1.8, text='测试。')]
    assert_reaped(tmp_path)


@pytest.mark.parametrize('mode', ['path', 'symlink', 'digest', 'crash', 'error', 'no-metal'])
@pytest.mark.asyncio
async def test_worker_failure_and_untrusted_output(models, worker, tmp_path, mode):
    with pytest.raises(moss.MossError) as exc:
        await moss.transcribe(audio(tmp_path), 2, tmp_path, worker(mode), models)
    assert 'private worker diagnostic' not in str(exc.value)
    assert not list(tmp_path.glob('moss-*'))
    if mode != 'no-metal':
        assert_reaped(tmp_path)


@pytest.mark.parametrize('mode', ['slow', 'events'])
@pytest.mark.asyncio
async def test_timeout_reaps_worker_even_with_progress(models, worker, tmp_path, monkeypatch, mode):
    monkeypatch.setattr(moss, 'REQUEST_TIMEOUT', .08)
    with pytest.raises(moss.MossError, match='超时'):
        await moss.transcribe(audio(tmp_path), 2, tmp_path, worker(mode), models)
    assert_reaped(tmp_path)


@pytest.mark.asyncio
async def test_cancellation_reaps_worker(models, worker, tmp_path):
    task = asyncio.create_task(moss.transcribe(audio(tmp_path), 2, tmp_path, worker('slow'), models))
    for _ in range(100):
        if (tmp_path / 'worker.pid').exists():
            break
        await asyncio.sleep(.01)
    else:
        pytest.fail('fixture worker did not reach job.run')
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_reaped(tmp_path)


def test_model_readiness_requires_aligner_and_rejects_escape(models, worker, tmp_path):
    executable = worker()
    assert moss.ready(executable, models)
    folder = models / moss.COMPONENTS[1][1]
    manifest_path = folder / '.bcut-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files'][0]['path'] = '../outside.json'
    manifest_path.write_text(json.dumps(manifest))
    assert not moss.ready(executable, models)
    with pytest.raises(moss.MossError):
        moss.bundle_for(models)


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf'), True, '1000000'])
def test_invalid_timescale(value):
    with pytest.raises(moss.MossError):
        moss.segments_from(dict(schema='baocut.asr-result/v1', outcome='transcribed', timescale=value, segments=[]), 2)


def test_long_segments_split_with_real_word_timestamps():
    words = [dict(start=i*10000, end=(i+1)*10000, text='字') for i in range(2200)]
    segment = dict(start=0, end=22000000, text='字'*2200, words=words)
    data = dict(schema='baocut.asr-result/v1', outcome='transcribed', timescale=1000000, segments=[segment])
    result = providers.validate_segments(moss.segments_from(data, 22), 22)
    assert ''.join(s['text'] for s in result) == '字'*2200
    assert max(len(s['text']) for s in result) <= 160
    assert result[-1]['end'] == 22
    data['segments'][0]['words'][1]['start'] = -1
    with pytest.raises(moss.MossError):
        moss.segments_from(data, 22)
