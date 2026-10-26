"""Cross-device Chromium tests over a real local protected API.

A test-only reverse-proxy shim substitutes a locally signed JWT for Access.
Production has no test identity switch and no signature bypass.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

import pytest
from playwright.sync_api import sync_playwright, expect
from tests.test_remote_access import signing  # shared generated RSA fixture, never a real credential


@pytest.fixture
def remote_app(tmp_path,signing):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    config=tmp_path/'public-test-key.json';config.write_text(json.dumps(signing[1]))
    code='''
import sys,json,time
from pathlib import Path
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from api.services.access import AccessSettings,AccessVerifier,Boundary,decode
from api.main import app
from api.services import workbench_store,crawler_manager,analytics
from api.routers import workbench,data
root=Path(sys.argv[1]);port=int(sys.argv[2]);jwk=json.loads((root/'public-test-key.json').read_text())
workbench_store.DB_PATH=root/'workbench.sqlite3'
analytics.sqlite_db_config['db_path']=str(root/'collection.db')
workbench.DATA_ROOT=root/'media';data.DATA_DIR=root/'media'
settings=AccessSettings('https://test-team.cloudflareaccess.com','audience','https://crawler.test',frozenset({'operator@test.invalid'}),frozenset({'viewer@test.invalid'}))
verify=AccessVerifier(settings)
verify.keys={jwk['kid']:rsa.RSAPublicNumbers(int.from_bytes(decode(jwk['e']),'big'),int.from_bytes(decode(jwk['n']),'big')).public_key()};verify.refreshed=time.monotonic()
# Only this test harness replaces collection with a harmless local process.
script="import time;print('fixture task started',flush=True);time.sleep(2);print('fixture task completed',flush=True)"
crawler_manager._build_command=lambda _:[sys.executable,'-u','-c',script]
uvicorn.run(Boundary(app,settings,verifier=verify),host='127.0.0.1',port=port,log_level='warning',access_log=False)
'''
    with (tmp_path/'remote.log').open('w') as log:
        process=subprocess.Popen([sys.executable,'-c',code,str(tmp_path),str(port)],stdout=log,stderr=log)
        try:
            for _ in range(100):
                if process.poll() is not None:pytest.fail((tmp_path/'remote.log').read_text())
                try:
                    urllib.request.urlopen(f'http://127.0.0.1:{port}/',timeout=1)
                except urllib.error.HTTPError:
                    break  # A protected listener is ready even when anonymous access fails.
                except OSError:time.sleep(.1)
            else:pytest.fail('protected API not ready')
            yield f'http://127.0.0.1:{port}'
        finally:
            process.terminate();process.wait(timeout=10)


def browser_context(browser,remote_app,token,viewport):
    context=browser.new_context(viewport=viewport)
    def proxy(route):
        request=route.request
        suffix=request.url.removeprefix('https://crawler.test')
        headers={**request.headers,'host':'crawler.test','cf-access-jwt-assertion':token}
        response=route.fetch(url=remote_app+suffix,headers=headers)
        route.fulfill(response=response)
    context.route('https://crawler.test/**',proxy)
    # Suppress WebSocket log delivery to verify the durable HTTP fallback.
    context.route_web_socket('wss://crawler.test/**',lambda ws:None)
    context.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true');localStorage.setItem('mediacrawler_env_checked','true')")
    return context


def test_operator_close_page_and_polling_recovery(remote_app,signing):
    evidence=Path('/tmp/mediacrawler-remote-proof');evidence.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('MEDIACRAWLER_TEST_BROWSER') or None,headless=True)
        context=browser_context(browser,remote_app,signing[3](),{'width':1440,'height':1000})
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('https://crawler.test/')
        expect(page.get_by_label('访问与执行位置')).to_contain_text('远程访问 · 采集电脑执行')
        expect(page.get_by_label('访问与执行位置')).to_contain_text('操作员')
        page.get_by_label('采集方式',exact=True).select_option('browser')
        page.get_by_role('textbox',name='搜索关键词').fill('local-fixture-only')
        with page.expect_response('**/api/crawler/start') as submitted:
            page.get_by_role('button',name='执行关键词搜索',exact=True).click()
        assert submitted.value.status==200
        expect(page.get_by_role('button',name='停止任务',exact=True)).to_be_visible()
        context.set_offline(True)
        page.close()  # The user disconnects immediately after submitting.
        context.set_offline(False)
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto('https://crawler.test/')
        page.get_by_role('button',name='任务记录',exact=True).click()
        expect(page.locator('.wb-table tbody')).to_contain_text('已完成',timeout=15000)
        page.get_by_role('button',name='采集工作台',exact=True).click()
        expect(page.get_by_label('执行日志')).to_contain_text('fixture task completed',timeout=12000)
        page.screenshot(path=str(evidence/'operator-desktop.png'),full_page=True)
        page.get_by_role('button',name='数据分析',exact=True).click()
        expect(page.get_by_text('尚未初始化分析结构',exact=True)).to_be_visible()
        page.get_by_role('button',name='备份并初始化').click()
        expect(page.get_by_text('当前筛选没有内容。',exact=False)).to_be_visible()
        for width,height,label in [(820,1180,'tablet'),(390,844,'phone')]:
            page.set_viewport_size({'width':width,'height':height})
            page.evaluate('window.scrollTo(0,0)')
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            page.screenshot(path=str(evidence/(label+'.png')),full_page=True)
        assert not errors,errors
        context.close();browser.close()


def test_viewer_permissions_expiry_and_file_access(remote_app,signing):
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('MEDIACRAWLER_TEST_BROWSER') or None,headless=True)
        context=browser_context(browser,remote_app,signing[3](sub='viewer-sub',email='viewer@test.invalid'),{'width':390,'height':844})
        page=context.new_page();page.goto('https://crawler.test/')
        expect(page.get_by_label('访问与执行位置')).to_contain_text('只读账号')
        expect(page.get_by_role('button',name='执行 MCP 采集',exact=True)).to_be_disabled()
        page.get_by_role('button',name='素材管理',exact=True).click()
        expect(page.get_by_role('button',name='导入图片',exact=True)).to_be_disabled()
        page.get_by_role('button',name='创作工作台',exact=True).click()
        expect(page.get_by_role('button',name='保存草稿',exact=True)).to_be_disabled()
        page.get_by_role('button',name='数据分析',exact=True).click()
        expect(page.get_by_role('button',name='备份并初始化')).to_be_disabled()
        # Expiry hides the workspace and offers reauthentication; no tokens in UI.
        page.evaluate("window.dispatchEvent(new Event('mc-session-expired'))")
        expect(page.get_by_role('heading',name='访问会话已失效或服务暂不可用')).to_be_visible()
        expect(page.get_by_role('button',name='刷新并重新连接')).to_be_visible()
        context.close();browser.close()
