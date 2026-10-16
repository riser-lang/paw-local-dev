"""C-08 installed acceptance: scanned evidence, actual model calls, real governance."""
import httpx
from tests.run_pm_tests import (api,BASE,running,project,formal,read_rows,accept,cruise,
    INSTALL_DIR,clean_and_record,silent_install)
from tests.run_pm_versions_tests import import_doc
from tests.run_pm_frontend_tests import stable_page,capture,view
from tests.run_e2e_tests import timed_answer


def seed(case):
    pid=project()
    sid,_=import_doc(case,'研制方案_v1.docx','冲刺项目。\n进度与里程碑：原型已完成。\n风险与冲突：验收资料尚未复核。\n缺口：负责人待确认。\n下一步：整理验收记录。')
    return pid,sid


def advice(pid):return api('GET','/v1/pm/suggestions?project_id='+pid)['items']


def model(name):
    return api('POST','/v1/local/settings',{'provider':'custom','custom_api_key':'sk-fake-paw-test',
        'custom_base_url':'http://127.0.0.1:18000/v1','custom_model':name})


def run_c08(report,installer):
    from playwright.sync_api import sync_playwright,expect
    for case in ['PM-E1-01','PM-E1-02','PM-E1-03','PM-E2-01','PM-E2-02']:
        with report.case(case,'有依据的非正式建议：'+case) as c:
            c.fresh_install(clean_and_record,lambda:silent_install(installer)[2],INSTALL_DIR)
            with running():
                pid,sid=seed(case);before=formal()
                if case.startswith('PM-E1'):
                    cruise();items=advice(pid)
                    c.check('all_three_advice_categories',sorted({r['category'] for r in items}),['gaps','next','risks'])
                    c.check('formal_fields_unchanged_after_advice',formal(),before)
                    c.check('derived_storage_label',all(r['formal_state']==0 and r['label']=='AI 建议' for r in items),True)
                    for index,item in enumerate(items):
                        ref=item['basis'][0];original=api('GET',ref['href'])
                        c.check('advice_basis_opens_'+str(index),ref['source_version_id']==sid and ref['excerpt'] in original['body'],True)
                    if case=='PM-E1-01':
                        count=read_rows('SELECT COUNT(*) AS n FROM todo')[0]['n']
                        result=api('POST','/v1/pm/suggestions/'+items[0]['id']+'/adopt',{})
                        c.check('adoption_only_proposes',result['status'],'pending_review')
                        c.check('no_todo_before_approval',read_rows('SELECT COUNT(*) AS n FROM todo')[0]['n'],count)
                        c.check('project_unchanged_before_approval',formal(),before)
                        accept(result['candidate_id'])
                        c.check('one_approved_todo',read_rows('SELECT COUNT(*) AS n FROM todo')[0]['n'],count+1)
                        chain=read_rows('''SELECT r.decision,a.target_type,c.result FROM review_decision r
                            JOIN approved_change a ON a.approval_ref=r.id JOIN commit_record c ON c.change_id=a.change_id
                            WHERE r.candidate_id=?''',(result['candidate_id'],))
                        c.check('adoption_governance_committed',any(r['target_type']=='todo' and r['result']=='success' for r in chain),True)
                        c.check('advice_linked_to_approved_task',next(r for r in advice(pid) if r['id']==items[0]['id'])['state'],'adopted')
                    elif case=='PM-E1-03':
                        rejects=read_rows("SELECT COUNT(*) AS n FROM pm_contract_rejection WHERE contract='project_advice'")[0]['n']
                        model('fault-schema:project_advice');result=api('POST','/v1/pm/projects/'+pid+'/suggestions/generate',{})
                        c.check('invalid_schema_rejected',result['status'],'rejected')
                        c.check('schema_rejection_recorded',read_rows("SELECT COUNT(*) AS n FROM pm_contract_rejection WHERE contract='project_advice' AND reason='output_schema_invalid'")[0]['n']>rejects,True)
                        c.check('prior_valid_advice_preserved',[r['id'] for r in advice(pid)],[r['id'] for r in items])
                        c.check('bad_output_absent',not read_rows("SELECT id FROM pm_suggestion WHERE text LIKE '%FAULT_SCHEMA_OUTPUT%'"),True)
                        c.check('formal_unchanged_after_rejection',formal(),before)
                    with sync_playwright() as p:
                        browser=p.chromium.launch(headless=False)
                        try:
                            page=browser.new_page(viewport={'width':1440,'height':1000});stable_page(page,BASE+'/app/pm/projects/'+pid)
                            region=page.get_by_role('region',name='AI 建议',exact=True)
                            expect(region.locator('.pm-ai')).to_have_count(3)
                            c.check('distinct_advice_label',all('AI 建议' in t for t in region.locator('.pm-ai-label').all_text_contents()),True)
                            c.check('advice_separate_from_formal_sections',page.locator('.pm-sections .pm-ai').count(),0)
                            c.check('tinted_advice_background',region.locator('.pm-ai').first.evaluate("n=>getComputedStyle(n).backgroundColor") not in ('rgba(0, 0, 0, 0)','rgb(255, 255, 255)'),True)
                            c.check('bad_output_not_rendered','FAULT_SCHEMA_OUTPUT' not in page.inner_text('body'),True)
                            capture(c,page,1440);page.set_viewport_size({'width':390,'height':900});capture(c,page,390)
                            title=items[0]['basis'][0]['title']
                            region.get_by_role('button',name='依据：'+title,exact=True).first.click()
                            page.get_by_role('dialog',name='依据：'+title,exact=True).get_by_role('button',name='来源 · '+title,exact=True).click()
                            expect(page.locator('#source-text')).to_contain_text('原型已完成')
                        finally:browser.close()
                    c.check('today_uses_same_advice_ids',sorted(r['id'] for r in view()['ai_suggestions']),sorted(r['id'] for r in advice(pid)))
                else:
                    if case=='PM-E2-02':model('fault-advice-entities')
                    prior=httpx.get('http://127.0.0.1:18000/stats').json()['advice_fault_sentences']
                    session=api('POST','/v1/web/agy/chat',{'mode':'chat','project_id':pid})['session_id']
                    result=timed_answer(session,'冲刺项目后续最好如何优化？',case);text=result['response']
                    c.check('advice_question_answered',result['status'],'SUCCESS')
                    c.check('fact_has_readable_citation','〔依据：' in text,True)
                    c.check('advice_heading','建议（基于以上依据的推断）' in text,True)
                    claims=result['evidence_map']['claims']
                    c.check('facts_and_inferences_have_distinct_refs',any(r.get('kind')=='fact' and r.get('evidence') for r in claims) and any(r.get('kind')=='recommendation' and r.get('derived_from') for r in claims),True)
                    if case=='PM-E2-02':
                        stats=httpx.get('http://127.0.0.1:18000/stats').json()
                        c.check('model_injected_both_fault_sentences',stats['advice_fault_sentences']-prior>=2,True)
                        c.check('unbacked_number_removed','987654321' not in text,True)
                        c.check('unbacked_person_removed','顾时安' not in text,True)
                        c.check('number_filter_recorded',result['advice_filter']['reasons'].get('uncited_number',0)>=1,True)
                        c.check('person_filter_recorded',result['advice_filter']['reasons'].get('uncited_person',0)>=1,True)
                        c.check('grounded_fact_preserved','原型已完成' in text,True)
                    c.check('answer_never_changes_formal_records',formal(),before)
