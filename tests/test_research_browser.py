"""Three research dimensions use a temporary API and mocked model only."""
import os
import sqlite3
import time
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from tests.test_remix_browser import remix_app


def wait_batch(page,base,space_id,previous_revision=None):
    for _ in range(100):
        detail=page.request.get(base+'/api/remix/research/spaces/'+space_id).json()
        if detail['runs'] and detail['runs'][0]['status'] not in ('queued','running') and (not previous_revision or detail.get('latest',{}).get('id')!=previous_revision):
            assert detail['runs'][0]['status']=='completed',detail['runs'][0]
            return detail
        time.sleep(.1)
    raise AssertionError('research fixture did not finish')


def test_research_topic_incremental_account_reuse_and_project_pin(remix_app,tmp_path):
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('MEDIACRAWLER_TEST_BROWSER') or None,headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1100});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true');localStorage.setItem('mediacrawler_env_checked','true')")
        page.goto(remix_app);page.get_by_role('button',name='AI 内容分析',exact=True).click()
        for name in ('单条内容','单账号','专题与类目资产'):
            expect(page.get_by_role('button',name=name,exact=True)).to_be_visible()
        page.get_by_role('button',name='专题与类目资产',exact=True).click()
        page.get_by_label('研究名称',exact=True).fill('旅居增量专题')
        keyword=page.get_by_label('采集来源关键词',exact=True)
        keyword.press_sequentially('旅行\n旅居',delay=10)
        expect(keyword).to_have_value('旅行\n旅居')
        page.get_by_role('button',name='保存研究范围',exact=True).click()
        expect(page.get_by_label('已保存的研究范围',exact=True)).not_to_have_value('')
        sid=page.get_by_label('已保存的研究范围',exact=True).input_value()
        expect(page.get_by_text('待正文理解 1',exact=True)).to_be_visible()
        submit=page.get_by_role('button',name='仅分析新增与变化，并更新综合',exact=True)
        expect(submit).to_be_disabled()
        page.get_by_role('checkbox',name='确认将本批正文',exact=False).check();submit.click()
        detail=wait_batch(page,remix_app,sid);old_id=detail['latest']['id']
        expect(page.get_by_role('heading',name='可追溯研究版本',exact=True)).to_be_visible(timeout=10000)
        expect(page.get_by_text('正文可复用 1',exact=True)).to_be_visible(timeout=10000)
        # Searching and multi-label categorization use immutable evidence-backed assets.
        page.get_by_label('搜索资产',exact=True).fill('费用')
        card=page.locator('.research-workbench .remix-section').filter(has=page.get_by_label('资产类目标签')).first
        card.get_by_label('资产类目标签',exact=True).fill('旅居 / 费用，决策')
        card.get_by_role('button',name='保存资产类目',exact=True).click()
        expect(card.get_by_role('status')).to_have_text('类目标签已保存')
        card.get_by_role('button',name='查看证据',exact=False).first.click()
        expect(page.get_by_role('complementary',name='研究证据')).to_be_visible()
        # A new local saved comment only invokes comments + affected category synthesis.
        with sqlite3.connect(tmp_path/'collection.db') as db:
            db.execute("INSERT INTO analytics_comment(platform,comment_id,content_id,body) VALUES('xhs','comment-2','fixture-note','费用包含什么？')")
        page.get_by_role('button',name='更新本机纳入范围',exact=True).click()
        expect(page.get_by_text('待处理评论 1',exact=True)).to_be_visible(timeout=10000)
        page.get_by_role('checkbox',name='确认将本批正文',exact=False).check();submit.click()
        latest=wait_batch(page,remix_app,sid,old_id)
        assert latest['latest']['id']!=old_id
        assert not any(c['kind']=='material' for c in latest['runs'][0]['calls'])
        # Select the earlier immutable snapshot and link it to a real fixture project.
        response=page.request.post(remix_app+'/api/remix/projects',data={'title':'研究引用项目'})
        assert response.ok;pid=response.json()['id']
        page.get_by_role('button',name='更新本机纳入范围',exact=True).click()
        page.get_by_label('查看报告版本',exact=True).select_option(old_id)
        page.get_by_label('引用此版本的内容项目',exact=True).select_option(pid)
        page.get_by_role('button',name='将此研究版本关联项目',exact=True).click()
        expect(page.get_by_role('status').filter(has_text='项目已引用此确定研究版本')).to_be_visible()
        # Account dimension reuses the topic's neutral body/comment cache.
        page.get_by_role('button',name='单账号',exact=True).click()
        page.get_by_label('研究名称',exact=True).fill('旅行作者观察')
        discovery=page.request.get(remix_app+'/api/remix/research/discovery').json()
        page.get_by_label('实际发布者',exact=True).select_option(discovery['accounts'][0]['id'])
        page.get_by_role('button',name='保存研究范围',exact=True).click()
        expect(page.get_by_label('已保存的研究范围',exact=True)).not_to_have_value('')
        account_sid=page.get_by_label('已保存的研究范围',exact=True).input_value()
        expect(page.get_by_text('正文可复用 1',exact=True)).to_be_visible(timeout=10000)
        page.get_by_role('checkbox',name='确认将本批正文',exact=False).check()
        page.get_by_role('button',name='仅分析新增与变化，并更新综合',exact=True).click()
        account=wait_batch(page,remix_app,account_sid)
        assert all(c['kind']=='account' for c in account['runs'][0]['calls'])
        expect(page.get_by_text('同一账号的选题及表达观察',exact=True).first).to_be_visible(timeout=10000)
        page.get_by_role('button',name='单条内容',exact=True).click()
        page.get_by_role('button',name='共享素材研究（不生成改写）',exact=True).click()
        page.get_by_label('研究名称',exact=True).fill('单篇共享理解')
        page.get_by_label('研究素材',exact=True).fill('fixture-note')
        page.get_by_role('button',name='保存研究范围',exact=True).click()
        expect(page.get_by_label('已保存的研究范围',exact=True)).not_to_have_value('')
        single_sid=page.get_by_label('已保存的研究范围',exact=True).input_value()
        expect(page.get_by_text('正文可复用 1',exact=True)).to_be_visible(timeout=10000)
        page.get_by_role('checkbox',name='确认将本批正文',exact=False).check()
        page.get_by_role('button',name='仅分析新增与变化，并更新综合',exact=True).click()
        single=wait_batch(page,remix_app,single_sid)
        assert all(c['kind']=='single' for c in single['runs'][0]['calls'])
        expect(page.get_by_role('heading',name='可追溯研究版本',exact=True)).to_be_visible(timeout=10000)
        proof=Path('/tmp/mediacrawler-research-proof');proof.mkdir(exist_ok=True)
        page.screenshot(path=str(proof/'desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(proof/'mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        # Fixed reference is visible in the project, alongside its update notice.
        page.set_viewport_size({'width':1440,'height':1100})
        page.get_by_role('button',name='内容项目',exact=True).click()
        page.get_by_role('button',name='研究引用项目',exact=False).click()
        page.get_by_text('查看固定研究快照',exact=False).click()
        expect(page.get_by_text('有新研究版本可用',exact=False)).to_be_visible()
        page.get_by_role('button',name='在此项目下新建分析',exact=True).click()
        expect(page.get_by_label('当前内容项目',exact=True)).to_have_value(pid)
        page.get_by_label('选择内容',exact=True).select_option('fixture-note')
        page.get_by_role('checkbox',name='使用项目明确引用的研究版本',exact=False).check()
        page.get_by_role('checkbox',name='我同意',exact=False).check()
        page.get_by_role('button',name='开始分析',exact=True).click()
        expect(page.get_by_role('heading',name='内容摘要',exact=True)).to_be_visible(timeout=15000)
        jobs=page.request.get(remix_app+'/api/remix/jobs').json()['items']
        job=page.request.get(remix_app+'/api/remix/jobs/'+jobs[0]['id']).json()
        assert job['source']['project_research'][0]['revision_id']==old_id
        page.get_by_text('本次分析范围与写作经验',exact=True).click()
        expect(page.get_by_text('本次选用的项目研究快照',exact=False)).to_be_visible()
        assert not errors,errors
        browser.close()
