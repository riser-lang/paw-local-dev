"""Installed-app PM acceptance. Run on disposable Windows CI runners only.

All records originate in product APIs/scanning; SQL is used only for readback.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from contextlib import contextmanager, closing
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tests.paw_case import Report
from tests.run_e2e_tests import (INSTALL_DIR, clean_and_record, clean_environment, silent_install, start_paw, stop_paw,
    get_db_path, configure_mock_settings, poll_candidates_from_scanner, parse_candidate_types, get_file_sha256)

BASE = 'http://127.0.0.1:8800'


def api(method, path, body=None):
    response = httpx.request(method, BASE + path, json=body, timeout=60)
    response.raise_for_status()
    return response.json()


@contextmanager
def running():
    proc = start_paw()
    if proc is None:
        raise RuntimeError('Installed application did not start')
    try:
        wait_for_startup_cruise()
        yield
    finally:
        stop_paw(proc)


@contextmanager
def clean_environment_on_error():
    """Remove app residue in a finally block before a later case starts."""
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        if failed:
            try:
                clean_environment()
            except Exception as cleanup_error:
                print('Failed-case cleanup raised ' + type(cleanup_error).__name__)


def wait_for_startup_cruise(timeout_sec: int = 30):
    """Wait for the app's first scheduled cruise before seeding test data."""
    deadline = time.monotonic() + timeout_sec
    last = {"status": "unknown", "total_cruises": 0}
    while time.monotonic() < deadline:
        response = httpx.get(BASE + '/v1/local/runtime/status', timeout=5)
        response.raise_for_status()
        last = response.json().get('scheduler') or {}
        if last.get('total_cruises', 0) >= 1 and last.get('status') == 'idle':
            return
        time.sleep(0.2)
    raise TimeoutError(
        'Startup cruise did not reach idle within '
        f'{timeout_sec}s (status={last.get("status")}, total_cruises={last.get("total_cruises", 0)})'
    )


def project():
    return api('POST', '/v1/web/workspace/projects', {'name': '冲刺项目'})['id']


def read_rows(sql, params=()):
    with closing(sqlite3.connect(str(get_db_path()))) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params)]


def formal():
    return read_rows('SELECT id,latest_progress_summary,next_action_summary,stage,status,description FROM project ORDER BY id')


def dossier(pid):
    return api('GET', '/v1/pm/projects/' + pid)


def progress(data):
    return next(s for s in data['sections'] if s['id'] == 'progress')


def accept(cid):
    result = api('POST', '/v1/web/brain/review/candidates/' + cid + '/accept', {})
    if not result['ok'] or result['result']['status'] != 'applied':
        raise RuntimeError('Candidate did not commit')
    return result['result']


def scan_document(case_id, body='冲刺项目：第一阶段完成。第二阶段进入联调。'):
    from docx import Document
    directory = Path(os.environ['TEMP']) / ('PAW_PM_' + case_id)
    directory.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.add_paragraph(body)
    doc.save(directory / '虚构项目进展.docx')
    configure_mock_settings(directory)
    api('POST', '/v1/local/scanner/start', {'directory': str(directory), 'force_restart': True})
    candidates = poll_candidates_from_scanner()
    cid = parse_candidate_types(candidates)[0]
    if not cid:
        raise RuntimeError('扫描完成但没有项目进展提议')
    return accept(cid)


def propose(pid, text, source_id):
    result = api('POST', '/v1/pm/projects/' + pid + '/sections/propose',
                 {'section_id': 'progress', 'text': text, 'source_version_id': source_id})
    return accept(result['candidate_id'])


def cruise():
    for _ in range(15):
        result = api('POST', '/v1/local/runtime/cruise/trigger', {'force_rescan': True, 'analyze_projects': True})
        state = result.get('result', result).get('status')
        if state != 'already_running':
            if state != 'success':
                raise RuntimeError('Cruise failed: ' + str(state))
            return result
        time.sleep(.2)
    raise RuntimeError('Cruise remained busy')


def screenshot(c, pid, width):
    from playwright.sync_api import sync_playwright
    url = BASE + '/app/pm/projects/' + pid
    output = ROOT / 'screenshots' / ('screen_' + c.id.replace('-', '_') + '_' + str(width) + '.png')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            page = browser.new_page(viewport={'width': width, 'height': 900})
            page.goto(url, wait_until='domcontentloaded')
            page.get_by_text('AI 生成 · 尚未成为正式事实', exact=True).first.wait_for()
            text = page.inner_text('body')
            c.check('ai_label_rendered_' + str(width), 'AI 生成' in text, True)
            if width == 390:
                c.check('no_horizontal_overflow', page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), True)
            page.screenshot(path=str(output), full_page=True)
        finally:
            browser.close()
    c.screenshot(output, url)


def run_c02(report, installer):
    cases = ['PM-B1-01','PM-B1-02','PM-B1-03','PM-B1-04','PM-B1-05','PM-B5-01','PM-GOV-01','PM-GOV-02','PM-GOV-03']
    for case_id in cases:
        with report.case(case_id, '项目档案与治理链：' + case_id) as c:
            c.fresh_install(clean_and_record, lambda: silent_install(installer)[2], INSTALL_DIR)
            with running():
                pid = project()
                if case_id == 'PM-B1-01':
                    expected = json.loads((ROOT / 'backend/pm/dossier_sections.json').read_text(encoding='utf-8'))['sections']
                    actual = [{k: s[k] for k in ('id','title','project_fields')} for s in dossier(pid)['sections']]
                    c.check('sections_match_configuration', actual, expected)
                elif case_id == 'PM-B1-02':
                    path = INSTALL_DIR / '_internal/backend/pm/dossier_sections.json'
                    value = json.loads(path.read_text(encoding='utf-8'))
                    value['sections'][0]['title'] = '可配置的项目现状'
                    path.write_text(json.dumps(value,ensure_ascii=False), encoding='utf-8')
                    stop_paw(None)
                    proc = start_paw()
                    try:
                        c.check('configuration_title_after_restart', dossier(pid)['sections'][0]['title'], '可配置的项目现状')
                    finally:
                        stop_paw(proc)
                elif case_id in ('PM-B1-03','PM-B1-04','PM-B1-05','PM-B5-01'):
                    text = '冲刺项目：第一阶段完成。第二阶段进入联调。'
                    if case_id == 'PM-B5-01':
                        text += '补充试验资料。' * 5000
                    first = scan_document(case_id, text)
                    initial = progress(dossier(pid))['current']
                    c.check('approved_progress_is_current', '本周推进核心模块执行与云端测试验证' in initial['text'], True)
                    source = api('GET', initial['source_url'])
                    c.check('source_version_id_present', bool(initial['source_version_id']), True)
                    c.check('source_opens_original', '第一阶段完成' in source['body'], True)
                    if case_id == 'PM-B1-04':
                        propose(pid, '第二阶段进入联调。', initial['source_version_id'])
                        section = progress(dossier(pid))
                        c.check('one_current_conclusion', section['current']['text'], '第二阶段进入联调。')
                        c.check('old_conclusion_in_history', initial['id'] in [r['id'] for r in section['history']], True)
                        c.check('current_not_in_history', section['current']['id'] in [r['id'] for r in section['history']], False)
                    elif case_id == 'PM-B1-05':
                        counts_before = read_rows('SELECT (SELECT COUNT(*) FROM project) AS projects,(SELECT COUNT(*) FROM todo) AS todos,(SELECT COUNT(*) FROM knowledge_page) AS knowledge')
                        second = propose(pid, '第二阶段进入联调。', initial['source_version_id'])
                        api('POST', '/v1/pm/changes/' + second['approved_change_id'] + '/undo', {})
                        restored = dossier(pid)
                        c.check('previous_conclusion_restored', progress(restored)['current']['id'], initial['id'])
                        c.check('undo_receipt_visible', restored['undo_records'][0]['original_change_id'], second['approved_change_id'])
                        c.check('formal_counts_unchanged', read_rows('SELECT (SELECT COUNT(*) FROM project) AS projects,(SELECT COUNT(*) FROM todo) AS todos,(SELECT COUNT(*) FROM knowledge_page) AS knowledge'), counts_before)
                    elif case_id == 'PM-B5-01':
                        propose(pid, text, initial['source_version_id'])
                        response = httpx.get(BASE + '/v1/pm/projects/' + pid + '/context', timeout=10)
                        response.raise_for_status()
                        budget = json.loads((ROOT / 'backend/pm/dossier_sections.json').read_text(encoding='utf-8'))['context_budget_bytes']
                        c.check('entire_wire_json_within_budget', len(response.content) <= budget, True)
                        bounded = response.json()
                        c.check('stage_summary_replaces_oversize_context', bounded['stage_summaries'][0]['summary_kind'], 'stored_excerpt')
                        raw = api('GET', bounded['stage_summaries'][0]['source_url'])
                        c.check('full_original_retained', text in raw['body'], True)
                else:
                    configure_mock_settings()
                    before = formal()
                    audit_before = {r['id'] for r in read_rows('SELECT id FROM audit_event')}
                    if case_id == 'PM-GOV-03':
                        analyzed = api('POST', '/v1/local/projects/analyze', {'force_rescan': True})
                        c.check('analysis_generated_draft', analyzed['count'], 1)
                    else:
                        cruise()
                    c.check('formal_fields_unchanged_after_cruise', formal(), before)
                    data = dossier(pid)
                    c.check('derived_explainer_generated', len(data['ai_generated']), 1)
                    c.check('derived_is_nonformal', data['ai_generated'][0]['formal_state'], 0)
                    bypass = [r for r in read_rows("SELECT id FROM audit_event WHERE target_type='project' AND change_id IS NULL") if r['id'] not in audit_before]
                    c.check('no_unapproved_project_audit_writes', len(bypass), 0)
                    if case_id == 'PM-GOV-02':
                        screenshot(c, pid, 1440)
                        screenshot(c, pid, 390)
                        draft = data['ai_generated'][0]
                        proposal = api('POST', '/v1/pm/projects/' + pid + '/derived/' + draft['id'] + '/adopt', {})
                        c.check('adoption_waits_for_review', formal(), before)
                        result = accept(proposal['candidate_id'])
                        c.check('adoption_wrote_reviewed_progress', formal()[0]['latest_progress_summary'], draft['content']['current_state'])
                        receipt = read_rows('SELECT a.status,r.decision FROM approved_change a JOIN review_decision r ON r.id=a.approval_ref WHERE a.change_id=?', (result['approved_change_id'],))
                        c.check('adoption_passed_governance', receipt, [{'status':'committed','decision':'approve'}])


def run_governance_negative(report, installer):
    with report.case('NC-PM-GOV', '2.3.9 巡航直接写正式字段的负对照') as c:
        c.fresh_install(clean_and_record, lambda: silent_install(installer)[2], INSTALL_DIR)
        with running():
            project()
            configure_mock_settings()
            before = formal()
            cruise()
            c.check('formal_fields_unchanged_after_cruise', formal(), before)
    from tests.run_pm_search_tests import run_c11_negative
    run_c11_negative(report, installer)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--installer-exe', required=True)
    parser.add_argument('--commit-sha', required=True)
    parser.add_argument('--output-report', required=True)
    parser.add_argument('--version-negative',action='store_true')
    parser.add_argument('--cockpit-negative',action='store_true')
    parser.add_argument('--c12-negative',action='store_true')
    args=parser.parse_args()
    installer=Path(args.installer_exe)
    report=Report('NEGATIVE-CONTROL', args.commit_sha, 'mock-llm', get_file_sha256(installer))
    if args.cockpit_negative:
        from tests.run_pm_frontend_tests import run_c06_negative
        run_c06_negative(report,installer)
    elif args.c12_negative:
        from tests.run_pm_c12_tests import run_c12_negative
        run_c12_negative(report,installer)
    elif args.version_negative:
        from tests.run_pm_versions_tests import run_c03_negative
        run_c03_negative(report,installer)
    else:
        run_governance_negative(report, installer)
    report.save(args.output_report)
    if args.c12_negative:
        expected_checks = {
            'NC-PM-U1': 'current_target_visible',
            'NC-PM-U2': 'source_body_readable',
            'NC-PM-U3': 'no_internal_terms_visible',
            'NC-PM-U4': 'advice_not_refused',
        }
        controls_ok = len(report.cases) == len(expected_checks) and all(
            case.status == 'FAIL' and case.failed_check == expected_checks.get(case.id)
            for case in report.cases
        )
        sys.exit(0 if controls_ok else 1)
    expected=report.cases[0]
    check='cockpit_rendered' if args.cockpit_negative else 'old_version_superseded' if args.version_negative else 'formal_fields_unchanged_after_cruise'
    sys.exit(0 if expected.status == 'FAIL' and expected.failed_check == check else 1)
