import pytest
from tests.scanner_wait import wait_for_scan


def test_three_candidates_are_not_completion():
    # A deterministic reproduction of the old race: the fourth candidate is
    # committed only with completion. The old len >= 3 barrier returned early.
    states = iter(['phase2_analyzing', 'phase2_analyzing', 'completed'])
    rows = ['todo', 'knowledge', 'flat_project']
    sleeps = []
    def progress():
        state = next(states)
        if state == 'completed':
            rows.append('empty_project')
        return {'status': state}
    result = wait_for_scan(lambda: rows.copy(), progress, sleep=sleeps.append)
    assert result == ['todo', 'knowledge', 'flat_project', 'empty_project']
    assert sleeps == [1, 1, 1]


def test_completed_scan_does_not_hide_missing_candidates():
    result = wait_for_scan(lambda: ['todo'], lambda: {'status': 'completed'}, sleep=lambda _: None)
    assert result == ['todo']


def test_unfinished_scan_fails_instead_of_returning_partial_data():
    with pytest.raises(TimeoutError, match='phase2_analyzing'):
        wait_for_scan(lambda: ['todo'] * 4, lambda: {'status': 'phase2_analyzing'}, timeout_sec=2, sleep=lambda _: None)
