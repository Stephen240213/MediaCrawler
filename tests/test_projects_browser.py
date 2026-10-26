"""Project hub and editor recovery use only fixture processes and browser storage."""
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from tests.test_remix_browser import remix_app


def test_projects_links_recovery_and_cross_page_editing(remix_app):
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('MEDIACRAWLER_TEST_BROWSER') or None,headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1100});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true');localStorage.setItem('mediacrawler_env_checked','true')")
        page.goto(remix_app);page.get_by_role('button',name='内容项目',exact=True).click()
        page.get_by_label('项目名称',exact=True).fill('第一轮贯通项目')
        page.get_by_label('项目题材',exact=True).fill('旅行')
        page.get_by_role('button',name='保存内容项目',exact=True).click()
        expect(page.get_by_role('heading',name='第一轮贯通项目',exact=True)).to_be_visible()
        page.get_by_role('button',name='新建内容项目',exact=True).click()
        expect(page.get_by_label('项目名称',exact=True)).to_have_value('')
        expect(page.get_by_role('region',name='项目详情').get_by_role('button',name='恢复未完成编辑',exact=True)).to_have_count(0)
        page.get_by_role('button',name='第一轮贯通项目',exact=False).click()
        # Unsaved notes must survive unmount and reload, without changing the official project.
        page.get_by_label('项目工作笔记',exact=True).fill('尚未提交的资料整理：核对费用')
        expect(page.get_by_role('status').filter(has_text='未完成编辑已保存').last).to_be_visible(timeout=10000)
        project=page.request.get(remix_app+'/api/remix/projects').json()[0]
        assert project['notes']==''
        page.get_by_role('button',name='AI 内容分析',exact=True).click()
        page.get_by_role('button',name='内容项目',exact=True).click()
        page.get_by_role('button',name='第一轮贯通项目',exact=False).click()
        page.get_by_role('region',name='项目详情').get_by_role('button',name='恢复未完成编辑',exact=True).click()
        expect(page.get_by_label('项目工作笔记',exact=True)).to_have_value('尚未提交的资料整理：核对费用')
        page.get_by_role('button',name='保存内容项目',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='项目已保存')).to_be_visible()
        # Create an isolated analysis through the mocked model then link it from the hub.
        response=page.request.post(remix_app+'/api/remix/jobs',data=dict(source='xhs',content_id='fixture-note',consent=True,request_key='project-browser-analysis'))
        assert response.ok,response.text();job_id=response.json()['id']
        for _ in range(100):
            job=page.request.get(remix_app+'/api/remix/jobs/'+job_id).json()
            if job['status']=='completed':break
            time.sleep(.1)
        page.reload();page.get_by_role('button',name='内容项目',exact=True).click();page.get_by_role('button',name='第一轮贯通项目',exact=False).click()
        page.get_by_label('选择关联记录',exact=True).select_option(job_id)
        page.get_by_role('button',name='关联到项目',exact=True).click()
        expect(page.get_by_role('button',name='打开分析与制作',exact=True)).to_be_visible()
        page.get_by_role('button',name='打开分析与制作',exact=True).click()
        expect(page.get_by_role('heading',name='完整改写稿',exact=True)).to_be_visible()
        expect(page.get_by_label('当前内容项目',exact=True)).to_have_value(project['id'])
        page.get_by_label('成稿标题',exact=True).fill('恢复中的成稿标题')
        page.get_by_label('改写正文',exact=True).fill('这段编辑尚未保存为正式成稿。')
        page.get_by_role('button',name='内容项目',exact=True).click()
        page.get_by_role('button',name='第一轮贯通项目',exact=False).click();page.get_by_role('button',name='打开分析与制作',exact=True).click()
        page.get_by_role('button',name='恢复未完成编辑',exact=True).click()
        expect(page.get_by_label('成稿标题',exact=True)).to_have_value('恢复中的成稿标题')
        page.get_by_role('button',name='保存成稿版本',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='已保存新版本')).to_be_visible()
        page.get_by_role('button',name='内容项目',exact=True).click();page.get_by_role('button',name='第一轮贯通项目',exact=False).click()
        expect(page.get_by_text('成稿：恢复中的成稿标题',exact=False)).to_be_visible()
        proof=Path('/tmp/mediacrawler-projects-proof');proof.mkdir(exist_ok=True)
        page.screenshot(path=str(proof/'desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(proof/'mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        assert not errors,errors
        browser.close()
