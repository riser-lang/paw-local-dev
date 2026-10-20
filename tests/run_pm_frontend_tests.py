"""C-06 real installed pages and local view models. No fabricated AI records."""
import json
import time
from pathlib import Path
import httpx
from jsonschema import Draft202012Validator
from tests.run_pm_tests import (api,BASE,running,project,read_rows,dossier,progress,accept,propose,
    INSTALL_DIR,clean_and_record,silent_install,clean_environment_on_error)
from tests.run_pm_versions_tests import import_doc
from tests.run_pm_updates_tests import seed,make_conflict


_schema=json.loads((Path(__file__).resolve().parents[1]/'frontend/pm-src/view_model.schema.json').read_text(encoding='utf-8'))


def view(pid=None):
    data=api('GET','/v1/pm/view/projects/'+pid if pid else '/v1/pm/view/today')
    Draft202012Validator(_schema).validate(data)
    return data


def milestone(case,pid):
    # The user edits a real generated candidate before approval. No direct business insert.
    candidates=api('GET','/v1/web/brain/review/candidates')['items']
    todo=next(c for c in candidates if c['target_type']=='todo')
    payload={**todo['payload'],'title':'验收里程碑','project_id':pid,'due_at':None}
    result=api('POST','/v1/web/brain/review/candidates/'+todo['id']+'/edit',{'edited_payload':payload})
    if not result['ok'] or result['result']['status']!='applied':raise RuntimeError('待办核准未写入')
    return next(m for m in view(pid)['milestones'] if m['title']=='验收里程碑')


def stable_page(page,url):
    from urllib.parse import urlsplit, unquote
    path=urlsplit(url).path
    if path.rstrip('/')=='/app/pm/today':
        expected_api='/v1/pm/view/today'
    else:
        project_id=unquote(path.rsplit('/',1)[-1])
        expected_api='/v1/pm/view/projects/'+project_id
    with page.expect_response(
        lambda response: response.request.method=='GET'
        and urlsplit(response.url).path==expected_api
        and response.status==200,
        timeout=30000,
    ):
        page.goto(url,wait_until='domcontentloaded',timeout=30000)
    page.locator('.pm-page h1').first.wait_for(state='visible')
    page.evaluate('document.fonts.ready')
    page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')


def capture(c,page,width,suffix=''):
    measure='''()=>({viewport:innerWidth,scroll:document.documentElement.scrollWidth,layout:document.querySelector('[data-pm-layout]')?.dataset.pmLayout,
      overflow:[...document.querySelectorAll('body *')].filter(n=>n.getBoundingClientRect().right>innerWidth+1).slice(0,8).map(n=>({tag:n.tagName,cls:n.getAttribute('class'),right:n.getBoundingClientRect().right}))})'''
    before=page.evaluate(measure)
    if page.locator('[data-pm-layout]').count():
        # The breakpoint event replaces the desktop chart with a different React tree.
        # Synchronize with that tree, never poll for the overflow assertion to become true.
        page.wait_for_function("document.querySelector('[data-pm-layout]').dataset.pmLayout === (innerWidth<=640?'phone':'desktop')",timeout=5000)
        page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    after=page.evaluate(measure)
    print('Responsive layout evidence',json.dumps({'case':c.id,'before':before,'after':after},ensure_ascii=False))
    if after['scroll']>after['viewport']:
        debug=Path('screenshots')/('debug_'+c.id.replace('-','_')+'_'+str(width)+suffix+'.png')
        page.screenshot(path=str(debug),full_page=True);c.screenshot(debug,page.url)
    c.check('no_page_overflow_'+str(width)+suffix,page.evaluate('document.documentElement.scrollWidth <= innerWidth'),True)
    out=Path('screenshots')/('screen_'+c.id.replace('-','_')+'_'+str(width)+suffix+'.png')
    page.screenshot(path=str(out),full_page=True);c.screenshot(out,page.url)


def run_c06(report,installer):
    from playwright.sync_api import sync_playwright,expect
    cases=['PM-C1-01','PM-C1-02','PM-C1-03','PM-C1-04','PM-C1-05','PM-C2-01','PM-C2-02','PM-C2-03','PM-C3-01','PM-C4-01','PM-C5-01']
    for case in cases:
        with report.case(case,'本地驾驶舱与设计迁移：'+case) as c:
            with clean_environment_on_error():
                c.fresh_install(clean_and_record,lambda:silent_install(installer)[2],INSTALL_DIR)
                with running():
                    pid=None;initial=None
                    if case=='PM-C1-05':
                        httpx.post('http://127.0.0.1:18000/stats/reset',timeout=5).raise_for_status()
                    elif case=='PM-C1-02':
                        pid,_,_,_,_=make_conflict(case)
                        import_doc(case,'方案修订.docx','冲刺项目。进度与里程碑：评审已启动。')
                        import_doc(case,'未定资料.docx','项目归属待确认。进度与里程碑：等待联调。')
                    elif case=='PM-C1-01':pid=project()
                    else:
                        pid,_,initial=seed(case,'原型已完成；联调已启动')
                        if case in ('PM-C1-04','PM-C2-03','PM-C4-01','PM-C5-01'):milestone(case,pid)
                        if case=='PM-C1-04':api('POST','/v1/web/workspace/projects',{'name':'山川项目'})
                        if case in ('PM-C2-02','PM-C4-01'):
                            propose(pid,'联调已启动。',initial['source_version_id'])
                    with sync_playwright() as p:
                        browser=p.chromium.launch(headless=False)
                        try:
                            page=browser.new_page(viewport={'width':1440,'height':1000})
                            home=BASE+'/app/pm/today';project_url=BASE+'/app/pm/projects/'+pid if pid else None
                            if case.startswith('PM-C1') or case in ('PM-C3-01','PM-C5-01'):
                                stable_page(page,home)
                            else:stable_page(page,project_url)
                            if case=='PM-C1-01':
                                body=page.inner_text('body')
                                c.check('cockpit_rendered',all(t in body for t in ['需要你决定','项目总览','AI 建议','系统状态']),True)
                                capture(c,page,1440)
                            elif case=='PM-C1-02':
                                counts=read_rows("SELECT (SELECT COUNT(*) FROM candidate WHERE status='pending') AS candidates,(SELECT COUNT(*) FROM pm_family_decision WHERE status='pending' AND project_id IS NULL) AS assignments,(SELECT COUNT(*) FROM pm_family_decision WHERE status='pending' AND project_id IS NOT NULL) AS versions,(SELECT COUNT(*) FROM pm_conflict WHERE status='open') AS conflicts,(SELECT COUNT(*) FROM pm_section_assessment WHERE status='needs_user') AS exclusive")[0]
                                data=view();items=data['decisions'];expected=sum(counts.values())
                                c.check('all_pending_categories_present',all(counts[k]>0 for k in ['candidates','assignments','versions','conflicts']),True)
                                c.check('inbox_count_matches_all_pending',len(items),expected)
                                c.check('rendered_card_count',page.locator('.pm-decision').count(),expected)
                                c.check('decision_consequences_complete',all(i['question'] and i['if_yes'] and i['if_no'] for i in items),True)
                                c.check('no_duplicate_decision_ids',len({i['id'] for i in items}),expected)
                                c.check('decision_titles_explain_action',all(not i['question'].startswith('是否批准：') and '。？' not in i['question'] for i in items),True)
                                c.check('candidate_outcomes_are_specific',all('按所列提议的内容和操作' not in i['if_yes'] for i in items),True)
                                for item in items:
                                    card=page.locator('[data-decision-id='+json.dumps(item['id'])+']')
                                    expect(card.locator('h3')).to_have_text(item['question'])
                                    if item.get('excerpt'):expect(card.locator('.pm-decision-excerpt')).to_have_text('原文：'+item['excerpt'])
                                c.check('impact_label_is_readable','影响中' not in page.inner_text('body'),True)
                                capture(c,page,1440)
                            elif case=='PM-C1-03':
                                candidates=api('GET','/v1/web/brain/review/candidates')['items']
                                chosen=[i for i in candidates if i['target_type'] in ('todo','knowledge')][:2]
                                c.check('two_real_generated_candidates',len(chosen),2)
                                ids={'candidate:'+i['id'] for i in chosen};items=[i for i in view()['decisions'] if i['id'] in ids]
                                before=read_rows('SELECT COUNT(*) AS n FROM approved_change')[0]['n']
                                for item in items:page.locator('[data-decision-id='+json.dumps(item['id'])+']').get_by_role('checkbox').check()
                                started=time.monotonic()
                                with page.expect_response(lambda r:r.request.method=='GET' and r.url.endswith('/v1/pm/view/today')) as refreshed:
                                    with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/v1/pm/inbox/decide')) as decided:
                                        page.get_by_role('button',name='全部选「是」',exact=True).click()
                                result=decided.value.json();refreshed.value.finished()
                                print('PM inbox batch evidence',json.dumps({'elapsed_seconds':round(time.monotonic()-started,3),'response':result},ensure_ascii=False))
                                c.check('batch_api_committed_each_selected_item',all(r['ok'] for r in result['items']) and len(result['items'])==2,True)
                                for item in items:expect(page.locator('[data-decision-id='+json.dumps(item['id'])+']')).to_have_count(0)
                                c.check('batch_items_disappear',ids.intersection(i['id'] for i in view()['decisions']),set())
                                c.check('batch_created_approved_changes',read_rows('SELECT COUNT(*) AS n FROM approved_change')[0]['n']-before,2)
                                committed=read_rows('SELECT a.status,c.result FROM approved_change a JOIN review_decision r ON r.id=a.approval_ref JOIN commit_record c ON c.change_id=a.change_id WHERE r.candidate_id IN (?,?)',tuple(i['id'] for i in chosen))
                                c.check('batch_governance_committed',committed,[{'status':'committed','result':'success'}]*2)
                                c.check('batch_business_records_exist',read_rows('SELECT (SELECT COUNT(*) FROM todo)+(SELECT COUNT(*) FROM knowledge_page) AS n')[0]['n'],2)
                            elif case=='PM-C1-04':
                                page.locator('.pm-gantt-chart svg path').first.wait_for()
                                c.check('one_gantt_row_per_active_project',page.locator('.pm-gantt-labels .pm-gantt-cell').count(),2)
                                c.check('today_line_present',page.locator('svg .pm-today').count(),1)
                                first=page.locator('.pm-gantt-chart svg path').evaluate_all('(nodes)=>nodes.map(n=>n.getAttribute("d"))')
                                stable_page(page,home)
                                second=page.locator('.pm-gantt-chart svg path').evaluate_all('(nodes)=>nodes.map(n=>n.getAttribute("d"))')
                                c.check('fixed_seed_svg_paths_identical',second,first)
                                c.check('hand_drawn_paths_present',len(first)>0,True)
                                capture(c,page,1440)
                            elif case=='PM-C1-05':
                                c.check('no_key_clear_prompt','需要填写 Key' in page.locator('[aria-labelledby="pm-h-ai"]').inner_text(),True)
                                c.check('no_fabricated_suggestions',view()['ai_suggestions'],[])
                                count=httpx.get('http://127.0.0.1:18000/stats',timeout=5).json()['chat_requests']
                                c.check('no_key_zero_model_requests',count,0)
                                capture(c,page,1440)
                            elif case=='PM-C2-01':
                                titles=[s['title'] for s in dossier(pid)['sections']]
                                c.check('all_configured_sections_rendered',[page.locator('#pm-sec-'+s['id']).inner_text() for s in dossier(pid)['sections']],titles)
                                section=page.locator('[aria-labelledby="pm-sec-progress"]')
                                section.get_by_role('button',name='来源 1',exact=True).click()
                                section.locator('.pm-source').first.click()
                                expect(page.locator('#source')).to_be_visible()
                                c.check('source_modal_actual_original','原型已完成' in page.locator('#source-text').inner_text(),True)
                                page.get_by_role('button',name='关闭',exact=True).click();capture(c,page,1440)
                            elif case=='PM-C2-02':
                                area=page.locator('[aria-labelledby="pm-sec-progress"]')
                                expect(area.locator('.pm-entry-main p')).to_have_text('联调已启动。')
                                selected=progress(dossier(pid))['current']
                                c.check('edited_conclusion_grounded_in_original',selected['text'] in api('GET',selected['source_url'])['body'],True)
                                area.get_by_role('button',name='撤销',exact=True).click()
                                expect(area.locator('.pm-entry-main p')).to_have_text(initial['text'])
                                c.check('ui_undo_restores_current',progress(dossier(pid))['current']['id'],initial['id'])
                            elif case=='PM-C2-03':
                                area=page.locator('[aria-labelledby="pm-sec-progress"]');area.locator('svg path[stroke-dasharray="3 5"]').first.wait_for()
                                c.check('missing_date_box_label','日期待补' in area.inner_text(),True)
                                c.check('same_milestone_in_gaps','验收里程碑：日期待补' in page.locator('[aria-labelledby="pm-sec-gaps"]').inner_text(),True)
                                m=view(pid)['milestones'][0];c.check('no_guessed_dates',[m[k] for k in ['plan_start','plan_end','actual_start','actual_end']],[None]*4)
                                capture(c,page,1440)
                            elif case=='PM-C3-01':
                                for route in ['/app/today','/app/projects','/app/review','/app/agnes']:
                                    response=page.goto(BASE+route,wait_until='domcontentloaded');page.locator('#root').wait_for(state='visible')
                                    page.wait_for_function('document.querySelector("#root").innerText.length > 20')
                                    # Dismiss the real first-run setup dialog through its close button.
                                    overlay=page.locator('#paw-modal-overlay')
                                    if overlay.is_visible():page.locator('#paw-close-btn').click()
                                    c.check('legacy_page_usable_'+route,response.status==200 and len(page.locator('#root').inner_text())>20,True)
                                    if route=='/app/agnes':
                                        c.check('legacy_assistant_heading',page.get_by_role('heading',name='AI助手',exact=True).count(),1)
                                        c.check('legacy_assistant_input',page.get_by_placeholder('问问 AI助手，例如：今天有哪些待办？').is_visible(),True)
                                        capture(c,page,1440,'_legacy_chat')
                                page.get_by_role('link',name='驾驶舱',exact=True).click();page.get_by_role('heading',name='今日驾驶舱',exact=True).wait_for()
                                c.check('sidebar_navigates_to_cockpit',page.url,home)
                                capture(c,page,1440)
                                comparison=Path('screenshots/compare_PM_C3_01.html')
                                comparison.write_text('<!doctype html><meta charset="utf-8"><title>真实页面截图对照</title><style>body{background:#F7F3EB;font:14px sans-serif}main{display:grid;grid-template-columns:1fr 1fr;gap:16px}img{width:100%;display:block}figure{margin:0}</style><h1>现有 AI 助手与本地驾驶舱</h1><p>同一次安装验收的两个真实页面截图；原始 PNG 保持不变。</p><main><figure><figcaption>/app/agnes</figcaption><img src="screen_PM_C3_01_1440_legacy_chat.png"></figure><figure><figcaption>/app/pm/today</figcaption><img src="screen_PM_C3_01_1440.png"></figure></main>',encoding='utf-8')
                                page.goto(BASE+'/app/projects/'+pid,wait_until='domcontentloaded')
                                page.locator('#root').wait_for(state='visible')
                                page.wait_for_function('document.querySelector("#root").innerText.length > 20')
                                if page.locator('#paw-modal-overlay').is_visible():page.locator('#paw-close-btn').click()
                                page.get_by_role('link',name='本项目档案',exact=True).click()
                                page.get_by_role('heading',name='冲刺项目',exact=True).wait_for()
                                c.check('legacy_project_links_to_local_dossier',page.url,project_url)
                            elif case=='PM-C4-01':
                                changes=view(pid)['changes'];c.check('two_actual_changes',len(changes),2)
                                for change in changes:
                                    card=page.locator('#pm-change-'+change['id'])
                                    c.check('timeline_original_source_'+change['id'],change['source']['title'] in card.inner_text(),True)
                                    c.check('timeline_affected_column_'+change['id'],'进度与里程碑' in card.inner_text(),True)
                                c.check('chart_and_timeline_marker_count',page.locator('svg .pm-change-dot').count(),len(changes))
                                date=view(pid)['today'];expected='今天 '+str(int(date[5:7]))+'月'+str(int(date[8:10]))+'日'
                                c.check('shared_today_date',page.locator('svg .pm-today').get_attribute('aria-label'),expected)
                                capture(c,page,1440)
                            else:
                                page.set_viewport_size({'width':390,'height':900});stable_page(page,home)
                                c.check('decisions_first_content_block',page.locator('.pm-today-page > section').first.get_attribute('aria-labelledby'),'pm-h-decisions')
                                c.check('mobile_portfolio_vertical',page.locator('.pm-vt').count()>0,True)
                                c.check('mobile_portfolio_no_gantt',page.locator('.pm-gantt').count(),0)
                                capture(c,page,390,'_cockpit')
                                stable_page(page,project_url)
                                c.check('mobile_milestones_vertical',page.locator('.pm-vt').count()>0,True)
                                c.check('mobile_project_no_gantt',page.locator('.pm-gantt').count(),0)
                                c.check('mobile_conclusions_not_clipped',page.locator('.pm-entry-main p,.pm-entry-main .pm-tag').evaluate_all('(nodes)=>nodes.length>0 && nodes.every(n=>{const r=n.getBoundingClientRect();return r.left>=8 && r.right<=document.documentElement.clientWidth-8 && n.scrollWidth<=n.clientWidth+1})'),True)
                                capture(c,page,390,'_project')
                        finally:browser.close()


def run_c06_negative(report,installer):
    from playwright.sync_api import sync_playwright
    with report.case('NC-PM-C1','2.3.9 缺少驾驶舱的负对照') as c:
        c.fresh_install(clean_and_record,lambda:silent_install(installer)[2],INSTALL_DIR)
        with running():
            project()
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=False)
                try:
                    page=browser.new_page(viewport={'width':1440,'height':1000})
                    page.goto(BASE+'/app/pm/today',wait_until='domcontentloaded')
                    page.wait_for_function('document.body.innerText.length>100')
                    body=page.inner_text('body')
                    c.check('cockpit_rendered',all(t in body for t in ['需要你决定','项目总览','AI 建议','系统状态']),True)
                finally:browser.close()
