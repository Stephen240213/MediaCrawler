"""Only the new MCP form is exercised; all MCP collection requests intercepted."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from tests.test_remix_browser import remix_app
from api.services.redfox import TOOLS


def test_mcp_platform_tools_parameters_and_single_submission(remix_app):
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('MEDIACRAWLER_TEST_BROWSER') or None,headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1050})
        errors=[];submissions=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true');localStorage.setItem('mediacrawler_env_checked','true')")
        page.route('**/api/crawler/redfox/tools',lambda route:route.fulfill(json={'tools':TOOLS,'configured':{'xhs':True,'dy':True,'twitter':True,'youtube':True}}))
        def intercept(route):
            submissions.append(route.request.post_data_json)
            route.fulfill(json={'status':'ok','task_id':'a'*32})
        page.route('**/api/crawler/start',intercept)
        page.goto(remix_app)
        expect(page.get_by_role('heading',name='发现内容，沉淀灵感',exact=True)).to_be_visible()
        page.get_by_label('采集方式',exact=True).select_option('redfox_mcp')
        expect(page.get_by_role('heading',name='RedFox MCP 采集')).to_be_visible()
        expect(page.get_by_text('已找到本机持久化 RedFox 凭据。',exact=True)).to_be_visible()
        tool=page.get_by_label('MCP 工具',exact=True)
        assert tool.locator('option').count()==15
        page.get_by_label('目标平台',exact=True).select_option('youtube')
        assert tool.locator('option').count()==4
        tool.select_option('youtube_get_comments')
        page.get_by_label('视频 ID *',exact=True).fill('fixture-video')
        page.get_by_label('下一页令牌（可选）',exact=True).fill('fixture-cursor')
        with page.expect_response('**/api/crawler/start'):
            page.get_by_role('button',name='执行 MCP 采集',exact=True).click()
        assert len(submissions)==1
        payload=submissions[0]
        assert payload['collection_method']=='redfox_mcp'
        assert payload['platform']=='youtube' and payload['mcp_tool']=='youtube_get_comments'
        assert payload['mcp_arguments']=={'video_id':'fixture-video','continuation_token':'fixture-cursor'}
        assert payload['save_option']=='json' and not payload.get('xhs_filters') and not payload['cookies']
        page.get_by_label('目标平台',exact=True).select_option('xhs')
        tool.select_option('xiaohongshu_get_comments_result')
        page.get_by_label('远程任务 ID *',exact=True).fill('fixture-task')
        proof=Path('/tmp/mediacrawler-redfox-proof');proof.mkdir(exist_ok=True)
        page.screenshot(path=str(proof/'desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(proof/'mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        assert not errors,errors
        browser.close()
