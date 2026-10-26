"""All start requests intercepted; UI runs against temporary saved data only."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from tests.test_remix_browser import remix_app


def test_collection_limits_batch_navigation_picker_and_file_design(remix_app):
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('MEDIACRAWLER_TEST_BROWSER') or None,headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1100});errors=[];submissions=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true');localStorage.setItem('mediacrawler_env_checked','true')")
        def intercept(route):
            submissions.append(route.request.post_data_json)
            route.fulfill(json={'status':'started','message':'fixture only','task_id':'a'*32})
        page.route('**/api/crawler/start',intercept)
        page.goto(remix_app)
        page.get_by_label('采集方式',exact=True).select_option('browser')
        page.get_by_role('textbox',name='搜索关键词').fill('测试关键词')
        toggle=page.get_by_role('checkbox',name='启用网页筛选',exact=True)
        if not toggle.is_checked():toggle.check()
        page.get_by_label('最多滚动加载次数',exact=True).fill('30')
        page.get_by_label('内容数量上限',exact=True).fill('30')
        page.get_by_text('高级参数',exact=True).click()
        page.get_by_label('起始页',exact=True).fill('2')
        # Further filter edits must not silently restore the starting batch to 1.
        page.get_by_label('排序依据',exact=True).select_option('latest')
        expect(page.get_by_label('起始页',exact=True)).to_have_value('2')
        with page.expect_response('**/api/crawler/start'):
            page.get_by_role('button',name='执行关键词搜索',exact=True).click()
        assert len(submissions)==1 and submissions[0]['start_page']==2
        assert submissions[0]['max_notes_count']==30 and submissions[0]['xhs_filters']['max_scrolls']==30
        assert not submissions[0]['xhs_filters'].get('refresh_existing',False)
        # Exact task membership opens the correct local library, not an entire keyword pool.
        page.get_by_role('button',name='任务记录',exact=True).click()
        row=page.get_by_role('row').filter(has_text='测试旅行批次')
        expect(row.get_by_text('本次 1 条 · 新增 0 · 复用 1 · 刷新 0',exact=True)).to_be_visible()
        row.get_by_role('button',name='查看资料',exact=True).click()
        expect(page.get_by_role('heading',name='本次采集资料',exact=True)).to_be_visible()
        expect(page.get_by_label('筛选采集批次',exact=True)).to_have_value('b'*32)
        page.get_by_role('checkbox',name='选择资料 测试旅行笔记',exact=True).check()
        page.get_by_role('button',name='分析所选资料',exact=True).click()
        expect(page.get_by_label('资料纳入方式',exact=True)).to_have_value('selected')
        expect(page.get_by_role('checkbox',name='选择资料 测试旅行笔记',exact=True)).to_be_checked()
        page.get_by_label('研究名称',exact=True).fill('我选择的一条旅行资料')
        page.get_by_role('button',name='保存研究范围',exact=True).click()
        expect(page.get_by_label('已保存的研究范围',exact=True)).not_to_have_value('')
        sid=page.get_by_label('已保存的研究范围',exact=True).input_value()
        scope=page.request.get(remix_app+'/api/remix/research/spaces/'+sid).json()['space']
        assert scope['selection_mode']=='selected' and scope['include_ids']==['fixture-note']
        assert page.request.get(remix_app+'/api/remix/research/spaces/'+sid+'/plan').json()['total']==1
        # Creating a scope does not submit any paid research batch.
        assert page.request.get(remix_app+'/api/remix/research/spaces/'+sid).json()['runs']==[]
        page.get_by_role('button',name='任务记录',exact=True).click()
        page.get_by_role('row').filter(has_text='测试旅行批次').get_by_role('button',name='内容分析',exact=True).click()
        expect(page.get_by_label('资料纳入方式',exact=True)).to_have_value('task')
        page.get_by_role('button',name='保存研究范围',exact=True).click()
        expect(page.get_by_label('已保存的研究范围',exact=True)).not_to_have_value('')
        sid=page.get_by_label('已保存的研究范围',exact=True).input_value()
        scope=page.request.get(remix_app+'/api/remix/research/spaces/'+sid).json()['space']
        assert scope['task_id']=='b'*32 and scope['selection_mode']=='task'
        # Exports use semantic categories, natural titles and a compact preview.
        page.get_by_role('button',name='数据文件',exact=True).click()
        page.get_by_role('button',name='导出数据文件',exact=True).click()
        expect(page.get_by_role('heading',name='数据文件',exact=True,level=2)).to_be_visible()
        expect(page.locator('.wb-files-tabs').get_by_role('button',name='搜索记录',exact=False)).to_be_visible()
        expect(page.get_by_role('heading',name='测试旅行批次 · 检索记录',exact=True)).to_be_visible()
        page.locator('.wb-export-card').filter(has_text='测试旅行批次 · 检索记录').get_by_role('button',name='预览',exact=True).click()
        expect(page.get_by_role('region',name='数据文件预览')).to_be_visible()
        proof=Path('/tmp/mediacrawler-collection-proof');proof.mkdir(exist_ok=True)
        page.screenshot(path=str(proof/'desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(proof/'mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        assert not errors,errors
        browser.close()
