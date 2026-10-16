"""C-05 installed-app acceptance: real watcher/capture inputs, SQL readback only."""
import hashlib
import os
import shutil
import time
from pathlib import Path
from tests.run_pm_tests import (api,BASE,running,project,read_rows,dossier,progress,
    INSTALL_DIR,clean_and_record,silent_install,configure_mock_settings)


def wait_for(fn, description, seconds=60):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        result=fn()
        if result:return result
        time.sleep(.2)
    raise RuntimeError('等待实际处理完成失败：'+description)


def receipt(sid):
    return next((r for r in api('GET','/v1/pm/receipts')['items'] if r['source_version_id']==sid),None)


def watch_input(case,title,body):
    from docx import Document
    directory=Path(os.environ['TEMP'])/('PAW_PM_ROUTE_'+case)
    directory.mkdir(parents=True,exist_ok=True)
    configure_mock_settings(directory)
    staging=Path(os.environ['TEMP'])/('PAW_PM_STAGE_'+case)
    staging.mkdir(parents=True,exist_ok=True)
    file=staging/title
    document=Document();document.add_paragraph(body);document.save(file)
    sid=hashlib.sha256(file.read_bytes()).hexdigest()
    shutil.copyfile(file,directory/title)
    wait_for(lambda: receipt(sid), '文件监控产生输入回执')
    wait_for(lambda: read_rows("SELECT * FROM analyzed_source WHERE file_hash=? AND completion_state='complete'",(sid,)), '文件分析完成')
    return sid,directory


def check_routed(c,pid,sid,fragment,source='standing_user_rule'):
    row=wait_for(lambda: (r if (r:=receipt(sid)) and r['state']=='routed' and r['updated_sections'] else None),'已提交归属及栏目')
    c.check('destination_existing_project',row['project_id'],pid)
    c.check('actual_updated_column',row['updated_sections'],['进度与里程碑'])
    c.check('actual_conclusion',fragment in progress(dossier(pid))['current']['text'],True)
    c.check('assignment_approval_origin',row['authorization']['approval_source'],source)
    c.check('original_source_readable',fragment in api('GET',row['source_url'])['body'],True)
    chain=read_rows('SELECT a.status,r.approval_source FROM approved_change a JOIN pm_review_decision_audit r ON r.id=a.approval_ref WHERE a.change_id=?',(row['assignment_change_id'],))
    c.check('assignment_governance_chain',chain,[{'status':'committed','approval_source':source}])
    c.check('no_unapproved_new_project',read_rows('SELECT COUNT(*) AS n FROM project')[0]['n'],1)
    return row


def receipts_page(c,width,expected,decide_pid=None):
    from playwright.sync_api import sync_playwright
    url=BASE+'/app/pm/receipts'
    output=Path('screenshots')/('screen_'+c.id.replace('-','_')+'_'+str(width)+'.png')
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=False)
        try:
            page=browser.new_page(viewport={'width':width,'height':1000})
            page.goto(url,wait_until='networkidle')
            page.get_by_role('heading',name='资料去向',exact=True).wait_for()
            if decide_pid:
                page.get_by_label('归属项目',exact=True).select_option(decide_pid)
                page.get_by_role('button',name='确认归属',exact=True).click()
                page.get_by_text('已归入 冲刺项目',exact=True).first.wait_for()
            body=page.inner_text('body')
            for fragment in expected:c.check('rendered_'+str(width)+'_'+fragment,fragment in body,True)
            c.check('no_horizontal_overflow_'+str(width),page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'),True)
            page.screenshot(path=str(output),full_page=True)
        finally:browser.close()
    c.screenshot(output,url)


def run_c05(report,installer):
    for case in ['PM-D1-01','PM-D1-02','PM-D1-03','PM-D2-01','PM-D2-02']:
        with report.case(case,'输入归属与回执：'+case) as c:
            c.fresh_install(clean_and_record,lambda:silent_install(installer)[2],INSTALL_DIR)
            with running():
                pid=project()
                if case=='PM-D1-03':
                    configure_mock_settings()
                    body='冲刺项目。\n进度与里程碑：原型已交付。'
                    capture=api('POST','/v1/web/workspace/captures',{'raw_text':body,'source':'local_pm'})
                    sid=hashlib.sha256((capture['id']+'\0'+body).encode()).hexdigest()
                    row=check_routed(c,pid,sid,'原型已交付')
                    c.check('capture_receipt',row['kind'],'capture')
                    c.check('capture_summary',body[:10] in row['title'],True)
                    wait_for(lambda:read_rows('SELECT id FROM capture WHERE id=? AND processed_at IS NOT NULL',(capture['id'],)),'随手记完成')
                elif case=='PM-D1-01':
                    sid,_=watch_input(case,'进度报告.docx','冲刺项目。\n进度与里程碑：原型已交付。')
                    check_routed(c,pid,sid,'原型已交付')
                    c.check('actual_watcher_pipeline',read_rows('SELECT pipeline FROM analyzed_source WHERE file_hash=?',(sid,))[0]['pipeline'],'watcher')
                    receipts_page(c,1440,['进度报告.docx','进度与里程碑','按用户常设规则自动写入','用户 2026-10-15 设定'])
                    receipts_page(c,390,['进度报告.docx','已归入 冲刺项目'])
                else:
                    sid,directory=watch_input(case,'待分配资料.docx','项目归属待确认。\n进度与里程碑：联调已启动。')
                    before=receipt(sid)
                    c.check('uncertain_waits_for_user',before['state'],'needs_decision')
                    c.check('uncertain_receipt_note',before['note'],'等待你确认归属')
                    c.check('uncertain_not_assigned',before['project_id'],None)
                    c.check('uncertain_no_formal_conclusion',progress(dossier(pid))['current'],None)
                    if case=='PM-D1-02':
                        receipts_page(c,1440,['已归入 冲刺项目','用户亲手批准'],decide_pid=pid)
                    else:
                        if case=='PM-D2-02':
                            rejected=api('POST','/v1/pm/input-decisions/'+before['decision_id']+'/reject',{})
                            c.check('rejection_keeps_source',receipt(sid)['state'],'rejected')
                            c.check('rejected_original_retained','联调已启动' in api('GET',before['source_url'])['body'],True)
                            api('POST','/v1/pm/changes/'+rejected['change_id']+'/undo',{})
                            c.check('undo_restores_pending',receipt(sid)['state'],'needs_decision')
                        api('POST','/v1/pm/input-decisions/'+before['decision_id']+'/resolve',{'project_id':pid})
                    row=check_routed(c,pid,sid,'联调已启动','direct_user')
                    c.check('same_receipt_after_decision',row['id'],before['id'])
                    if case=='PM-D2-01':
                        second,_=watch_input(case,'自动归属资料.docx','冲刺项目。\n进度与里程碑：原型已交付。')
                        check_routed(c,pid,second,'原型已交付')
                        c.check('every_input_has_receipt',len(api('GET','/v1/pm/receipts')['items']),2)
                        api('POST','/v1/local/scanner/start',{'directory':str(directory),'force_restart':True})
                        wait_for(lambda:api('GET','/v1/local/scanner/progress')['status']=='completed','重复扫描结束')
                        c.check('rescan_does_not_duplicate_receipts',len(api('GET','/v1/pm/receipts')['items']),2)
                        for width in (1440,390):receipts_page(c,width,['待分配资料.docx','自动归属资料.docx','进度与里程碑','用户亲手批准','按用户常设规则自动写入'])
