"""Only isolated fixtures: template PNGs, immutable revisions, real subtitle files and ZIPs."""
import io
import json
import zipfile
from pathlib import Path

from PIL import Image
from pydantic import ValidationError
import pytest

from api.services.remix import production, store
from api.services.remix.schemas import ProductionInput, SubtitleCue, VersionInput
from tests.test_content_remix import isolated, client
from tests.test_remix_learning import finished


def payload(job, version, **updates):
    seed = production.seed(job['id'], version['id']); seed.pop('suggestions')
    seed.update(request_key='production-request-1234'); seed.update(updates)
    return ProductionInput(**seed)


def approved():
    return dict(copy_checked=True, facts=True, rights=True, subtitles=True)


def test_saved_artwork_and_bundle_are_frozen_and_never_mark_published(isolated):
    job, version = finished(None)
    item = production.save(job['id'], payload(job, version), 'fixture')
    assert item['published'] is False and item['status'] == 'prepared'
    assert len(production.listing(job['id'])) == 1
    assert 'spec' not in production.listing(job['id'])[0]
    assert production.save(job['id'], payload(job, version), 'fixture')['id'] == item['id']
    with pytest.raises(ValueError, match='核对'):
        production.bundle(item)
    checked = production.save(job['id'], payload(job, version, checks=approved(), request_key='checked-production-1234'), 'fixture')
    with zipfile.ZipFile(io.BytesIO(production.bundle(checked))) as z:
        assert {'cover.png','card-01.png','title.txt','body.txt','tags.txt','design.json','manifest.json','README.txt'} == set(z.namelist())
        assert z.read('body.txt').decode() == version['content']
        manifest = json.loads(z.read('manifest.json'))
        assert manifest['status'] == 'manual_handoff_only' and not manifest['published']
        assert not manifest['contains_original_video']
        assert manifest['version_id'] == version['id']
        for entry in manifest['artifacts']:
            assert production.digest(z.read(entry['name'])) == entry['sha256']
        image = Image.open(io.BytesIO(z.read('cover.png')))
        assert image.size == (1080,1440) and image.format == 'PNG'
    # Later saved/adopted copy cannot silently replace this revision's inputs.
    store.save_version(job['id'], VersionInput(rewrite_index=0,title='新的标题',content='后来修改正文',request_key='newer-version-1234'), 'fixture')
    assert production.get(checked['id'])['source_version_sha256'] == checked['source_version_sha256']
    assert production.asset(checked, 'body.txt').decode() == version['content']
    with pytest.raises(ValueError, match='请求键'):
        production.save(job['id'], payload(job, version, ratio='1:1'), 'fixture')
    assert not store.get(job['id']).get('published')


@pytest.mark.parametrize('ratio,size',[('1:1',(1080,1080)),('9:16',(1080,1920))])
def test_image_ratios_and_unicode_text(isolated, ratio, size):
    job, version = finished(None)
    item = production.save(job['id'],payload(job, version,ratio=ratio,theme='night'), 'fixture')
    image = Image.open(io.BytesIO(production.asset(item,'cover.png')))
    assert image.size == size
    assert len(image.getcolors(image.width*image.height)) > 10


def test_text_overflow_fails_without_partial_revision_or_silent_truncation(isolated):
    job, version = finished(None)
    spec = payload(job, version,ratio='1:1',cards=[dict(title='密集信息'*16,body='文\n'*440)] )
    with pytest.raises(ValueError,match='超出安全区域'):
        production.save(job['id'],spec,'fixture')
    assert not production.listing(job['id'])
    assert not (store.root()/'production').exists()


def test_complete_original_transcript_and_timestamp_boundaries(isolated):
    job, version = finished(None)
    cues = [dict(start=0,end=.999,text='第一句'),dict(start=1.005,end=2.125,text='<核对> & 正文')]
    store.update(job['id'],transcript=cues,duration=3,evidence=[])
    original = production.original_subtitles(job['id'])
    assert original['cues'] == cues and original['subtitle_duration'] == 3
    item=production.save(job['id'],payload(job,version,**original,checks=approved()),'fixture')
    assert production.asset(item,'original-video.srt').decode().startswith('1\n00:00:00,000 --> 00:00:00,999')
    assert '&lt;核对&gt; &amp; 正文' in production.asset(item,'original-video.srt').decode()
    assert production.asset(item,'original-video.vtt').decode().startswith('WEBVTT\n')
    with zipfile.ZipFile(io.BytesIO(production.bundle(item))) as z:
        assert json.loads(z.read('manifest.json'))['subtitle_basis'] == 'original_video'
        assert '不能用于改写稿' in z.read('README.txt').decode()


@pytest.mark.parametrize('cues,duration',[
    ([dict(start=0,end=1,text='一'),dict(start=.5,end=2,text='二')],3),
    ([dict(start=0,end=4,text='超长')],3),
    ([dict(start=.0001,end=.0002,text='零毫秒')],3),
    ([dict(start=0,end=1,text='A\n\nB')],3),
    ([dict(start=0,end=1,text='00 --> 01')],3),
    ([dict(start=0,end=1,text='  ')],3),
    ([dict(start=0,end=1,text='一')],None),
])
def test_subtitle_invalid_ranges_and_block_injection(isolated,cues,duration):
    job, version = finished(None)
    with pytest.raises(ValueError):
        production.save(job['id'],payload(job,version,subtitle_basis='manual_timeline',subtitle_duration=duration,cues=cues),'fixture')
    assert not production.listing(job['id'])


def test_cannot_invent_original_timecodes_or_mislabel_disabled_subtitles(isolated):
    job, version = finished(None)
    with pytest.raises(ValueError,match='没有'):
        production.original_subtitles(job['id'])
    cues=[dict(start=0,end=1,text='手动录制')]
    with pytest.raises(ValueError,match='原视频'):
        production.save(job['id'],payload(job,version,subtitle_basis='original_video',subtitle_duration=2,cues=cues),'fixture')
    with pytest.raises(ValueError,match='未启用字幕'):
        production.save(job['id'],payload(job,version,cues=cues),'fixture')
    manual = production.save(job['id'],payload(job,version,subtitle_basis='manual_timeline',subtitle_duration=2,cues=cues,checks=approved()),'fixture')
    assert production.asset(manual,'manual-video.srt')
    with pytest.raises(ValueError):production.asset(manual,'original-video.srt')


def test_corrupt_and_symlinked_assets_fail_closed(isolated):
    job, version = finished(None)
    item = production.save(job['id'],payload(job,version,checks=approved()),'fixture')
    path=production.folder(item['id'])/'cover.png'
    path.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='校验失败'):production.bundle(item)
    path.unlink();path.symlink_to(isolated/'workbench.sqlite3')
    with pytest.raises(ValueError,match='路径无效'):production.asset(item,'cover.png')
    with pytest.raises(ValueError,match='不存在'):production.asset(item,'../analysis.sqlite3')


def test_api_preview_export_and_missing_records(client):
    job, version = finished(None)
    assert client.get('/api/remix/production/config').json()['ready']
    seed=client.get(f"/api/remix/jobs/{job['id']}/production-seed",params={'version_id':version['id']}).json()
    seed.pop('suggestions'); seed['request_key']='api-production-1234'
    response=client.post(f"/api/remix/jobs/{job['id']}/productions",json=seed)
    assert response.status_code==200,response.text
    item=response.json();base='/api/remix/productions/'+item['id']
    assert client.get(base).json()['spec']['version_id']==version['id']
    image=client.get(base+'/assets/cover.png')
    assert image.status_code==200 and image.headers['content-type']=='image/png'
    assert image.headers['cache-control']=='no-store'
    assert client.get(base+'/manual-package.zip').status_code==409
    seed.update(checks=approved(),request_key='api-checked-production')
    checked=client.post(f"/api/remix/jobs/{job['id']}/productions",json=seed).json()
    assert client.get('/api/remix/productions/'+checked['id']+'/manual-package.zip').status_code==200
    assert client.get('/api/remix/productions/'+'f'*32).status_code==404
    assert client.get('/api/remix/jobs/'+job['id']+'/original-subtitles').status_code==409


def test_nonfinite_times_and_excessive_pages_rejected():
    with pytest.raises(ValidationError):SubtitleCue(start=0,end=float('nan'),text='不可用')
    with pytest.raises(ValidationError):ProductionInput(version_id='a'*32,cover={'title':'A'},cards=[{'title':'A'}]*9,request_key='too-many-pages-1234')


def test_font_unavailable_is_actionable_without_silent_latin_fallback(monkeypatch):
    monkeypatch.setattr(production.Path,'is_file',lambda self:False)
    assert production.status()['ready'] is False
    assert 'MC_REMIX_DESIGN_FONT' in production.status()['issue']
