"""Chromium over real API/LangGraph/temp DB; model and ASR adapters replaced only in fixture process."""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect


@pytest.fixture
def remix_app(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    code='''
import os,sys,sqlite3,asyncio
from pathlib import Path
root=Path(sys.argv[1]);port=int(sys.argv[2])
os.environ['MC_REMIX_API_KEY']='test-only-key'
os.environ['MC_REMIX_ASR']='cloud'
from api.main import app
from api.services import analytics,workbench_store
from api.services.remix import providers
from api.routers import workbench,data
from database import analytics as schema
from tests.test_content_remix import report
workbench_store.DB_PATH=root/'workbench.sqlite3'
analytics.sqlite_db_config['db_path']=str(root/'collection.db')
workbench.DATA_ROOT=root/'media';data.DATA_DIR=root/'media'
schema.migrate(analytics.db_path())
with sqlite3.connect(analytics.db_path()) as db:
    schema.save(db,schema.normalize(dict(platform='xhs',content_id='fixture-note',title='测试旅行笔记',author='测试作者',url='https://www.xiaohongshu.com/explore/fixture-note?xsec_token=fixture-browser-token&xsec_source=pc_search')))
    db.execute('CREATE TABLE xhs_note(note_id TEXT,desc TEXT)')
    db.execute('INSERT INTO xhs_note VALUES (?,?)',('fixture-note','提出路线问题，然后展示安排方法。'))
    schema.save(db,schema.normalize(dict(platform='xhs',content_id='fixture-note',comment_id='comment-1',body='路线怎么安排？'),kind='comment'),kind='comment')
async def model(evidence):
    await asyncio.sleep(.3)
    return report(evidence),{'total_tokens':123},'browser-fixture-model'
async def asr(audio,duration,folder):
    return [{'start':0,'end':.8,'text':'浏览器测试转写片段'}]
providers.analyze=model;providers.transcribe=asr
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=port,log_level='warning')
'''
    with (tmp_path/'server.log').open('w') as log:
        process=subprocess.Popen([sys.executable,'-c',code,str(tmp_path),str(port)],stdout=log,stderr=log)
        base=f'http://127.0.0.1:{port}'
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail((tmp_path/'server.log').read_text())
                try:
                    opener.open(base+'/api/health',timeout=1).close();break
                except OSError:
                    time.sleep(.1)
            else:
                pytest.fail('API not ready')
            yield base
        finally:
            process.terminate();process.wait(timeout=10)


def test_remix_text_and_video_browser(remix_app,tmp_path):
    evidence=Path('/tmp/mediacrawler-remix-proof');evidence.mkdir(exist_ok=True)
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=black:s=32x32:d=1','-f','lavfi','-i','sine=frequency=440:duration=1','-shortest','-y',str(tmp_path/'upload.mp4')],check=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('MEDIACRAWLER_TEST_BROWSER') or None,headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1100})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true');localStorage.setItem('mediacrawler_env_checked','true')")
        page.goto(remix_app)
        page.get_by_role('button',name='AI 内容分析',exact=True).click()
        expect(page.get_by_role('heading',name='ContentRemixAgent',exact=True)).to_be_visible()
        expect(page.get_by_role('button',name='开始分析',exact=True)).to_be_disabled()
        page.get_by_label('选择内容',exact=True).select_option('fixture-note')
        page.get_by_label('目标受众',exact=True).fill('第一次规划旅行的人')
        page.get_by_label('改写框架',exact=True).select_option('信息筛选')
        page.get_by_text('写作经验库 · 偏好、修改经验与确认规则',exact=True).click()
        page.get_by_label('经验标题',exact=True).fill('语气偏好')
        page.get_by_label('经验内容',exact=True).fill('直接表达具体条件，不使用夸张词')
        page.get_by_role('button',name='确认并保存经验',exact=True).click()
        expect(page.get_by_role('heading',name='语气偏好',exact=False)).to_be_visible()
        page.get_by_text('写作经验库 · 偏好、修改经验与确认规则',exact=True).click()
        page.get_by_role('checkbox',name='我同意',exact=False).check()
        page.get_by_role('button',name='开始分析',exact=True).click()
        # Leaving and reloading does not cancel the job; select its durable history.
        page.get_by_role('button',name='数据分析',exact=True).click()
        page.reload()
        page.get_by_role('button',name='AI 内容分析',exact=True).click()
        page.locator('.remix-job').first.click()
        expect(page.get_by_role('heading',name='内容摘要',exact=True)).to_be_visible(timeout=15000)
        expect(page.get_by_role('heading',name='评论样本分析',exact=True)).to_be_visible()
        expect(page.get_by_role('heading',name='完整改写稿',exact=True)).to_be_visible()
        page.get_by_text('本次分析范围与写作经验',exact=True).click()
        expect(page.get_by_text('语气偏好 · v1',exact=True)).to_be_visible()
        page.get_by_label('成稿标题',exact=True).fill('人工确认的标题')
        page.get_by_label('改写正文',exact=True).fill('人工修改后的正文，明确路线条件。')
        page.get_by_label('修改反馈',exact=True).fill('以后将适用条件提前说明')
        page.get_by_role('button',name='保存成稿版本',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='已保存新版本')).to_be_visible()
        page.get_by_text('人工确认的标题 ·',exact=False).click()
        page.get_by_role('button',name='采用并保存到草稿',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='已采用此版本')).to_be_visible()
        page.get_by_role('button',name='确认修改经验',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='已将你的修改反馈')).to_be_visible()
        page.get_by_role('button',name='引用 E0001',exact=True).first.click()
        expect(page.get_by_role('complementary',name='引用原文')).to_contain_text('测试旅行笔记')
        # The original-link redirect must preserve the saved signing parameters.
        # Intercept the initial local navigation, before Chromium follows the
        # redirect. Destination routing does not cover every redirect hop.
        from urllib.parse import parse_qs, urlsplit
        original_requests=[]
        def original_note(route):
            response=route.fetch(max_redirects=0)
            assert response.status==302
            target=urlsplit(response.headers['location'])
            assert target.hostname=='www.xiaohongshu.com' and target.path=='/explore/fixture-note'
            original_requests.append(parse_qs(target.query))
            route.fulfill(status=200,content_type='text/html; charset=utf-8',body='<meta charset="utf-8"><h1>测试原始笔记</h1>')
        page.context.route('https://www.xiaohongshu.com/**',lambda route:route.abort())
        page.context.route('**/api/remix/jobs/*/source-link',original_note)
        with page.expect_popup() as popup:
            page.get_by_role('link',name='查看原始小红书内容',exact=False).click()
        expect(popup.value.get_by_role('heading',name='测试原始笔记')).to_be_visible()
        assert original_requests[0]['xsec_token']==['fixture-browser-token']
        popup.value.close()
        with page.expect_download() as download:
            page.get_by_role('button',name='导出 Markdown',exact=True).click()
        assert 'E0001' in Path(download.value.path()).read_text()
        page.get_by_role('button',name='保存提纲到草稿',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='已保存到草稿预览')).to_be_visible()
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(evidence/'desktop.png'),full_page=True)
        page.get_by_role('button',name='上传视频',exact=True).click()
        page.get_by_label('视频文件',exact=False).set_input_files(tmp_path/'upload.mp4')
        page.get_by_role('checkbox',name='我同意',exact=False).check()
        page.get_by_role('button',name='开始分析',exact=True).click()
        expect(page.get_by_role('heading',name='原视频与转写引用',exact=True)).to_be_visible(timeout=15000)
        page.get_by_text('查看完整转写',exact=True).click()
        expect(page.get_by_text('浏览器测试转写片段',exact=False)).to_be_visible()
        page.get_by_role('button',name='0:00',exact=False).click()
        expect(page.get_by_role('button',name='跳转视频',exact=False)).to_be_visible()
        page.get_by_role('button',name='跳转视频',exact=False).click()
        for width,height,name in [(768,1024,'tablet'),(390,844,'mobile')]:
            page.set_viewport_size({'width':width,'height':height})
            page.evaluate('window.scrollTo(0,0)')
            page.screenshot(path=str(evidence/(name+'.png')),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        # Missing configuration has no fake report or enabled submit path.
        page.route('**/api/remix/config',lambda route:route.fulfill(status=200,content_type='application/json',body='{"model":"not-configured","model_ready":false,"issues":["尚未配置 MC_REMIX_API_KEY"],"asr":"local","asr_ready":false,"ffmpeg_ready":false,"cloud_audio":false}'))
        page.get_by_role('button',name='刷新配置与素材',exact=True).click()
        expect(page.get_by_text('尚未配置 MC_REMIX_API_KEY',exact=True)).to_be_visible()
        expect(page.get_by_role('button',name='开始分析',exact=True)).to_be_disabled()
        assert not errors,errors
        browser.close()
