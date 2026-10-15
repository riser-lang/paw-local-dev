import threading
import pytest
from backend.pm.source_barrier import wait_until_idle


def test_busy_source_cannot_be_reported_complete():
    condition=threading.Condition()
    active={'v1'}
    with pytest.raises(TimeoutError):
        wait_until_idle(condition,active,'v1',timeout=0)
    active.clear()
    wait_until_idle(condition,active,'v1',timeout=0)


def test_completion_waits_for_owner_notification():
    condition=threading.Condition()
    active={'v1'}
    entered=threading.Event()
    completed=threading.Event()
    def wait():
        entered.set()
        wait_until_idle(condition,active,'v1',timeout=2)
        completed.set()
    worker=threading.Thread(target=wait)
    worker.start()
    assert entered.wait(1)
    assert not completed.wait(.02)
    with condition:
        active.remove('v1')
        condition.notify_all()
    worker.join(2)
    assert completed.is_set()
    assert not worker.is_alive()
