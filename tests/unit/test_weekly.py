import json
from types import SimpleNamespace
from backend.pm import storage, weekly, suggestions
from backend.governance import committer
from tests.unit.test_versions import database, ingest


def section(section, text, sid):
    _, cid = committer.create_direct_user_approved_change(
        'pm_section', 'update',
        {'project_id': 'p', 'section_id': section, 'text': text, 'source_version_id': sid},
        [sid]
    )
    assert committer.commit_approved_change(cid)['result'] == 'success'


def test_project_weekly_report_without_key_and_with_records(database):
    ingest('s1', '方案_v1.docx', '星河项目。原型已完成。验收资料尚未复核。')
    section('progress', '原型已完成', 's1')
    section('risks', '验收资料尚未复核', 's1')

    # Without key
    rep = weekly.build_project_weekly_report('p', model_provider=None, save=True)
    assert rep['project_id'] == 'p'
    assert rep['project_name'] == '星河项目'
    assert any('原型已完成' in p['text'] for p in rep['progress'])
    assert rep['suggestions']['needs_key'] is True
    assert '需要填写 Key' in rep['suggestions']['message']
    assert len(rep['suggestions']['items']) == 0

    # Verify storage record
    with storage.connect() as conn:
        stored = weekly.read_weekly_reports(conn, 'p')
        assert len(stored) >= 1
        assert stored[0]['project_id'] == 'p'


def test_project_weekly_report_with_key_and_suggestions(database):
    ingest('s1', '方案_v1.docx', '星河项目。原型已完成。验收资料尚未复核。')
    section('progress', '原型已完成', 's1')
    section('risks', '验收资料尚未复核', 's1')

    # Record a suggestion directly in pm_suggestion
    with storage.connect() as conn:
        suggestions.migrate(conn)
        basis = [{'source_version_id': 's1', 'title': '方案_v1.docx', 'excerpt': '验收资料尚未复核', 'href': '/s1'}]
        conn.execute(
            '''INSERT INTO pm_suggestion(id, project_id, category, text, basis_json, context_hash, fingerprint, created_at, last_generated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            ('sug-1', 'p', 'gaps', '建议：复核验收资料', json.dumps(basis), 'ctx', 'fp1', '2026-10-16T12:00:00Z', '2026-10-16T12:00:00Z')
        )

    provider = SimpleNamespace(api_key='fake-key')
    rep = weekly.build_project_weekly_report('p', model_provider=provider)
    assert rep['suggestions']['needs_key'] is False
    assert len(rep['suggestions']['items']) >= 1
    assert any('复核验收资料' in s['text'] for s in rep['suggestions']['items'])


def test_cross_project_comparison(database):
    ingest('s1', '方案_v1.docx', '星河项目。原型已完成。')
    section('progress', '原型已完成', 's1')

    cross = weekly.build_cross_project_comparison()
    assert cross['project_count'] >= 1
    matrix = cross['matrix']
    row = next(r for r in matrix if r['project_id'] == 'p')
    assert row['project_name'] == '星河项目'
    assert row['progress_count'] >= 1
