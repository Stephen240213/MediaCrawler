"""Draft layout and edits use disposable local data, never existing user drafts."""
import os
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright, expect
from tests.test_remix_browser import remix_app


def test_editor_canvas_picker_preview_save_and_recovery(remix_app):
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('MEDIACRAWLER_TEST_BROWSER') or None,headless=True)
        page=browser.new_page(viewport={'width':825,'height':740})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true');localStorage.setItem('mediacrawler_env_checked','true')")
        first,second=uuid4().hex,uuid4().hex
        for identifier,title in [(first,'旅行生活配套核对：停车、买菜与外卖如何确认'),(second,'另一份本机草稿')]:
            response=page.request.put(remix_app+'/api/workbench/drafts/'+identifier,data=dict(id=identifier,title=title,content='按实际来源核对生活配套。',platform='xhs',tags=['旅行'],images=[],version=0))
            assert response.ok,response.text()
        page.goto(remix_app)
        page.get_by_role('button',name='创作工作台',exact=True).click()
        picker=page.get_by_label('选择本机草稿',exact=True)
        picker.select_option(first)
        expect(page.get_by_label('标题',exact=True)).to_have_value('旅行生活配套核对：停车、买菜与外卖如何确认')
        preview=page.get_by_role('complementary',name='草稿实时预览',exact=True)
        expect(preview).to_be_hidden()
        editor=page.locator('.wb-draft-editor')
        bounds=editor.bounding_box()
        assert bounds['width']>650 # At 825px the editor is the main canvas, not a narrow column.
        padding=page.locator('.wb-editor-fields').evaluate('(e)=>parseFloat(getComputedStyle(e).paddingLeft)')
        assert padding>=18
        recovery=page.get_by_label('编辑恢复',exact=True)
        expect(recovery).to_contain_text('编辑恢复保护已开启')
        assert recovery.bounding_box()['height']<60
        page.get_by_label('标题',exact=True).fill('平板上整理后的草稿标题')
        page.get_by_label('正文',exact=True).fill('先核对停车，再确认买菜和外卖覆盖。')
        page.get_by_role('button',name='查看预览',exact=True).click()
        expect(preview).to_be_visible()
        expect(preview.get_by_role('heading',name='平板上整理后的草稿标题',exact=True)).to_be_visible()
        assert preview.bounding_box()['y']>=editor.bounding_box()['y']+editor.bounding_box()['height']
        page.get_by_role('button',name='收起预览',exact=True).click()
        page.get_by_role('button',name='保存草稿',exact=True).click()
        expect(page.get_by_text('已保存 · 版本 2',exact=True)).to_be_visible()
        saved=page.request.get(remix_app+'/api/workbench/drafts').json()['drafts']
        assert next(d for d in saved if d['id']==first)['title']=='平板上整理后的草稿标题'
        page.get_by_label('正文',exact=True).fill('尚未正式保存的后续编辑。')
        expect(recovery).to_contain_text('编辑内容已备份',timeout=10000)
        page.get_by_role('button',name='素材管理',exact=True).click()
        page.get_by_role('button',name='创作工作台',exact=True).click()
        expect(page.get_by_label('正文',exact=True)).to_have_value('尚未正式保存的后续编辑。')
        page.get_by_role('button',name='保存草稿',exact=True).click()
        expect(page.get_by_text('已保存 · 版本 3',exact=True)).to_be_visible()
        picker.select_option(second)
        expect(page.get_by_label('标题',exact=True)).to_have_value('另一份本机草稿')
        picker.select_option(first)
        expect(page.get_by_label('正文',exact=True)).to_have_value('尚未正式保存的后续编辑。')
        proof=Path('/tmp/mediacrawler-draft-layout-proof');proof.mkdir(exist_ok=True)
        for width,height,name in [(825,740,'tablet'),(1440,1000,'desktop'),(390,844,'mobile')]:
            page.set_viewport_size({'width':width,'height':height})
            page.get_by_role('heading',name='创作工作台',exact=True).scroll_into_view_if_needed()
            page.screenshot(path=str(proof/(name+'.png')))
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        assert not errors,errors
        browser.close()
