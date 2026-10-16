"""C-09 installed acceptance. Future-time checks are explicitly labelled previews."""
from datetime import datetime,timedelta,timezone
from tests.run_pm_tests import (api,BASE,running,project,formal,read_rows,accept,
    INSTALL_DIR,clean_and_record,silent_install)
from tests.run_pm_versions_tests import import_doc
from tests.run_pm_frontend_tests import stable_page,capture,view
from tests.run_e2e_tests import timed_answer


def run_c09(report,installer):
    from playwright.sync_api import sync_playwright,expect
    for case in ['PM-E3-01','PM-E4-01']:
        with report.case(case,'每日检查与真实知识缺口：'+case) as c:
            c.fresh_install(clean_and_record,lambda:silent_install(installer)[2],INSTALL_DIR)
            with running():
                pid=project()
                if case=='PM-E3-01':
                    old,_=import_doc(case,'方案_v1.docx','冲刺项目。\n进度与里程碑：交付状态为已完成。\n风险与冲突：交付状态为尚未完成。')
                    todo=next(r for r in api('GET','/v1/web/brain/review/candidates')['items'] if r['target_type']=='todo')
                    committed=api('POST','/v1/web/brain/review/candidates/'+todo['id']+'/edit',{'edited_payload':{**todo['payload'],'project_id':pid,'title':'复核交付记录'}})
                    c.check('task_approved_while_v1_current',committed['ok'] and committed['result']['status']=='applied',True)
                    import_doc(case,'方案_v2.docx','冲刺项目。\n进度与里程碑：交付状态为已完成。\n风险与冲突：交付状态为尚未完成。\n下一步：整理归档。')
                    import_doc(case,'待裁定记录.docx','冲刺项目。\n生效日期：2026-10-01。生效日期：2026-10-02。')
                    before=formal();count=read_rows('SELECT COUNT(*) AS n FROM approved_change')[0]['n']
                    actual=api('POST','/v1/pm/health-checks/run',{'project_id':pid,'force':True})['items'][0]
                    c.check('actual_check_is_not_preview',actual['preview'],False)
                    c.check('fresh_records_not_reported_as_aged',not any(i['kind'] in ('undecided_family','no_progress') for i in actual['items']),True)
                    reference=(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()
                    preview=api('POST','/v1/pm/health-checks/run',{'project_id':pid,'reference_at':reference})['items'][0]
                    c.check('preview_reference_is_explicit',preview['preview'] and preview['reference_at']==reference,True)
                    c.check('four_health_findings',sorted({i['kind'] for i in preview['items']}),['no_progress','section_contradiction','stale_reference','undecided_family'])
                    c.check('health_check_is_observation_only',formal(),before)
                    c.check('no_health_formal_approval',read_rows('SELECT COUNT(*) AS n FROM approved_change')[0]['n'],count)
                    c.check('normal_page_uses_actual_check',view(pid)['health_checks'][0]['id'],actual['id'])
                    suffix='?health_run='+preview['id']
                else:
                    import_doc(case,'研发记录.docx','冲刺项目。\n进度与里程碑：原型已完成。')
                    before=formal();question='冲刺项目的采购预算总额是多少？'
                    for i in range(2):
                        session=api('POST','/v1/web/agy/chat',{'mode':'chat','project_id':pid})['session_id']
                        response=timed_answer(session,question,case+'_'+str(i))
                        c.check('actual_insufficient_evidence_answer_'+str(i),response['response'].startswith('依据不足'),True)
                        c.check('answer_links_observed_gap_'+str(i),bool(response.get('knowledge_gap_id')),True)
                    gaps=api('GET','/v1/pm/knowledge-gaps?project_id='+pid)['items']
                    c.check('one_deduplicated_gap',len(gaps),1)
                    c.check('repeat_count',gaps[0]['occurrences'],2)
                    c.check('original_question_preserved',gaps[0]['question'],question)
                    c.check('gap_records_do_not_write_project',formal(),before);suffix=''
                with sync_playwright() as p:
                    browser=p.chromium.launch(headless=False)
                    try:
                        page=browser.new_page(viewport={'width':1440,'height':1000})
                        for path,label in [('/app/pm/projects/'+pid,'_project'),('/app/pm/today','_cockpit')]:
                            stable_page(page,BASE+path+suffix)
                            region=page.get_by_role('region',name='项目健康检查与知识缺口',exact=True)
                            if case=='PM-E3-01':
                                expect(region).to_contain_text('参考时间预演')
                                c.check('four_visible_findings'+label,all(t in region.inner_text() for t in ['正式待办或知识仍引用历史版本','栏目可能矛盾','文档族待裁定已超过7天','超过14天没有已确认的进展更新']),True)
                            else:expect(region).to_contain_text(question);expect(region).to_contain_text('出现 2 次')
                            page.set_viewport_size({'width':1440,'height':1000});capture(c,page,1440,label)
                            page.set_viewport_size({'width':390,'height':900});capture(c,page,390,label)
                    finally:browser.close()
