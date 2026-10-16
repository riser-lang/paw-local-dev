"""Installed C-07 acceptance, including real baseline overlay upgrade."""
import sqlite3
import re
from contextlib import closing
from datetime import datetime,timedelta,timezone
from pathlib import Path
from tests.run_pm_tests import (api,BASE,running,project,read_rows,accept,cruise,
    INSTALL_DIR,clean_and_record,silent_install,configure_mock_settings)
from tests.run_pm_versions_tests import import_doc
from tests.run_pm_frontend_tests import stable_page,capture,view
from tests.run_pm_routing_tests import wait_for
from tests.run_e2e_tests import get_db_path

TABLES=('project','todo','knowledge_page','candidate','review_decision','approved_change','commit_record','pm_section_revision')


def live_counts():return {table:read_rows('SELECT COUNT(*) AS n FROM '+table)[0]['n'] for table in TABLES}


def check_backup(c,backup):
    with closing(sqlite3.connect(backup['path'])) as conn:
        c.check('backup_integrity',conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        actual={table:conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in TABLES}
    c.check('latest_snapshot_matches_formal_counts',actual,live_counts())


def state():return {r['key']:r for r in api('GET','/v1/pm/system/status')['items']}


def run_c07(report,installer):
    from playwright.sync_api import sync_playwright,expect
    for case in ['PM-A1-01','PM-A2-01','PM-A4-01','PM-UP-01']:
        with report.case(case,'本地备份、状态与升级：'+case) as c:
            if case=='PM-UP-01':
                baseline=next(Path('baseline_pm').glob('*.exe'))
                c.fresh_install(clean_and_record,lambda:silent_install(baseline)[2],INSTALL_DIR)
                with running():
                    pid=project()
                    _,candidates=import_doc(case,'upgrade.docx','冲刺项目。原型已交付。',baseline=True)
                    todo=next(i for i in candidates if i['candidate_type']=='todo');knowledge=next(i for i in candidates if i['candidate_type']=='knowledge')
                    accept(todo['id']);accept(knowledge['id'])
                    before=read_rows('SELECT (SELECT COUNT(*) FROM project) AS projects,(SELECT COUNT(*) FROM todo) AS todos,(SELECT COUNT(*) FROM knowledge_page) AS knowledge')[0]
                    c.check('baseline_normal_records_created',before,{'projects':1,'todos':1,'knowledge':1})
                c.check('overlay_installer_exit_code',silent_install(installer)[2],0)
                with running():
                    after=read_rows('SELECT (SELECT COUNT(*) FROM project) AS projects,(SELECT COUNT(*) FROM todo) AS todos,(SELECT COUNT(*) FROM knowledge_page) AS knowledge')[0]
                    c.check('overlay_preserves_formal_counts',after,before)
                    c.check('upgraded_sqlite_integrity',read_rows('PRAGMA integrity_check')[0]['integrity_check'],'ok')
                    vm=view(pid);c.check('upgraded_project_name',vm['project']['name'],'冲刺项目')
                    c.check('upgraded_todo_in_milestones',any(m['title']=='完成系统流水线交付与文档归档' for m in vm['milestones']),True)
                    c.check('upgraded_knowledge_in_sources',any(s['kind']=='knowledge' and s['title']=='端侧沙箱安全隔离规范' for s in vm['sources']),True)
                    with sync_playwright() as p:
                        browser=p.chromium.launch(headless=False)
                        try:
                            page=browser.new_page(viewport={'width':1440,'height':1000});stable_page(page,BASE+'/app/pm/today')
                            c.check('upgraded_cockpit_project_visible','冲刺项目' in page.inner_text('body'),True);capture(c,page,1440,'_cockpit')
                            stable_page(page,BASE+'/app/pm/projects/'+pid)
                            c.check('upgraded_dossier_records_visible',all(t in page.inner_text('body') for t in ['冲刺项目','完成系统流水线交付与文档归档','端侧沙箱安全隔离规范']),True);capture(c,page,1440,'_project')
                        finally:browser.close()
                continue
            c.fresh_install(clean_and_record,lambda:silent_install(installer)[2],INSTALL_DIR)
            with running():
                pid=project();import_doc(case,'输入资料.docx','冲刺项目。\n进度与里程碑：原型已交付。')
                if case=='PM-A1-01':
                    limits=api('POST','/v1/pm/backups/policy',{'max_count':3,'max_bytes':8*1024**2})
                    manual=api('POST','/v1/local/backup/create',{'tag':'manual'})
                    for i in range(2,5):import_doc(case,'事项'+str(i)+'.docx','冲刺项目。\n进度与里程碑：第 '+str(i)+' 阶段已启动。')
                    pending=api('GET','/v1/web/brain/review/candidates')['items']
                    c.check('twelve_real_candidates_available',len(pending)>=12,True)
                    for i in range(6):
                        ids=[r['id'] for r in pending[i*2:i*2+2]]
                        result=api('POST','/v1/web/brain/review/batch_accept',{'candidate_ids':ids,'reviewer':'LocalTester'})
                        c.check('batch_committed_'+str(i),result['accepted_count'],2)
                        c.check('batch_no_failures_'+str(i),result['failed_count'],0)
                    backups=api('GET','/v1/pm/backups')['items'];automatic=[b for b in backups if b['automatic']]
                    c.check('automatic_count_within_policy',0<len(automatic)<=limits['max_count'],True)
                    c.check('automatic_bytes_within_policy',sum(b['size_bytes'] for b in automatic)<=limits['max_bytes'],True)
                    c.check('manual_backup_preserved',Path(manual['backup_path']).is_file(),True)
                    check_backup(c,automatic[0])
                elif case=='PM-A2-01':
                    backup=api('POST','/v1/local/backup/create',{'tag':'manual'})
                    before=live_counts();result=api('POST','/v1/pm/backups/selfcheck',{})
                    c.check('isolated_restore_passed',result['ok'],True)
                    c.check('restored_key_counts',result['counts'],before)
                    c.check('temporary_copy_cleaned',result['cleaned'],True)
                    temp=get_db_path().parent/'backups/.restore-check'/result['temp_dir_name']
                    c.check('temporary_directory_absent',temp.exists(),False)
                    c.check('production_counts_unchanged',live_counts(),before)
                    c.check('original_backup_preserved',Path(backup['backup_path']).is_file(),True)
                    c.check('selfcheck_in_system_status','恢复自检：通过' in state()['backup']['detail'],True)
                else:
                    api('POST','/v1/local/backup/create',{'tag':'manual'});cruise()
                    c.check('actual_model_connectivity_probe',api('POST','/v1/local/provider/test_connection',{})['ok'],True)
                    wait_for(lambda:state()['scan']['last_success_at'],'真实扫描成功状态')
                    statuses=state()
                    for key in ['patrol','scan','backup','model']:
                        c.check('state_text_'+key,bool(statuses[key]['summary']) and statuses[key]['state']=='ok',True)
                        c.check('last_success_'+key,bool(statuses[key]['last_success_at']),True)
                    # Reviewer expressly requires aging the operational timestamp. No AI/formal table is altered.
                    with closing(sqlite3.connect(str(get_db_path()))) as conn:
                        conn.execute("UPDATE pm_system_health SET last_success_at=? WHERE key='patrol'",((datetime.now(timezone.utc)-timedelta(hours=4)).isoformat(),));conn.commit()
                    c.check('four_hour_patrol_is_abnormal',state()['patrol']['state'],'error')
                with sync_playwright() as p:
                    browser=p.chromium.launch(headless=False)
                    try:
                        page=browser.new_page(viewport={'width':1440,'height':1000});stable_page(page,BASE+'/app/pm/today')
                        page.get_by_role('heading',name='本地维护',exact=True).wait_for()
                        area=page.get_by_role('region',name='本地维护',exact=True)
                        expect(area).to_contain_text('手动备份不参与自动轮转')
                        c.check('maintenance_short_local_time',not re.search(r'\d{4}-\d{2}-\d{2}T\d{2}:',area.inner_text()) and bool(re.search(r'\d{1,2}/\d{1,2} \d{2}:\d{2}',area.inner_text())),True)
                        if case=='PM-A2-01':c.check('selfcheck_visible_on_page','恢复自检通过' in area.inner_text(),True)
                        if case=='PM-A4-01':c.check('all_statuses_and_times_visible',all(t in area.inner_text() for t in ['巡航 · ✕ 异常','扫描','备份','模型连通','最后成功：']),True)
                        capture(c,page,1440);page.set_viewport_size({'width':390,'height':900});capture(c,page,390)
                    finally:browser.close()
