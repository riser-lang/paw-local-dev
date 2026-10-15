#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_BASE_val = os.environ.get("PAW_READER_RUNTIME_ROOT")
if not _BASE_val:
    raise RuntimeError(
        "PAW_READER_RUNTIME_ROOT is required "
        "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
    )
BASE = Path(_BASE_val)
_READER_ROOT_val = os.environ.get("PAW_READER_FIDELITY_ROOT")
if not _READER_ROOT_val:
    raise RuntimeError(
        "PAW_READER_FIDELITY_ROOT is required "
        "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
    )
READER_ROOT = Path(_READER_ROOT_val)
__VENV_ROOT_val = os.environ.get("PAW_KSHEET_VENV_ROOT")
if not __VENV_ROOT_val:
    raise RuntimeError(
        "PAW_KSHEET_VENV_ROOT is required "
        "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
    )
_VENV_ROOT = Path(__VENV_ROOT_val)
PYTHON = _VENV_ROOT / 'bin' / 'python'
ORDER = [
    '00_core_mentor',
    '01_core_intern',
    '02_core_bootcamp',
    '03_core_branch_training',
    '04_core_talent_review',
    '10_associated_sales_competition',
    '20_cross_project_shared',
    '30_shared_reference',
    '90_reference_other',
]
STATE_PATH = BASE / 'state' / 'reader_global_state.json'
EVENTS = BASE / 'state' / 'reader_events.jsonl'


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    except Exception:
        try: os.unlink(name)
        except OSError: pass
        raise


def event(kind: str, **data: object) -> None:
    EVENTS.parent.mkdir(parents=True, exist_ok=True)
    row = {'at': now(), 'event': kind, **data}
    with EVENTS.open('a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')
        f.flush(); os.fsync(f.fileno())


def load_state() -> dict:
    if STATE_PATH.is_file():
        return json.loads(STATE_PATH.read_text(encoding='utf-8'))
    return {'status':'PREPARED','required_total':710,'completed_batches':[],'current_batch':None,'failed_batches':[]}


def save_state(state: dict) -> None:
    state['updated_at'] = now()
    atomic_json(STATE_PATH, state)


def run_cmd(cmd: list[str], log_path: Path, env: dict[str, str] | None = None) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8') as log:
        log.write(f"\n===== {now()} CMD {' '.join(cmd)} =====\n")
        log.flush(); os.fsync(log.fileno())
        proc = subprocess.run(cmd, cwd=str(READER_ROOT), env=env, stdout=log, stderr=subprocess.STDOUT, text=True, check=False)
        log.write(f"===== {now()} RC {proc.returncode} =====\n")
        log.flush(); os.fsync(log.fileno())
        return proc.returncode


def main() -> int:
    lock = BASE / 'state' / 'reader_runner.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, str(os.getpid()).encode()); os.close(fd)
    except FileExistsError:
        print('reader runner lock exists; refusing duplicate run', file=sys.stderr)
        return 3

    state = load_state()
    state.update({'status':'RUNNING','started_at':state.get('started_at') or now(),'runner_pid':os.getpid(),'global_timeout':'NONE'})
    save_state(state); event('RUN_STARTED', pid=os.getpid(), order=ORDER)
    env = os.environ.copy(); env['PYTHONPATH'] = str(READER_ROOT)
    had_gate_fail = False
    try:
        for batch in ORDER:
            out = BASE / 'reader' / batch
            sv = BASE / 'manifests' / f'{batch}_source_versions.json'
            am = BASE / 'manifests' / f'{batch}_artifact_manifest.json'
            ledger = out / 'reader_artifact_ledger.json'
            gate_path = out / 'reader_fidelity_gate.json'

            state['current_batch'] = batch; save_state(state); event('BATCH_STARTED', batch=batch)
            cmd = [str(PYTHON), '-m', 'paw_reader_fidelity.materializer', '--source-version', str(sv), '--artifact-manifest', str(am), '--output', str(out)]
            rc = run_cmd(cmd, BASE / 'logs' / f'{batch}.log', env)
            if rc != 0:
                state['status'] = 'STOPPED_MATERIALIZER_ERROR'
                state.setdefault('failed_batches', []).append({'batch':batch,'stage':'materializer','returncode':rc,'at':now()})
                save_state(state); event('BATCH_MATERIALIZER_FAILED', batch=batch, returncode=rc)
                return rc

            gate_cmd = [str(PYTHON), str(BASE / 'evaluate_reader_batch.py'), '--ledger', str(ledger), '--source-versions', str(sv), '--output', str(gate_path)]
            grc = run_cmd(gate_cmd, BASE / 'logs' / f'{batch}.log', env)
            gate = json.loads(gate_path.read_text(encoding='utf-8')) if gate_path.is_file() else {}
            summary = {
                'batch':batch,
                'gate':gate.get('READER_FIDELITY_GATE','UNKNOWN'),
                'expected':gate.get('EXPECTED_SOURCE_VERSIONS'),
                'materialized':gate.get('MATERIALIZED_RESULTS'),
                'blockers':len(gate.get('BLOCKERS',[])),
                'ledger_summary':gate.get('LEDGER_SUMMARY',{}),
                'finished_at':now(),
            }
            done = [x for x in state.setdefault('completed_batches', []) if x.get('batch') != batch]
            done.append(summary); state['completed_batches'] = done
            if grc != 0:
                had_gate_fail = True
                state.setdefault('failed_batches', []).append({'batch':batch,'stage':'gate','returncode':grc,'blockers':summary['blockers'],'at':now()})
                event('BATCH_GATE_FAIL', **summary)
            else:
                event('BATCH_GATE_PASS', **summary)
            save_state(state)

        state['current_batch'] = None
        state['status'] = 'COMPLETED_WITH_GATE_BLOCKERS' if had_gate_fail else 'COMPLETED_PASS'
        state['finished_at'] = now(); save_state(state)
        event('RUN_FINISHED', status=state['status'])
        return 2 if had_gate_fail else 0
    finally:
        try: lock.unlink()
        except OSError: pass


if __name__ == '__main__':
    raise SystemExit(main())
