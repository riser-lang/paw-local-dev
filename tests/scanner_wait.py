"""Wait for a terminal scan barrier, never for an assumed proposal count."""
import time


def wait_for_scan(read_candidates, read_progress, timeout_sec=40, sleep=time.sleep):
    state = 'unknown'
    for _ in range(timeout_sec):
        sleep(1)
        progress = read_progress()
        state = progress.get('status', 'unknown')
        if state in ('error', 'failed', 'cancelled'):
            raise RuntimeError('Scanner did not complete: ' + state)
        if state == 'completed' and progress.get('pipeline_complete', True):
            # Read AFTER the completion barrier, including the last committed row.
            return read_candidates()
    raise TimeoutError(f'Scanner did not reach completed within {timeout_sec}s (last state={state})')
