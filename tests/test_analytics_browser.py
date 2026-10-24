"""Actual Chromium + local API verification, with a disposable collection DB.

No requests are sent to collection platforms. Fixture data exists only inside
this test's temporary database and is never bundled with the application.
"""
import json
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
def local_app(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    code = '''
import sys
from pathlib import Path
import uvicorn
from api.main import app
from api.services import analytics, workbench_store
from api.routers import workbench, data
root=Path(sys.argv[1])
analytics.sqlite_db_config['db_path']=str(root/'collection.db')
workbench_store.DB_PATH=root/'workbench.sqlite3'
workbench.DATA_ROOT=root/'media'
data.DATA_DIR=root/'media'
uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[2]),log_level='warning')
'''
    with (tmp_path/'server.log').open('w') as log:
        process = subprocess.Popen([sys.executable,'-c',code,str(tmp_path),str(port)],stdout=log,stderr=log)
        base = f'http://127.0.0.1:{port}'
        try:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail((tmp_path/'server.log').read_text())
                try:
                    urllib.request.urlopen(base+'/api/health', timeout=1).close()
                    break
                except OSError:
                    time.sleep(.1)
            else:
                pytest.fail('API did not become ready')
            yield base
        finally:
            process.terminate()
            process.wait(timeout=10)


def test_analytics_browser_flow(local_app, tmp_path):
    evidence = Path('/tmp/mediacrawler-analytics-proof')
    evidence.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('MEDIACRAWLER_TEST_BROWSER') or None, headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1080})
        errors=[]
        page.on('pageerror',lambda error: errors.append(str(error)))
        page.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true');localStorage.setItem('mediacrawler_env_checked','true')")
        page.goto(local_app)
        expect(page.get_by_role('combobox',name='保存格式')).to_have_value('sqlite')
        page.get_by_role('button',name='数据分析',exact=True).click()
        expect(page.get_by_text('尚未初始化分析结构',exact=True)).to_be_visible()
        page.get_by_role('button',name='备份并初始化').click()
        expect(page.get_by_text('当前筛选没有内容。',exact=False)).to_be_visible()
        page.screenshot(path=str(evidence/'empty.png'),full_page=True)
        fixture=[{'platform':'xhs','content_id':'fixture-a','title':'浏览器测试内容','author':'匿名创作者','keyword':'旅行','published_at':'2026-01-01T12:00:00+08:00','collected_at':'2026-02-01T12:00:00+08:00','likes':'1万','url':'https://www.xiaohongshu.com/explore/fixture-a'},
                 {'platform':'xhs','content_id':'fixture-a','title':'浏览器测试内容','author':'匿名创作者','keyword':'旅行','published_at':'2026-01-01T12:00:00+08:00','collected_at':'2026-02-02T12:00:00+08:00','likes':'1.2万','url':'https://www.xiaohongshu.com/explore/fixture-a'},
                 {'platform':'dy','content_id':'fixture-b','title':'缺失指标的测试内容','author':'匿名创作者乙','published_at':'2026-01-02','collected_at':'2026-02-01'}]
        page.get_by_label('数据文件',exact=True).set_input_files({'name':'history.json','mimeType':'application/json','buffer':json.dumps(fixture).encode()})
        page.get_by_role('button',name='校验并预览结果').click()
        expect(page.get_by_text('校验预览：',exact=False)).to_be_visible()
        page.get_by_role('button',name='确认导入有效记录').click()
        expect(page.get_by_text('导入完成，已刷新分析。',exact=True)).to_be_visible()
        expect(page.locator('.an-details tbody tr')).to_have_count(2)
        expect(page.locator('.an-details tbody tr').first).to_contain_text('12,000')
        page.locator('.an-details tbody tr').first.get_by_role('button',name='查看互动历史').click()
        expect(page.locator('.an-history')).to_contain_text('增长 2,000')
        expect(page.locator('.an-history')).to_contain_text('暂无可比较历史')
        # Verify an actual chart control drills into the filtered detail rows.
        page.get_by_role('heading',name='平台分布',exact=True).locator('..').get_by_role('button',name='抖音').click()
        expect(page.locator('.an-details tbody tr')).to_have_count(1)
        expect(page.locator('.an-details tbody tr')).to_contain_text('fixture-b')
        page.get_by_role('button',name='清除筛选与明细定位').click()
        expect(page.locator('.an-details tbody tr')).to_have_count(2)
        with page.expect_download() as download:
            page.get_by_role('button',name='导出 CSV',exact=True).click()
        assert download.value.suggested_filename.endswith('.csv')
        assert 'fixture-a' in Path(download.value.path()).read_text(encoding='utf-8-sig')
        # Import comment records from the actual browser UI.
        comments=[{'platform':'xhs','content_id':'fixture-a','comment_id':'c1','body':'旅行记录很好看','published_at':'2026-01-03'}]
        page.get_by_role('combobox',name='记录类型').select_option('comment')
        page.get_by_label('数据文件',exact=True).set_input_files({'name':'comments.jsonl','mimeType':'application/json','buffer':json.dumps(comments[0]).encode()})
        page.get_by_role('button',name='校验并预览结果').click()
        expect(page.get_by_role('button',name='确认导入有效记录')).to_be_enabled()
        page.get_by_role('button',name='确认导入有效记录').click()
        expect(page.get_by_text('导入结果：',exact=False)).to_be_visible()
        page.get_by_role('button',name='评论',exact=True).click()
        expect(page.locator('.an-details tbody')).to_contain_text('旅行记录很好看')
        expect(page.get_by_role('button',name='校验并预览结果')).to_be_enabled()
        expect(page.locator('.an-cards').first.locator('strong').nth(1)).to_have_text('1')
        page.get_by_role('button',name='内容',exact=True).click()
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(evidence/'desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(evidence/'mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'page must not overflow horizontally'
        # Existing destinations remain accessible.
        for name in ['草稿预览','素材管理','任务记录','采集工作台']:
            page.get_by_role('button',name=name,exact=True).click()
            expect(page.get_by_role('heading',name=name,level=1,exact=True)).to_be_visible()
        # Loading and API errors are explicit, and retry recovers.
        page.route('**/api/analytics/overview*', lambda route: route.fulfill(status=503,content_type='application/json',body='{"detail":"测试：数据库暂时不可读"}'))
        page.get_by_role('button',name='数据分析',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('数据库暂时不可读')
        page.unroute('**/api/analytics/overview*')
        page.get_by_role('button',name='重试',exact=True).click()
        expect(page.get_by_role('heading',name='内容发布趋势',exact=True)).to_be_visible()
        assert not errors, errors
        browser.close()
