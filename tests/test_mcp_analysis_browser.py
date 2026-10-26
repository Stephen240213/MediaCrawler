"""Local file -> visible preview -> exact batch statistics -> research plan. No paid calls."""
import json
import os
import sqlite3
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from tests.test_remix_browser import remix_app
from tests.test_redfox_ingest import exported,note


def test_readable_preview_sync_source_filter_and_research_directions(remix_app,tmp_path):
    tid='d'*32
    folder=tmp_path/'media'/'xhs'/'redfox';folder.mkdir(parents=True)
    entries=[{**note(),'workId':f'flow-{i}','workTitle':f'MCP行 {i}'} for i in range(23)]
    (folder/(tid+'.json')).write_text(json.dumps([exported({'list':entries},keyword='')]))
    task=dict(id=tid,platform='xhs',crawler_type='detail',target='MCP无关键词批次',mcp_tool='xiaohongshu_get_dark_horse_notes',
              collection_method='redfox_mcp',outcome='completed',started_at='2026-10-26T08:00:00Z',finished_at='2026-10-26T08:01:00Z',exit_code=0)
    with sqlite3.connect(tmp_path/'workbench.sqlite3') as db:
        db.execute('INSERT INTO documents VALUES(?,?,?)',('task',tid,json.dumps(task)))
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('MEDIACRAWLER_TEST_BROWSER') or None,headless=True)
        page=browser.new_page(viewport={'width':651,'height':742});errors=[];paid=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.add_init_script("localStorage.setItem('mediacrawler_license_accepted','true');localStorage.setItem('mediacrawler_env_checked','true')")
        def block(route):
            paid.append(route.request.url);route.abort()
        page.route('**/api/crawler/start',block)
        page.route('**/api/remix/research/spaces/*/runs',block)
        page.goto(remix_app)
        page.get_by_role('navigation',name='工作台导航').get_by_role('button',name='数据文件',exact=True).click()
        page.get_by_role('button',name='导出数据文件',exact=True).click()
        card=page.locator('.wb-export-card').filter(has_text=tid)
        expect(card.get_by_text('23 条记录',exact=True)).to_be_visible()
        card.get_by_role('button',name='预览',exact=True).click()
        modal=page.get_by_role('dialog',name='文件预览',exact=True)
        expect(modal).to_be_in_viewport()
        expect(modal.get_by_role('heading',name='MCP行 0',exact=True)).to_be_visible()
        modal.get_by_role('button',name='下一页预览',exact=True).click()
        expect(modal.get_by_role('heading',name='MCP行 20',exact=True)).to_be_visible()
        modal.get_by_role('button',name='原始 JSON',exact=True).click()
        expect(modal.locator('pre')).to_contain_text('redfox_mcp')
        modal.get_by_role('button',name='关闭预览',exact=True).click()
        expect(modal).not_to_be_visible()
        with page.expect_response('**/api/crawler/redfox/import'):
            card.get_by_role('button',name='同步到分析',exact=True).click()
        expect(card.get_by_role('status')).to_contain_text('内容 23 条')
        expect(card.get_by_text('已入库：内容 23 条 · 评论 0 条',exact=True)).to_be_visible()
        card.get_by_role('button',name='查看数据分析',exact=True).click()
        expect(page.get_by_label('采集来源',exact=True)).to_have_value('redfox_mcp')
        expect(page.get_by_label('分析采集批次',exact=True)).to_have_value(tid)
        expect(page.locator('.an-cards .wb-card').filter(has_text='内容数').locator('strong')).to_have_text('23')
        page.get_by_label('采集来源',exact=True).select_option('')
        expect(page.locator('.an-cards .wb-card').filter(has_text='内容数').locator('strong')).to_have_text('24')
        page.get_by_role('navigation',name='工作台导航').get_by_role('button',name='数据文件',exact=True).click()
        page.get_by_role('button',name='导出数据文件',exact=True).click()
        page.locator('.wb-export-card').filter(has_text=tid).get_by_role('button',name='AI 研究',exact=True).click()
        page.get_by_role('button',name='互动与样本对比',exact=True).click()
        expect(page.get_by_label('自然语言研究问题',exact=True)).to_have_value('依据本地计算的互动统计，比较样本的表现与表达差异；标注未知和近似指标，不将相关性解释为因果或承诺爆款。')
        page.get_by_role('button',name='查看本地统计',exact=True).click()
        stats=page.get_by_role('region',name='本地研究统计',exact=True)
        expect(stats).to_contain_text('内容 23 条')
        expect(stats).to_contain_text('已保存评论 0 条')
        assert page.request.get(remix_app+'/api/remix/research/spaces').json()==[]
        page.get_by_role('button',name='保存并查看 AI 执行计划',exact=True).click()
        plan=page.get_by_role('region',name='增量处理计划',exact=True)
        expect(plan.get_by_role('heading',name='AI 执行计划与费用确认',exact=True)).to_be_visible()
        expect(plan.get_by_role('checkbox')).not_to_be_checked()
        expect(plan.get_by_role('button',name='仅分析新增与变化，并更新综合',exact=True)).to_be_disabled()
        assert not paid and not errors,(paid,errors)
        proof=Path('/tmp/mediacrawler-mcp-analysis-proof');proof.mkdir(exist_ok=True)
        plan.scroll_into_view_if_needed();page.screenshot(path=str(proof/'research-plan.png'))
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        browser.close()
