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
        page.get_by_text('管理账号画像与版本',exact=True).click()
        page.get_by_label('画像名称',exact=True).fill('旅行账号')
        page.get_by_label('画像题材',exact=True).fill('旅行')
        page.get_by_label('账号定位',exact=True).fill('面向第一次规划旅行的人提供具体信息')
        page.get_by_role('button',name='创建账号画像',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='画像已保存')).to_be_visible()
        expect(page.get_by_label('当前账号画像',exact=True)).not_to_have_value('')
        page.get_by_text('管理账号画像与版本',exact=True).click()
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
        expect(page.get_by_role('heading',name='评论议题表',exact=True)).to_be_visible()
        expect(page.get_by_text('本次账号画像：旅行账号 · v1（冻结快照）',exact=True)).to_be_visible()
        expect(page.get_by_role('heading',name='完整改写稿',exact=True)).to_be_visible()
        page.get_by_text('本次分析范围与写作经验',exact=True).click()
        expect(page.get_by_text('语气偏好 · v1 · 账号经验',exact=True)).to_be_visible()
        page.get_by_label('成稿标题',exact=True).fill('人工确认的标题')
        page.get_by_label('改写正文',exact=True).fill('人工修改后的正文，明确路线条件。')
        page.get_by_label('修改反馈',exact=True).fill('以后将适用条件提前说明')
        page.get_by_role('button',name='保存成稿版本',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='已保存新版本')).to_be_visible()
        page.locator('summary').filter(has_text='人工确认的标题 ·').click()
        page.get_by_role('button',name='采用并保存到草稿',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='已采用此版本')).to_be_visible()
        page.get_by_role('button',name='确认修改经验',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='已将你的修改反馈')).to_be_visible()
        from datetime import datetime,timedelta
        published_local=(datetime.now().astimezone()-timedelta(hours=73)).strftime('%Y-%m-%dT%H:%M')
        observed_local=(datetime.now().astimezone()-timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
        page.get_by_text('回填一条已发布作品',exact=True).click()
        page.get_by_label('实际发布的成稿版本',exact=True).select_option(label='已采用 · 人工确认的标题')
        page.get_by_label('作品公开链接',exact=True).fill('https://www.xiaohongshu.com/explore/fixture-published')
        page.get_by_label('实际发布时间',exact=True).fill(published_local)
        page.get_by_role('button',name='确认已发布并回填',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='已记录人工发布结果')).to_be_visible()
        page.get_by_text('添加效果观测',exact=True).click()
        page.get_by_label('数据观测时间',exact=True).fill(observed_local)
        page.get_by_label('收藏数',exact=True).fill('20')
        page.get_by_label('阅读／播放数',exact=True).fill('100')
        page.get_by_label('推广状态',exact=True).select_option('false')
        page.get_by_role('button',name='保存观测快照',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='已保存观测快照')).to_be_visible()
        # Add two fixture peers through the real local API; no platform/model calls.
        current=page.request.get(remix_app+'/api/remix/jobs').json()['items'][0]['id']
        original=page.request.get(remix_app+f'/api/remix/jobs/{current}/publications').json()[0]
        observed_iso=datetime.fromisoformat(observed_local).astimezone().isoformat()
        for i,score in enumerate([5,10]):
            response=page.request.post(remix_app+f'/api/remix/jobs/{current}/publications',data=dict(version_id=original['version_id'],url=f'https://www.xiaohongshu.com/explore/fixture-peer-{i}',published_at=original['published_at'],topic='旅行'))
            assert response.ok,response.text()
            peer=response.json()
            response=page.request.post(remix_app+f'/api/remix/publications/{peer["id"]}/observations',data=dict(observed_at=observed_iso,favorites=score,views=100,promoted=False,request_key=f'browser-observe-peer-{i}'))
            assert response.ok,response.text()
        page.get_by_role('button',name='刷新复盘',exact=True).click()
        expect(page.get_by_text('同类对照 2 篇',exact=False)).to_be_visible()
        page.get_by_role('button',name='整理为候选经验',exact=True).click()
        expect(page.get_by_label('复盘候选经验',exact=True)).to_contain_text('观察差异')
        page.get_by_role('button',name='核对样本后确认入库',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='复盘经验已由你确认入库')).to_be_visible()
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
        page.get_by_label('成稿标题',exact=True).fill('视频字幕校对稿')
        page.get_by_role('button',name='保存成稿版本',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='已保存新版本')).to_be_visible()
        panel=page.get_by_role('region',name='封面卡片字幕制作',exact=True)
        panel.get_by_label('制作来源成稿',exact=True).select_option(index=1)
        panel.get_by_role('button',name='从成稿生成制作草案',exact=False).click()
        panel.get_by_text('字幕校对与时间轴（0 段）',exact=True).click()
        panel.get_by_label('字幕对应素材',exact=True).select_option('original_video')
        panel.get_by_role('button',name='加载原视频完整字幕',exact=True).click()
        expect(panel.get_by_label('第1段字幕',exact=True)).to_have_value('浏览器测试转写片段')
        panel.get_by_role('button',name='保存制作版本并生成预览',exact=True).click()
        expect(panel.get_by_role('heading',name='原视频字幕预览（仅原素材）',exact=True)).to_be_visible()
        track=panel.locator('video track')
        expect(track).to_have_attribute('src',__import__('re').compile(r'/assets/original-video\.vtt$'))
        track_url=track.get_attribute('src')
        assert '浏览器测试转写片段' in page.request.get(remix_app+track_url).text()
        with page.expect_download() as subtitle_download:
            panel.get_by_role('button',name='下载 SRT 字幕',exact=True).click()
        assert '00:00:00,000 --> 00:00:00,800' in Path(subtitle_download.value.path()).read_text()
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


def test_remix_production_browser(remix_app,tmp_path):
    """Full template/subtitle/package flow with isolated data and mocked model only."""
    import json
    import zipfile
    from PIL import Image
    evidence=Path('/tmp/mediacrawler-remix-production-proof');evidence.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('MEDIACRAWLER_TEST_BROWSER') or None,headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1100})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        response=page.request.post(remix_app+'/api/remix/jobs',data=dict(source='xhs',content_id='fixture-note',consent=True,request_key='production-browser-job-1234'))
        assert response.ok,response.text()
        job_id=response.json()['id']
        for _ in range(100):
            item=page.request.get(remix_app+'/api/remix/jobs/'+job_id).json()
            if item['status']=='completed':break
            assert item['status']!='failed',item.get('error')
            time.sleep(.1)
        else:pytest.fail('fixture analysis did not finish')
        response=page.request.post(remix_app+f'/api/remix/jobs/{job_id}/versions',data=dict(rewrite_index=0,title='双月湾旅居：先核对这三件事',content='先问清费用包含哪些项目。\n再确认租期、付款方式和退租条件。\n最后实地查看潮湿、隔音与生活配套。',request_key='production-browser-version'))
        assert response.ok,response.text()
        version=response.json()
        page.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true');localStorage.setItem('mediacrawler_env_checked','true')")
        page.goto(remix_app)
        page.get_by_role('button',name='AI 内容分析',exact=True).click()
        page.locator('.remix-job').first.click()
        panel=page.get_by_role('region',name='封面卡片字幕制作',exact=True)
        panel.get_by_label('制作来源成稿',exact=True).select_option(version['id'])
        panel.get_by_role('button',name='从成稿生成制作草案',exact=False).click()
        expect(panel.get_by_label('封面标题',exact=True)).to_have_value(version['title'])
        panel.get_by_label('封面标题',exact=True).fill('双月湾旅居，入住前先核对')
        panel.get_by_label('封面正文',exact=True).fill('费用范围 · 租期条件 · 实地体验')
        panel.get_by_label('封面栏目',exact=True).fill('旅居观察')
        panel.get_by_label('排版风格',exact=True).select_option('ocean')
        panel.get_by_text('内容卡片（1 / 8）',exact=True).click()
        panel.get_by_label('卡片1标题',exact=True).fill('问清这三项，再做决定')
        panel.get_by_role('button',name='新增内容卡片',exact=True).click()
        panel.get_by_label('卡片2标题',exact=True).fill('把疑问变成看房清单')
        panel.get_by_label('卡片2正文',exact=True).fill('价格口径、付款方式和实地体验，逐项记录。')
        panel.get_by_role('button',name='上移卡片',exact=True).last.click()
        expect(panel.get_by_label('卡片1标题',exact=True)).to_have_value('把疑问变成看房清单')
        panel.get_by_text('字幕校对与时间轴（0 段）',exact=True).click()
        panel.get_by_label('字幕对应素材',exact=True).select_option('manual_timeline')
        panel.get_by_label('字幕对应视频时长（秒）',exact=True).fill('5')
        panel.get_by_role('button',name='新增字幕段',exact=True).click()
        panel.get_by_label('第1段字幕',exact=True).fill('先问清费用，再确认条件。')
        panel.get_by_label('第1段结束秒',exact=True).fill('3.25')
        panel.get_by_role('button',name='保存制作版本并生成预览',exact=True).click()
        expect(panel.get_by_role('status').filter(has_text='制作版本已保存')).to_be_visible()
        expect(panel.get_by_role('img',name='保存的封面预览',exact=True)).to_be_visible()
        expect(panel.get_by_role('button',name='下载人工发布包 ZIP',exact=True)).to_be_disabled()
        for name in ['已核对标题、正文、封面及卡片内容','已核对待核实信息和事实表述','已确认发布内容及素材的使用权利','已在对应视频中校对字幕文字和真实时间轴']:
            panel.get_by_role('checkbox',name=name,exact=True).check()
        # Changing any artwork clears manual confirmation.
        panel.get_by_label('封面页脚',exact=True).fill('记录真实信息')
        expect(panel.get_by_role('checkbox',name='已核对标题、正文、封面及卡片内容',exact=True)).not_to_be_checked()
        for name in ['已核对标题、正文、封面及卡片内容','已核对待核实信息和事实表述','已确认发布内容及素材的使用权利','已在对应视频中校对字幕文字和真实时间轴']:
            panel.get_by_role('checkbox',name=name,exact=True).check()
        panel.get_by_role('button',name='保存制作版本并生成预览',exact=True).click()
        expect(panel.get_by_role('button',name='下载人工发布包 ZIP',exact=True)).to_be_enabled()
        with page.expect_download() as downloaded:
            panel.get_by_role('button',name='下载人工发布包 ZIP',exact=True).click()
        with zipfile.ZipFile(downloaded.value.path()) as archive:
            assert archive.read('body.txt').decode()==version['content']
            assert '00:00:03,250' in archive.read('manual-video.srt').decode()
            manifest=json.loads(archive.read('manifest.json'))
            assert not manifest['published'] and manifest['version_id']==version['id']
            for name in ['cover.png','card-01.png','card-02.png']:
                (evidence/name).write_bytes(archive.read(name))
                assert Image.open(evidence/name).size==(1080,1440)
        # Reload only reads durable revisions; no extra model request.
        revision=panel.get_by_label('历史制作版本',exact=True).input_value()
        page.reload();page.get_by_role('button',name='AI 内容分析',exact=True).click();page.locator('.remix-job').first.click()
        panel=page.get_by_role('region',name='封面卡片字幕制作',exact=True)
        panel.get_by_label('历史制作版本',exact=True).select_option(revision)
        panel.get_by_role('button',name='载入所选制作版本进行编辑',exact=True).click()
        expect(panel.get_by_label('封面标题',exact=True)).to_have_value('双月湾旅居，入住前先核对')
        expect(panel.get_by_role('button',name='下载人工发布包 ZIP',exact=True)).to_be_enabled()
        for width,height,name in [(1440,1100,'desktop'),(390,844,'mobile')]:
            page.set_viewport_size({'width':width,'height':height})
            panel.scroll_into_view_if_needed()
            panel.screenshot(path=str(evidence/(name+'.png')))
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert not errors,errors
        browser.close()
