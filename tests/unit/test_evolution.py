from backend.pm import storage, evolution
from tests.unit.test_versions import database


def test_evolution_metrics_calculation(database):
    with storage.connect() as conn:
        metrics = evolution.calculate_current_metrics(conn)
        assert len(metrics) == 6
        expected = {
            'pending_decisions', 'coverage_rate', 'conflicts',
            'knowledge_gaps', 'suggestion_adoption_rate', 'backlog'
        }
        assert set(metrics.keys()) == expected


def test_evolution_snapshots_and_trends(database):
    # Snapshot today
    s1 = evolution.record_snapshot(period='2026-W41')
    assert len(s1) == 6

    # Snapshot next week
    s2 = evolution.record_snapshot(period='2026-W42')
    assert len(s2) == 6

    trends = evolution.read_trends()
    assert len(trends) == 6
    for m in trends:
        assert 'trend' in m
        assert len(m['trend']) >= 2
        periods = [t['period'] for t in m['trend']]
        assert '2026-W41' in periods
        assert '2026-W42' in periods
