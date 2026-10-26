"""Complete source browsing with a fixture model and disposable originals."""
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from playwright.sync_api import sync_playwright,expect
from tests.test_remix_browser import remix_app


def test_complete_analysis_originals_paging_and_download(remix_app):
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('MEDIACRAWLER_TEST_BROWSER') or None,headless=True)
        page=browser.new_page(viewport={'width':825,'height':740})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        comments=[dict(platform='xhs',content_id='fixture-note',comment_id=f'added-{i:03d}',body=f'第{i}条完整评论，保留到末尾。') for i in range(45)]
        result=page.request.post(remix_app+'/api/analytics/import?filename=comments.json&kind=comment&platform=xhs&dry_run=false',data=json.dumps(comments),headers={'Content-Type':'application/json'})
        assert result.ok,result.text()
        created=page.request.post(remix_app+'/api/remix/jobs',data=dict(source='analytics',analytics_filters={'platform':'xhs'},include_comments=False,consent=True,request_key=uuid4().hex)).json()
        for _ in range(100):
            job=page.request.get(remix_app+'/api/remix/jobs/'+created['id']).json()
            if job['status'] in ('completed','failed'):break
            time.sleep(.05)
        assert job['status']=='completed',job
        page.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true');localStorage.setItem('mediacrawler_env_checked','true')")
        page.goto(remix_app)
        page.get_by_role('button',name='AI 内容分析',exact=True).click()
        page.get_by_role('button',name='分析结果',exact=True).click()
        page.locator('.remix-job').first.click()
        panel=page.get_by_role('region',name='完整任务来源',exact=True)
        panel.get_by_role('button',name='查看完整来源',exact=True).click()
        expect(panel.get_by_text('完整冻结：内容 1 条 · 原始评论 46 条',exact=True)).to_be_visible()
        panel.get_by_label('来源类型',exact=True).select_option('content')
        panel.get_by_text('测试旅行笔记 · D0001',exact=True).click()
        expect(panel.locator('pre')).to_contain_text('提出路线问题，然后展示安排方法。')
        panel.get_by_label('来源类型',exact=True).select_option('comment')
        panel.get_by_role('button',name='下一页来源',exact=True).click()
        panel.get_by_role('button',name='下一页来源',exact=True).click()
        panel.get_by_text('评论 added-044 · C0045',exact=True).click()
        expect(panel.locator('pre').filter(has_text='第44条完整评论，保留到末尾。')).to_be_visible()
        with page.expect_download() as download:panel.get_by_role('button',name='下载完整来源包',exact=True).click()
        archive=json.loads(Path(download.value.path()).read_text())
        assert archive['source']['dataset_snapshot']['counts']==dict(contents=1,comments=46)
        assert len(archive['source']['dataset_snapshot']['comments'])==46
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        assert not errors,errors
        browser.close()


@pytest.mark.parametrize('remix_app',['rejected-statistics'],indirect=True)
def test_failed_report_keeps_draft_and_revalidates_saved_response_in_browser(remix_app):
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('MEDIACRAWLER_TEST_BROWSER') or None,headless=True)
        page=browser.new_page(viewport={'width':825,'height':740})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        created=page.request.post(remix_app+'/api/remix/jobs',data=dict(source='analytics',analytics_filters={'platform':'xhs'},consent=True,request_key=uuid4().hex)).json()
        base=remix_app+'/api/remix/jobs/'+created['id']
        for _ in range(100):
            job=page.request.get(base).json()
            if job['status'] in ('completed','failed'):break
            time.sleep(.05)
        assert job['status']=='failed'
        page.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true');localStorage.setItem('mediacrawler_env_checked','true')")
        page.goto(remix_app)
        page.get_by_role('button',name='AI 内容分析',exact=True).click()
        page.get_by_role('button',name='分析结果',exact=True).click()
        page.locator('.remix-job').first.click()
        detail=page.get_by_role('region',name='分析任务详情',exact=True)
        expect(detail.get_by_text('已生成 · 校验待处理',exact=False)).to_be_visible()
        detail.get_by_role('button',name='查看已生成稿件（待核对）',exact=True).click()
        detail.get_by_text('方案 1 · 旅行安排方法1',exact=True).click()
        expect(detail.get_by_text('先明确旅行需求，再核对路线条件。根据真实资料安排计划，尚未确认的细节留待核实。',exact=True)).to_be_visible()
        detail.get_by_role('button',name='重新校验已保存响应（不调用模型）',exact=True).click()
        expect(detail.get_by_role('status').filter(has_text='未再次调用模型')).to_be_visible()
        expect(detail.get_by_text('结构与引用校验通过',exact=True)).to_be_visible()
        restored=page.request.get(base).json()
        assert restored['status']=='completed' and restored['recovered_response']
        assert restored['report']['comment_analysis'][0]['basis']=='comment_statistics'
        assert restored['model_generated_at'] and restored['artifacts']['creation']['ready_count']==1
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        assert not errors,errors
        browser.close()
