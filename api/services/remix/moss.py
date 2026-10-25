"""Offline BaoCut model-worker adapter. Never installs or downloads models."""
import asyncio
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path

BUNDLE_ID = 'moss-transcribe-diarize@mlx-8bit'
MODEL_NAME = 'MOSS-Transcribe-Diarize'
COMPONENTS = (
    ('asr', 'OpenMOSS-Team/MOSS-Transcribe-Diarize', 'moss-transcribe-diarize'),
    ('aligner', 'aufklarer/Qwen3-ForcedAligner-0.6B-4bit', 'qwen3-forced-aligner'),
)
MAX_RESULT_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT = 900


class MossError(ValueError):
    pass


def bundle_for(models_root):
    root = Path(models_root).expanduser().resolve()
    components = {}
    try:
        for name, repo, family in COMPONENTS:
            directory = (root / repo).resolve()
            if not directory.is_relative_to(root):
                raise ValueError('component path')
            manifest_path = directory / '.bcut-manifest.json'
            if manifest_path.stat().st_size > 1024 * 1024:
                raise ValueError('manifest size')
            manifest = json.loads(manifest_path.read_text())
            if manifest['repo'] != repo or not re.fullmatch(r'[a-f0-9]{40}', manifest['revision']):
                raise ValueError('manifest identity')
            files = []
            for item in manifest['files']:
                path = (directory / item['path']).resolve()
                if not path.is_relative_to(directory) or not path.is_file() or path.stat().st_size != item['size']:
                    raise ValueError('model file')
                if not re.fullmatch(r'[a-f0-9]{64}', item['sha256']):
                    raise ValueError('model digest')
                files.append(dict(path=item['path'], sha256=item['sha256'], byteLength=item['size']))
            names = {item['path'] for item in files}
            if not {'config.json', 'tokenizer_config.json'} <= names or not any(n.endswith('.safetensors') for n in names):
                raise ValueError('incomplete model')
            components[name] = dict(family=family, revision=manifest['revision'], dir=str(directory), files=files)
    except (OSError, ValueError, KeyError, TypeError):
        raise MossError('MOSS 模型或时间戳对齐器不完整，请检查 BaoCut 模型目录') from None
    return dict(bundleId=BUNDLE_ID, backend='mlx', device='metal', components=components,
                threads=4, memoryBudgetBytes=None)


def ready(worker, models_root):
    if not Path(worker).is_file() or not os.access(worker, os.X_OK):
        return False
    try:
        bundle_for(models_root)
        return True
    except MossError:
        return False


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MossError('MOSS 返回的时间戳格式无效')
    return value


def segments_from(data, duration):
    """Use aligned words to keep long speech in bounded, independently citable pieces."""
    try:
        if data['schema'] != 'baocut.asr-result/v1' or data['outcome'] != 'transcribed':
            raise MossError('MOSS 未返回有效转写')
        scale = number(data['timescale'])
        if scale <= 0 or not isinstance(data['segments'], list) or len(data['segments']) > 10000:
            raise MossError('MOSS 返回的转写格式无效')
        result = []
        previous = -1
        for segment in data['segments']:
            start, end = number(segment['start']) / scale, number(segment['end']) / scale
            if not 0 <= start < end <= duration + 1 or start < previous:
                raise MossError('MOSS 时间戳超出音频范围或顺序无效')
            previous = end
            words = segment.get('words')
            if not words:
                result.append(dict(start=start, end=end, text=segment['text']))
                continue
            if not isinstance(words, list) or len(words) > 100000:
                raise MossError('MOSS 字词时间戳格式无效')
            group = []
            count = 0
            last = start
            for word in words:
                a, b = number(word['start']) / scale, number(word['end']) / scale
                text = word['text']
                if not isinstance(text, str) or not 0 <= a < b <= duration + 1 or a < last or a < start or b > end:
                    raise MossError('MOSS 字词时间戳格式无效')
                last = b
                group.append(dict(start=a, end=b, text=text))
                count += len(text)
                if count >= 160 or text.rstrip().endswith(('。', '！', '？', '!', '?')):
                    result.append(dict(start=group[0]['start'], end=b, text=''.join(w['text'] for w in group)))
                    group = []
                    count = 0
            if group:
                result.append(dict(start=group[0]['start'], end=group[-1]['end'], text=''.join(w['text'] for w in group)))
        return result
    except (KeyError, TypeError, AttributeError):
        raise MossError('MOSS 返回的转写格式无效') from None


async def stop(process):
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 5)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def transcribe(audio, duration, folder, worker, models_root):
    bundle = bundle_for(models_root)
    audio = Path(audio).resolve()
    try:
        process = await asyncio.create_subprocess_exec(
            worker, '--parent-pid', str(os.getpid()), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=1024 * 1024)
    except OSError:
        raise MossError('无法启动 BaoCut model-worker，请检查 MC_REMIX_MOSS_WORKER') from None
    sequence = 0

    async def request(method, params, timeout):
        nonlocal sequence
        sequence += 1
        request_id = sequence

        async def exchange():
            process.stdin.write((json.dumps(dict(id=request_id, method=method, params=params)) + '\n').encode())
            await process.stdin.drain()
            for _ in range(100000):
                line = await process.stdout.readline()
                if not line:
                    raise MossError('MOSS 转写进程提前退出，请检查模型与 Metal 运行环境')
                reply = json.loads(line)
                if not isinstance(reply, dict):
                    raise MossError('MOSS worker 协议格式无效')
                if reply.get('id') != request_id:
                    if isinstance(reply.get('event'), str):
                        continue
                    raise MossError('MOSS worker 响应编号无效')
                if 'error' in reply:
                    raise MossError('MOSS worker 执行失败，请检查模型文件和 BaoCut 版本')
                return reply['result']
            raise MossError('MOSS worker 通知过多')

        return await asyncio.wait_for(exchange(), timeout)

    try:
        hello = await request('worker.hello', {'contractVersion': 2}, 30)
        if hello.get('contractVersion') != 2 or not any(
            b.get('id') == 'mlx' and b.get('available') and 'metal' in b.get('devices', [])
            for b in hello.get('backends', [])
        ):
            raise MossError('BaoCut worker 未提供兼容的 MLX／Metal 后端，请在本机非沙箱环境运行')
        loaded = await request('model.load', {'bundle': bundle}, 180)
        if not loaded.get('loaded'):
            raise MossError('MOSS 模型加载失败')
        with tempfile.TemporaryDirectory(prefix='moss-', dir=Path(folder).resolve()) as temporary:
            staging = Path(temporary).resolve()
            digest = await asyncio.to_thread(lambda: hashlib.sha256(audio.read_bytes()).hexdigest())
            # Reap the worker before staging is removed, even if job.run fails.
            try:
                result = await request('job.run', dict(
                    jobId='job_remix_moss', runGeneration=1, capability='transcribe',
                    input=dict(file=str(audio), contentHash='sha256:' + digest, track=0),
                    options=dict(language=dict(mode='prefer', tag='zh'), diarize=False, timescale=1000000),
                    staging=str(staging), outputContract='baocut.asr-result/v1'), REQUEST_TIMEOUT)
                if result['outcome'] != 'completed':
                    raise MossError('MOSS 转写未完成')
                output = result['output']
                target = (staging / output['path']).resolve()
                if not target.is_relative_to(staging) or not target.is_file() or target.stat().st_size > MAX_RESULT_BYTES:
                    raise MossError('MOSS 转写输出路径或大小无效')
                raw = target.read_bytes()
                if len(raw) != output['byteLength'] or hashlib.sha256(raw).hexdigest() != output['sha256'].removeprefix('sha256:'):
                    raise MossError('MOSS 转写输出校验失败')
                return segments_from(json.loads(raw), duration)
            finally:
                await stop(process)
    except asyncio.TimeoutError:
        raise MossError('MOSS 转写超时，请缩短视频后重试') from None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        if isinstance(exc, MossError):
            raise
        raise MossError('MOSS worker 通信或输出格式无效，请检查 BaoCut 版本') from None
    finally:
        await stop(process)
