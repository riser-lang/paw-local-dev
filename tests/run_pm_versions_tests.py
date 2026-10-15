"""C-03 installed-app acceptance; no direct writes to AI/formal records."""
import hashlib
import json
import logging
import os
import time
from pathlib import Path
import httpx
from tests.run_pm_tests import (api,BASE,running,project,read_rows,dossier,progress,accept,propose,
    INSTALL_DIR,clean_and_record,silent_install,configure_mock_settings,poll_candidates_from_scanner)
from tests.run_e2e_tests import parse_candidate_types,timed_answer

logger=logging.getLogger('pm.versions.tests')


def import_doc(case,title,body,baseline=False):
    from docx import Document
    directory=Path(os.environ['TEMP'])/('PAW_PM_VERSION_'+case)
    directory.mkdir(parents=True,exist_ok=True)
    document=Document();document.add_paragraph(body)
    path=directory/title
    document.save(path)
    sid=hashlib.sha256(path.read_bytes()).hexdigest()
    configure_mock_settings(directory)
    api('POST','/v1/local/scanner/start',{'directory':str(directory),'force_restart':True})
    candidates=poll_candidates_from_scanner()
    matching=[r for r in candidates if sid in json.loads(r.get('source_refs') or '[]')]
    if not matching and not baseline:
        raise RuntimeError('文档导入没有可核对的来源候选：'+title)
    if baseline:
        from tests.run_e2e_tests import get_db_path
        ingested=read_rows('SELECT file_hash FROM analyzed_source WHERE file_hash=?',(sid,))
        result_path=get_db_path().parent/'evidence'/(path.stem+'_'+sid[:8])/'reader_result.json'
        parsed=json.loads(result_path.read_text(encoding='utf-8')) if result_path.is_file() else {}
        original='\n'.join(str(b.get('text','')) for b in parsed.get('text_blocks',[]))
        if len(ingested)!=1 or body not in original:
            raise RuntimeError('baseline 资料未真实摄取，不能作为版本裁定负对照')
    return sid,matching


def family(pid):
    return api('GET','/v1/pm/projects/'+pid+'/families')['items'][0]


def capture_versions(c,pid,width):
    from playwright.sync_api import sync_playwright
    url=BASE+'/app/pm/projects/'+pid
    output=Path('screenshots')/('screen_PM_B2_01_'+str(width)+'.png')
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=False)
        try:
            page=browser.new_page(viewport={'width':width,'height':900})
            page.goto(url,wait_until='networkidle')
            page.get_by_role('heading',name='资料版本',exact=True).wait_for()
            text=page.inner_text('body')
            c.check('version_status_rendered_'+str(width),'方案_v2.docx · ● 当前版本' in text and '方案_v1.docx · 历史 · 已替换' in text,True)
            if width==390:
                c.check('versions_mobile_no_overflow',page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'),True)
            page.screenshot(path=str(output),full_page=True)
        finally:
            browser.close()
    c.screenshot(output,url)


def prepare(case,negative=False):
    pid=project()
    name='plan' if negative else '方案'
    first,proposals=import_doc(case,name+'_v1.docx','冲刺项目。第一版：琥珀原型已完成。')
    cid=parse_candidate_types(proposals)[0]
    if not cid:
        raise RuntimeError('第一版没有正常项目更新提议')
    accept(cid)
    return pid,first,name


def assert_transition(c,pid,first,second,negative=False):
    response=httpx.get(BASE+'/v1/pm/projects/'+pid+'/families',timeout=10)
    value=response.json()['items'][0] if response.status_code==200 else {'versions':[]}
    statuses={v['source_version_id']:v['status'] for v in value['versions']}
    c.check('old_version_superseded',statuses.get(first),'superseded')
    if negative:
        return
    c.check('new_version_is_current',value['current_source_id'],second)
    linked=[r['source_version_id'] for s in dossier(pid)['sections'] for r in ([s['current']] if s['current'] else []) if r['source_version_id'] in (first,second)]
    c.check('all_current_references_switched',set(linked),{second})
    c.check('new_content_actually_reflected','靛蓝联调' in progress(dossier(pid))['current']['text'],True)
    log=read_rows('SELECT r.review_channel,r.review_notes,a.status FROM review_decision r JOIN approved_change a ON a.approval_ref=r.id WHERE a.change_id=?',(value['rulings'][0]['change_id'],))
    c.check('version_ruling_committed_with_rule_provenance',log[0]['status']=='committed' and '用户 2026-10-15 设定' in log[0]['review_notes'],True)


def run_c03(report,installer):
    for case in ['PM-B2-01','PM-B2-02','PM-B2-03','PM-B2-04','PM-B2-05']:
        with report.case(case,'文档族版本裁定：'+case) as c:
            c.fresh_install(clean_and_record,lambda:silent_install(installer)[2],INSTALL_DIR)
            with running():
                if case=='PM-B2-02':
                    pid=project()
                    first,_=import_doc(case,'方案_v3.docx','冲刺项目。生效日期：2026-10-01。较早的白橡设计。')
                    second,_=import_doc(case,'方案_v2.docx','冲刺项目。生效日期：2026-10-14。较晚的蓝杉设计。')
                    value=family(pid)
                    c.check('effective_date_precedes_version_number',value['current_source_id'],second)
                    c.check('earlier_effective_version_superseded',next(v['status'] for v in value['versions'] if v['source_version_id']==first),'superseded')
                    continue
                pid,first,name=prepare(case)
                if case=='PM-B2-03':
                    before=dossier(pid)
                    second,_=import_doc(case,'方案修订.docx','冲刺项目。正在讨论的另一个方向。')
                    cards=api('GET','/v1/pm/decisions')['items']
                    card=next((r for r in cards if r['source_version_id']==second),None)
                    c.check('uncertain_family_requires_user',bool(card and '需要你决定' in card['prompt']),True)
                    c.check('formal_project_unchanged',dossier(pid)['project'],before['project'])
                    c.check('current_conclusion_unchanged',progress(dossier(pid))['current'],progress(before)['current'])
                    c.check('not_silently_joined',len(family(pid)['versions']),1)
                    continue
                initial=progress(dossier(pid))['current']
                second,_=import_doc(case,name+'_v2.docx','冲刺项目。第二版：靛蓝联调已启动。')
                assert_transition(c,pid,first,second)
                value=family(pid)
                if case=='PM-B2-01':
                    capture_versions(c,pid,1440)
                    capture_versions(c,pid,390)
                if case=='PM-B2-04':
                    change=value['rulings'][0]['change_id']
                    api('POST','/v1/pm/changes/'+change+'/undo',{})
                    restored=family(pid)
                    c.check('old_version_restored_current',restored['current_source_id'],first)
                    c.check('section_reference_restored',progress(dossier(pid))['current']['id'],initial['id'])
                    c.check('undo_receipt_matches',dossier(pid)['undo_records'][0]['original_change_id'],change)
                elif case=='PM-B2-05':
                    session=api('POST','/v1/web/agy/chat',{'mode':'chat','project_id':pid})['session_id']
                    result=timed_answer(session,'请介绍冲刺项目当前方案的主要进展','PM-B2-05')
                    answer=result.get('response','')
                    evidence=json.dumps(result.get('evidence',{}),ensure_ascii=False)
                    c.check('assistant_cites_current_version','方案_v2.docx' in answer+evidence,True)
                    c.check('assistant_excludes_superseded_version','方案_v1.docx' in answer+evidence or '琥珀原型' in answer,False)
                    c.check('assistant_current_content','靛蓝联调' in answer,True)


def run_c03_negative(report,installer):
    with report.case('NC-PM-B2','v2.3.9 缺少自动版本裁定的负对照') as c:
        c.fresh_install(clean_and_record,lambda:silent_install(installer)[2],INSTALL_DIR)
        with running():
            # ASCII transport paths avoid the unrelated old English-Windows child encoding bug.
            pid,first,name=prepare('negative',negative=True)
            second,_=import_doc('negative',name+'_v2.docx','冲刺项目。第二版：靛蓝联调已启动。',baseline=True)
            c.check('both_versions_ingested',len(read_rows('SELECT file_hash FROM analyzed_source')),2)
            assert_transition(c,pid,first,second,negative=True)
