"""Actual UI submission, local method editing, reuse and responsive rendering."""
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright,expect
from tests.test_remix_browser import remix_app


def test_independent_analysis_writing_registry_and_reuse_in_browser(remix_app):
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('MEDIACRAWLER_TEST_BROWSER') or None,headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true');localStorage.setItem('mediacrawler_env_checked','true')")
        page.goto(remix_app);page.get_by_role('button',name='AI 内容分析',exact=True).click()
        project=page.request.post(remix_app+'/api/remix/projects',data=dict(title='独立写作贯通项目')).json()
        page.get_by_role('button',name='刷新配置与素材',exact=True).click()
        page.reload();page.get_by_role('button',name='AI 内容分析',exact=True).click()
        page.get_by_label('当前内容项目',exact=True).select_option(project['id'])
        page.get_by_label('选择内容',exact=True).select_option('fixture-note')
        page.get_by_role('checkbox',name='我同意',exact=False).check()
        with page.expect_request(lambda r:r.url.endswith('/api/remix/jobs') and r.method=='POST') as sent:
            page.get_by_role('button',name='开始分析',exact=True).click()
        assert sent.value.post_data_json['task_kind']=='analysis'
        expect(page.get_by_role('heading',name='内容摘要',exact=True)).to_be_visible(timeout=15000)
        expect(page.get_by_role('heading',name='完整改写稿',exact=True)).to_have_count(0)
        expect(page.get_by_text('写作由独立任务执行',exact=True)).to_be_visible()
        parent=page.request.get(remix_app+'/api/remix/jobs?task_kind=analysis').json()['items'][0]
        original=page.request.get(remix_app+'/api/remix/jobs/'+parent['id']).json()
        page.get_by_role('button',name='用此报告创作',exact=True).click()
        expect(page.get_by_label('选择分析报告',exact=True)).to_have_value(parent['id'])
        expect(page.get_by_label('写作关联内容项目',exact=True)).to_have_value(project['id'])
        page.get_by_text('管理 Agent 与 Skill',exact=True).click()
        manager=page.locator('.creation-registry')
        manager.get_by_label('配置编号',exact=True).fill('browser_editor')
        manager.get_by_label('配置名称',exact=True).fill('测试来源编辑')
        manager.get_by_label('执行方法',exact=True).fill('先整理来源范围，再写已确认条件。')
        manager.get_by_role('button',name='保存新配置',exact=True).click()
        expect(manager.get_by_role('status')).to_contain_text('测试来源编辑 · v1')
        manager.get_by_label('执行方法',exact=True).fill('先整理来源范围，再写已确认条件，最后列出核实项。')
        manager.get_by_role('button',name='保存配置新版本',exact=True).click()
        expect(manager.get_by_role('status')).to_contain_text('测试来源编辑 · v2')
        manager.get_by_label('配置类型',exact=True).select_option('skill')
        manager.get_by_label('配置编号',exact=True).fill('browser_scope')
        manager.get_by_label('配置名称',exact=True).fill('范围说明')
        manager.get_by_label('执行方法',exact=True).fill('统计只写本次范围，不替代原文事实。')
        manager.get_by_role('checkbox',name='支持视频',exact=True).uncheck()
        manager.get_by_role('button',name='保存新配置',exact=True).click()
        expect(manager.get_by_role('status')).to_contain_text('范围说明 · v1')
        page.get_by_text('管理 Agent 与 Skill',exact=True).click()
        page.get_by_label('创作 Agent',exact=True).select_option('browser_editor')
        page.get_by_role('checkbox',name='范围说明 · v1',exact=True).check()
        page.get_by_text('创作需求与改写框架',exact=True).click()
        page.get_by_label('表达语气',exact=True).fill('直接')
        page.get_by_role('checkbox',name='我同意将所选来源',exact=False).check()
        with page.expect_request(lambda r:r.url.endswith('/api/remix/jobs') and r.method=='POST') as sent, page.expect_response(lambda r:r.url.endswith('/api/remix/jobs') and r.request.method=='POST') as saved:
            page.get_by_role('button',name='调用 Agent 生成草稿',exact=True).click()
        assert sent.value.post_data_json['task_kind']=='writing'
        expect(page.get_by_label('打开写作任务',exact=True)).to_have_value(saved.value.json()['id'])
        expect(page.get_by_role('button',name='选用并编辑此稿',exact=True)).to_be_visible(timeout=15000)
        first=page.request.get(remix_app+'/api/remix/jobs?task_kind=writing').json()['items'][0]
        first=page.request.get(remix_app+'/api/remix/jobs/'+first['id']).json()
        assert first['parent_job_id']==parent['id']
        assert first['input']['project_id']==project['id']
        assert first['source']['execution']['agent']['version']==2
        assert first['report']['sections']==[] and first['report']['comment_analysis']==[]
        page.get_by_role('button',name='复用相同来源再写一版',exact=True).click()
        expect(page.get_by_label('创作来源',exact=True)).to_be_disabled()
        page.get_by_label('表达语气',exact=True).fill('温和')
        page.get_by_role('checkbox',name='我同意将所选来源',exact=False).check()
        with page.expect_request(lambda r:r.url.endswith('/api/remix/jobs') and r.method=='POST') as sent, page.expect_response(lambda r:r.url.endswith('/api/remix/jobs') and r.request.method=='POST') as saved:
            page.get_by_role('button',name='调用 Agent 生成草稿',exact=True).click()
        assert sent.value.post_data_json['reuse_job_id']==first['id']
        expect(page.get_by_label('打开写作任务',exact=True)).to_have_value(saved.value.json()['id'])
        expect(page.get_by_role('button',name='选用并编辑此稿',exact=True)).to_be_visible(timeout=15000)
        second=page.request.get(remix_app+'/api/remix/jobs?task_kind=writing').json()['items'][0]
        assert second['id']!=first['id'] and second['parent_result_sha256']==first['parent_result_sha256']
        assert page.request.get(remix_app+'/api/remix/jobs/'+parent['id']).json()==original
        page.get_by_role('button',name='打开完整报告',exact=True).click()
        expect(page.get_by_text('复用已有分析结果',exact=True)).to_be_visible()
        page.get_by_role('button',name='打开来源任务',exact=True).click()
        expect(page.get_by_role('heading',name='基于此结果的写作任务',exact=True)).to_be_visible()
        expect(page.get_by_text('共 2 个写作任务',exact=True)).to_be_visible()
        proof=Path('/tmp/mediacrawler-independent-writing-proof');proof.mkdir(exist_ok=True)
        page.screenshot(path=str(proof/'linked-tasks.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        page.screenshot(path=str(proof/'mobile.png'))
        assert not errors,errors
        browser.close()
