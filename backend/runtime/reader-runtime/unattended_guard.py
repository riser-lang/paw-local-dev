#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CODE_ROOT = Path(__file__).resolve().parent
_BASE_val = os.environ.get("PAW_READER_RUNTIME_ROOT")
if not _BASE_val:
    raise RuntimeError(
        "PAW_READER_RUNTIME_ROOT is required "
        "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
    )
BASE = Path(_BASE_val)
REGISTRY = BASE / 'state' / 'canonical_reader_batches.json'
SEM_STATE = BASE / 'state' / 'semantic_state.json'
SEM_HEARTBEAT = BASE / 'state' / 'semantic_supervisor.heartbeat.json'
SEM_LOCK = BASE / 'state' / 'semantic_supervisor.lock'
SUPERVISOR = _CODE_ROOT / 'semantic_supervisor.py'
GUARD_STATE = BASE / 'state' / 'unattended_guard_state.json'
GUARD_LOCK = BASE / 'state' / 'unattended_guard.lock'
GUARD_LOG = BASE / 'logs' / 'unattended_guard.log'
SUPERVISOR_LOG = BASE / 'logs' / 'semantic_supervisor.unattended.log'
POLICY = BASE / 'state' / 'analysis_orchestration_policy.json'
PATROL_PACKET = BASE / 'state' / 'patrol_analysis_packet.json'

EXPECTED_TOTAL = 710
EXPECTED_BATCHES = 9
POLL_SECONDS = 60
HEARTBEAT_STALE_SECONDS = 180
DISK_LOW_WATERMARK_BYTES = 4 * 1024**3
MAX_RESTARTS_PER_HOUR = 3
PRO_NONBLOCKING_MARKERS = {
    'GEMINI31PRO_HARD_QUOTA_SMOKE_BLOCKER',
    'GEMINI31PRO_HARD_QUOTA',
    'VISUAL_PENDING_QUOTA',
}
STOP = False


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def log(msg: str) -> None:
    line = f'{now_iso()} {msg}\n'
    # PAW v1.2 phase 3 (technical debt H): logging must never be fatal.
    # ``logs`` is a symlink into the live reader data root; when that directory
    # is missing the symlink dangles and ``mkdir(exist_ok=True)`` re-raises,
    # because Path.is_dir() is False for a dangling link.  That made the guard
    # crash-loop instead of supervising.  Fall back to stderr so systemd /
    # journald still records the line.
    try:
        GUARD_LOG.parent.mkdir(parents=True, exist_ok=True)
        with GUARD_LOG.open('a', encoding='utf-8') as f:
            f.write(line)
        return
    except OSError:
        pass
    try:
        sys.stderr.write(line)
        sys.stderr.flush()
    except Exception:
        pass


def read_json(path: Path) -> dict[str, Any]:
    with path.open('r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f'{path} is not a JSON object')
    return data


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def parse_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding='utf-8').strip().strip('"')
        return int(raw)
    except Exception:
        return None


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def registry_check() -> tuple[bool, dict[str, Any]]:
    try:
        reg = read_json(REGISTRY)
        batches = reg.get('batches', {})
        if not isinstance(batches, dict):
            return False, {'reason': 'REGISTRY_BATCHES_INVALID'}
        total = sum(int(v.get('source_count', 0)) for v in batches.values() if isinstance(v, dict))
        all_pass = (
            len(batches) == EXPECTED_BATCHES
            and total == EXPECTED_TOTAL
            and all(isinstance(v, dict) and v.get('canonical') is True and v.get('gate') == 'PASS' for v in batches.values())
        )
        return all_pass, {
            'batch_count': len(batches),
            'source_total': total,
            'all_canonical_gate_pass': all_pass,
        }
    except Exception as exc:
        return False, {'reason': f'REGISTRY_READ_ERROR:{type(exc).__name__}'}


def semantic_snapshot() -> dict[str, Any]:
    try:
        state = read_json(SEM_STATE)
    except Exception as exc:
        return {'status': 'UNKNOWN', 'read_error': type(exc).__name__}
    overall = state.get('overall', {}) if isinstance(state.get('overall'), dict) else {}
    return {
        'status': state.get('status'),
        'updated_at': state.get('updated_at'),
        'supervisor_pid': state.get('supervisor_pid'),
        'current_batch': state.get('current_batch'),
        'production_blocker': state.get('production_blocker'),
        'expected': overall.get('expected'),
        'covered': overall.get('covered'),
        'coverage': overall.get('coverage'),
        'qwen_success': overall.get('qwen_success'),
        'flash_success': overall.get('flash_success'),
        'visual_success': overall.get('visual_success'),
        'visual_pending_quota': overall.get('visual_pending_quota'),
        'needs_review': overall.get('needs_review'),
        'failed_retryable': overall.get('failed_retryable'),
        'qwen': state.get('qwen'),
        'agy': state.get('agy'),
        'visual': state.get('visual'),
        'workers': state.get('workers'),
        'coverage_breakdown': state.get('coverage_breakdown'),
        'batches': state.get('batches'),
    }


def heartbeat_snapshot() -> dict[str, Any]:
    try:
        hb = read_json(SEM_HEARTBEAT)
        at = parse_time(hb.get('at'))
        age = None if at is None else max(0.0, (datetime.now(at.tzinfo) - at).total_seconds())
        return {'exists': True, 'pid': hb.get('pid'), 'at': hb.get('at'), 'phase': hb.get('phase'), 'age_seconds': age}
    except Exception:
        return {'exists': False, 'pid': None, 'at': None, 'phase': None, 'age_seconds': None}


def disk_free() -> int:
    return shutil.disk_usage(BASE).free


def completion_reached(snapshot: dict[str, Any]) -> bool:
    # Flash 710/710 is the basic coverage gate, but the supervisor must stay
    # alive while Qwen deep-read or visual obligations still have work. The
    # explicit all-queues status is therefore authoritative for guard exit.
    return str(snapshot.get('status') or '').upper() in {
        'SEMANTIC_COMPLETE_ALL_QUEUES', 'COMPLETED', 'COMPLETED_PASS', 'COMPLETE'
    }


def current_supervisor_pid(snapshot: dict[str, Any]) -> int | None:
    lock_pid = parse_pid(SEM_LOCK)
    if pid_alive(lock_pid):
        return lock_pid
    try:
        state_pid = int(snapshot.get('supervisor_pid'))
    except Exception:
        state_pid = None
    if pid_alive(state_pid):
        return state_pid
    return None


def restart_history() -> list[str]:
    try:
        st = read_json(GUARD_STATE)
        items = st.get('restart_history', [])
        return [str(x) for x in items if isinstance(x, str)]
    except Exception:
        return []


def pruned_restart_history(items: list[str]) -> list[str]:
    cutoff = time.time() - 3600
    kept: list[str] = []
    for item in items:
        dt = parse_time(item)
        if dt and dt.timestamp() >= cutoff:
            kept.append(item)
    return kept


def safe_remove_stale_semantic_lock() -> None:
    pid = parse_pid(SEM_LOCK)
    if pid_alive(pid):
        return
    try:
        SEM_LOCK.unlink()
        log(f'REMOVED_STALE_SEMANTIC_LOCK pid={pid}')
    except FileNotFoundError:
        pass


def launch_supervisor() -> int:
    safe_remove_stale_semantic_lock()
    SUPERVISOR_LOG.parent.mkdir(parents=True, exist_ok=True)
    out = SUPERVISOR_LOG.open('ab', buffering=0)
    proc = subprocess.Popen(
        [sys.executable, str(SUPERVISOR)],
        cwd=str(BASE),
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
        env={**os.environ, 'PAW_UNATTENDED_GUARD': '1'},
    )
    log(f'SEMANTIC_SUPERVISOR_RESTARTED pid={proc.pid}')
    return proc.pid


def write_patrol_packet(reg_meta: dict[str, Any], sem: dict[str, Any], hb: dict[str, Any], free: int, guard_status: str) -> None:
    packet = {
        'packet_version': '2026-09-02-v1',
        'generated_at': now_iso(),
        'purpose': 'read-only packet for the 30-minute GPT patrol and later project-level synthesis',
        'guard_status': guard_status,
        'reader': reg_meta,
        'semantic': {
            'status': sem.get('status'),
            'current_batch': sem.get('current_batch'),
            'expected': sem.get('expected'),
            'covered': sem.get('covered'),
            'coverage': sem.get('coverage'),
            'qwen_success': sem.get('qwen_success'),
            'flash_success': sem.get('flash_success'),
            'visual_success': sem.get('visual_success'),
            'visual_pending_quota': sem.get('visual_pending_quota'),
            'needs_review': sem.get('needs_review'),
            'failed_retryable': sem.get('failed_retryable'),
            'production_blocker': sem.get('production_blocker'),
            'batches': sem.get('batches'),
        },
        'heartbeat': hb,
        'disk_free_bytes': free,
        'analysis_policy_path': str(POLICY),
        'instructions_for_patrol': [
            'Do not reread all 710 raw sources on every patrol.',
            'Use semantic deltas and evidence-backed synthesis packets; drill down only when a conflict or missing evidence requires it.',
            'Treat Raw Source / SourceVersion / deterministic Reader Evidence as the final evidence arbiter.',
            'Gemini 3.1 Pro quota is visual pending only and must not stop Qwen/Flash semantic coverage.',
            'Never write Formal State directly. Route knowledge/project/data/todo changes through Candidate -> Review -> ApprovedChange -> deterministic Committer.',
            'Never touch TCP 443, VLESS, Xray, WPS raw sources, or unrelated services.',
        ],
    }
    atomic_json(PATROL_PACKET, packet)


def run_once() -> dict[str, Any]:
    ok, reg_meta = registry_check()
    sem = semantic_snapshot()
    hb = heartbeat_snapshot()
    free = disk_free()
    pid = current_supervisor_pid(sem)
    status = 'HEALTHY'
    action = 'NONE'
    blocker = sem.get('production_blocker')
    restart_items = pruned_restart_history(restart_history())

    if not ok:
        status = 'FAIL_CLOSED_READER_REGISTRY'
    elif free < DISK_LOW_WATERMARK_BYTES:
        status = 'PAUSED_DISK_LOW'
    elif completion_reached(sem):
        status = 'SEMANTIC_COMPLETE'
    elif pid:
        age = hb.get('age_seconds')
        if age is None or age <= HEARTBEAT_STALE_SECONDS:
            status = 'HEALTHY_RUNNING'
        else:
            status = 'RUNNING_HEARTBEAT_STALE'
            # Do not kill a live supervisor. A long model call is allowed; the
            # heartbeat thread should recover itself. Escalate only if the PID dies.
    else:
        blocker_text = str(blocker or '')
        blocker_is_visual_only = (not blocker_text) or any(m in blocker_text for m in PRO_NONBLOCKING_MARKERS)
        if not blocker_is_visual_only:
            status = 'FAIL_CLOSED_NONVISUAL_BLOCKER'
        elif len(restart_items) >= MAX_RESTARTS_PER_HOUR:
            status = 'FAIL_CLOSED_RESTART_LIMIT'
        else:
            new_pid = launch_supervisor()
            restart_items.append(now_iso())
            pid = new_pid
            action = 'RESTARTED_SEMANTIC_SUPERVISOR'
            status = 'RECOVERING'

    guard = {
        'guard_version': '2026-09-02-v1',
        'updated_at': now_iso(),
        'pid': os.getpid(),
        'status': status,
        'action': action,
        'semantic_supervisor_pid': pid,
        'reader_registry': reg_meta,
        'semantic': sem,
        'heartbeat': hb,
        'disk_free_bytes': free,
        'disk_low_watermark_bytes': DISK_LOW_WATERMARK_BYTES,
        'restart_history': restart_items,
        'formal_state_write': 'PROHIBITED',
        'network_research': 'PROHIBITED',
        'infra_no_touch': ['TCP_443', 'VLESS', 'XRAY', 'WPS_RAW_SOURCE'],
        'manual_intervention_required': status.startswith('FAIL_CLOSED_'),
    }
    atomic_json(GUARD_STATE, guard)
    write_patrol_packet(reg_meta, sem, hb, free, status)
    return guard


def acquire_guard_lock() -> None:
    if GUARD_LOCK.exists():
        old_pid = parse_pid(GUARD_LOCK)
        if pid_alive(old_pid):
            raise SystemExit(f'guard already running pid={old_pid}')
        try:
            GUARD_LOCK.unlink()
        except OSError:
            pass
    fd = os.open(GUARD_LOCK, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(str(os.getpid()) + '\n')


def cleanup() -> None:
    try:
        if parse_pid(GUARD_LOCK) == os.getpid():
            GUARD_LOCK.unlink()
    except OSError:
        pass


def handle_stop(signum: int, frame: Any) -> None:
    global STOP
    STOP = True


def main() -> int:
    acquire_guard_lock()
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    log(f'GUARD_STARTED pid={os.getpid()} poll={POLL_SECONDS}s')
    try:
        while not STOP:
            try:
                result = run_once()
                log(f"CHECK status={result['status']} action={result['action']} supervisor_pid={result.get('semantic_supervisor_pid')} free={result['disk_free_bytes']}")
            except Exception as exc:
                log(f'GUARD_LOOP_ERROR type={type(exc).__name__} detail={exc}')
                atomic_json(GUARD_STATE, {
                    'guard_version': '2026-09-02-v1',
                    'updated_at': now_iso(),
                    'pid': os.getpid(),
                    'status': 'FAIL_CLOSED_GUARD_EXCEPTION',
                    'error_type': type(exc).__name__,
                    'manual_intervention_required': True,
                    'formal_state_write': 'PROHIBITED',
                    'infra_no_touch': ['TCP_443', 'VLESS', 'XRAY', 'WPS_RAW_SOURCE'],
                })
            for _ in range(POLL_SECONDS):
                if STOP:
                    break
                time.sleep(1)
    finally:
        log(f'GUARD_STOPPED pid={os.getpid()}')
        cleanup()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
