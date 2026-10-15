#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Reader runtime root comes from the explicit PAW Runtime
# Path Contract; no development-tree fallback.
_BASE_val = os.environ.get("PAW_READER_RUNTIME_ROOT")
if not _BASE_val:
    raise RuntimeError(
        "PAW_READER_RUNTIME_ROOT is required "
        "(PAW Runtime Path Contract, /etc/paw-runtime-paths.env)"
    )
BASE = Path(_BASE_val)
STATE_PATH = BASE / 'state' / 'semantic_state.json'
EVENTS_PATH = BASE / 'state' / 'semantic_events.jsonl'
LOCK_PATH = BASE / 'state' / 'semantic_supervisor.lock'
HEARTBEAT_PATH = BASE / 'state' / 'semantic_supervisor.heartbeat.json'
WORKERS_STATE_PATH = BASE / 'state' / 'semantic_workers_state.json'
READER_REGISTRY_PATH = BASE / 'state' / 'canonical_reader_batches.json'
SCHEMA_PATH = BASE / 'semantic_schema.json'
QODER = '/root/.local/bin/qoderclicn'
AGY = '/usr/local/bin/agy'
QWEN_MODEL = 'Qwen3.8-Max'
AGY_MODEL = 'gemini-3.7-flash-high'
VISUAL_MODEL = 'gemini-3.1-pro-high'
QWEN_SOFT_CREDIT_CAP = 2800.0
MODEL_CALL_TIMEOUT_SECONDS = 1800
POLL_SECONDS = 30
AGY_QUOTA_COOLDOWN_SECONDS = 3600
SHARD_THRESHOLD_BYTES = 2 * 1024 * 1024
WORKER_POLL_SECONDS = 2
WORKER_COOLDOWN_SECONDS = 15
WORKER_HEARTBEAT_SECONDS = 15
WORKER_NAMES = ('flash', 'qwen', 'visual')

# State writes are intentionally serialized. Model calls themselves are not
# serialized: each worker owns one model lane and can be in-flight at once.
STATE_MUTEX = threading.RLock()
ACTIVE_MODEL_SOURCES: dict[str, set[str]] = {name: set() for name in WORKER_NAMES}

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
CORE_BATCHES = {
    '00_core_mentor',
    '01_core_intern',
    '02_core_bootcamp',
    '03_core_branch_training',
    '04_core_talent_review',
    '10_associated_sales_competition',
}
CORE_PROJECT_NAMES = ['运营导师体系', '运营实习生', '星火计划新兵营', '兵种培训', '干部管理人才盘点', '销售顾问技能大赛']
VISUAL_KEYWORDS = ('方案', '汇报', '复盘', '手册', '介绍', '制度', '竞聘', '规划', '盘点', '培训', '课程', '大赛', '总结', '报告')
QUOTA_HARD = ('insufficient credit', 'insufficient credits', 'credits exhausted', 'credit exhausted', 'quota exceeded', 'quota reached', 'individual quota', 'daily quota', 'monthly quota', 'usage limit', 'limit reached', '额度', '积分不足', 'credit balance')
RATE_LIMIT = ('rate limit', 'too many requests', '429', 'throttl')
ANALYSIS_SECTIONS = ('facts', 'inferences', 'project_signals', 'timeline', 'metrics', 'decisions_or_plans', 'conflicts', 'risks', 'ambiguities')
ANALYSIS_REQUIRED = (*ANALYSIS_SECTIONS, 'related_projects', 'needs_visual_review', 'visual_review_reason')


class ToolCallIncomplete(ValueError):
    """The CLI returned a tool request instead of a final semantic payload."""


class ModelReportedError(ValueError):
    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def safe_component(value: str) -> str:
    return ''.join(ch if ch.isalnum() or ch in '._-@' else '_' for ch in str(value))[:190] or 'unknown'


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
            if not text.endswith('\n'):
                f.write('\n')
            f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    except Exception:
        try: os.unlink(name)
        except OSError: pass
        raise


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def append_event(kind: str, **data: Any) -> None:
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {'at': now(), 'event': kind, **data}
    with EVENTS_PATH.open('a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')
        f.flush(); os.fsync(f.fileno())


def initial_state() -> dict[str, Any]:
    workers = {}
    for name, model, queue in (
        ('flash', AGY_MODEL, 'flash_coverage'),
        ('qwen', QWEN_MODEL, 'qwen_deep_read'),
        ('visual', VISUAL_MODEL, 'visual_obligations'),
    ):
        workers[name] = {
            'worker': name, 'model': model, 'queue': queue, 'enabled': True,
            'status': 'IDLE', 'cursor': {'batch': None, 'index': 0},
            'checkpoint_path': str(BASE / 'state' / f'semantic_worker_{name}.checkpoint.json'),
            'heartbeat_path': str(BASE / 'state' / f'semantic_worker_{name}.heartbeat.json'),
            'last_source': None, 'last_success': None, 'last_error': None,
            'cooldown_until': None, 'active_source_version_id': None,
            'attempts': 0, 'successes': 0, 'reused_successes': 0,
        }
    return {
        'status': 'WAITING_FOR_READER_GATE',
        'started_at': now(),
        'updated_at': now(),
        'supervisor_pid': os.getpid(),
        'global_timeout': 'NONE',
        'qwen': {
            'model': QWEN_MODEL,
            'enabled': True,
            'credits_consumed_this_run': 0.0,
            'soft_credit_cap': QWEN_SOFT_CREDIT_CAP,
            'pause_reason': None,
            'paused_at': None,
        },
        'agy': {
            'model': AGY_MODEL,
            'enabled': True,
            'cooldown_until': None,
            'pause_reason': None,
        },
        'visual': {'model': VISUAL_MODEL, 'enabled': True, 'cooldown_until': None, 'pause_reason': None, 'pending_quota': 0},
        'workers': workers,
        'batches': {},
        'formal_state_write': 'PROHIBITED',
        'network_research': 'PROHIBITED',
    }


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return initial_state()
    try:
        state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except Exception:
        state = initial_state()
    state['supervisor_pid'] = os.getpid()
    # This marker belonged to the pre-canonical smoke run. It must not leak
    # into production status after the canonical registry has passed all gates.
    if state.get('production_blocker') == 'GEMINI31PRO_HARD_QUOTA_SMOKE_BLOCKER':
        state.pop('production_blocker', None)
    state.setdefault('visual', {'model': VISUAL_MODEL, 'enabled': True, 'cooldown_until': None, 'pause_reason': None, 'pending_quota': 0})
    state['visual'].setdefault('cooldown_until', None)
    state['visual'].setdefault('pause_reason', None)
    state['visual'].setdefault('pending_quota', 0)
    defaults = initial_state()['workers']
    state.setdefault('workers', {})
    for name in WORKER_NAMES:
        current = state['workers'].setdefault(name, {})
        for key, value in defaults[name].items():
            if key not in current:
                current[key] = value.copy() if isinstance(value, dict) else value
    # Keep the new worker view compatible with the legacy model-specific
    # controls. A historical hard quota/soft-cap pause must not be lost on
    # restart, while Flash and Visual remain independently enabled.
    state['workers']['qwen']['enabled'] = bool(state.get('qwen', {}).get('enabled', True))
    state['workers']['flash']['enabled'] = bool(state.get('agy', {}).get('enabled', True))
    state['workers']['visual']['enabled'] = bool(state.get('visual', {}).get('enabled', True))
    if state.get('qwen', {}).get('pause_reason') and not state['workers']['qwen'].get('last_error'):
        state['workers']['qwen']['last_error'] = state['qwen']['pause_reason']
    return state


def save_state(state: dict[str, Any]) -> None:
    state['updated_at'] = now()
    atomic_json(STATE_PATH, state)


def save_worker_state(name: str, **updates: Any) -> None:
    """Atomically update one worker without clobbering sibling worker state."""
    with STATE_MUTEX:
        state = load_state()
        worker = state.setdefault('workers', {}).setdefault(name, {})
        worker.update(updates)
        save_state(state)
        atomic_json(WORKERS_STATE_PATH, state.get('workers', {}))


def worker_checkpoint(name: str, **updates: Any) -> None:
    path = BASE / 'state' / f'semantic_worker_{name}.checkpoint.json'
    current = {'worker': name, 'updated_at': now()}
    if path.is_file():
        try:
            loaded = read_json(path)
            if isinstance(loaded, dict):
                current.update(loaded)
        except (OSError, ValueError, TypeError):
            pass
    current.update(updates)
    current['updated_at'] = now()
    atomic_json(path, current)


def write_worker_heartbeat(name: str, **updates: Any) -> None:
    path = BASE / 'state' / f'semantic_worker_{name}.heartbeat.json'
    payload = {'worker': name, 'pid': os.getpid(), 'at': now(), **updates}
    atomic_json(path, payload)


def result_success(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
        return obj.get('status') == 'SUCCESS' and isinstance(obj.get('analysis'), dict)
    except Exception:
        return False


def parse_jsonish(text: str) -> Any:
    text = (text or '').strip()
    if not text:
        raise ValueError('empty model output')
    if '<tool_call' in text.lower() or '<tool_name>' in text.lower():
        raise ToolCallIncomplete('model returned a tool call without a final payload')
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # qoderclicn --output-format json emits one JSON envelope followed by
    # human-readable stderr markers on some versions. Agy stream mode can
    # likewise contain one JSON event per line. Prefer a complete JSON line
    # before attempting the looser fenced/object extraction below.
    line_candidates: list[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            line_candidates.append(candidate)
    for candidate in reversed(line_candidates):
        if any(key in candidate for key in ('result', 'response', 'structured_output', 'content', 'final')):
            return candidate
    if line_candidates:
        return line_candidates[-1]

    fenced = re.search(r'```(?:json)?\s*(\{.*\}|\[.*\])\s*```', text, flags=re.S | re.I)
    if fenced:
        return json.loads(fenced.group(1))
    start = text.find('{'); end = text.rfind('}')
    if start >= 0 and end > start:
        return json.loads(text[start:end+1])
    raise ValueError('no JSON object found')


def analysis_shape_ok(value: Any) -> bool:
    """Small, dependency-free check matching semantic_schema.json's top level."""
    if not isinstance(value, dict) or any(key not in value for key in ANALYSIS_REQUIRED):
        return False
    if not isinstance(value['summary'], str) or not isinstance(value['visual_review_reason'], str):
        return False
    if not isinstance(value['needs_visual_review'], bool):
        return False
    if any(not isinstance(value[key], list) for key in (*ANALYSIS_SECTIONS, 'related_projects')):
        return False
    for key in ANALYSIS_SECTIONS:
        for item in value[key]:
            if not isinstance(item, dict) or not isinstance(item.get('content'), str):
                return False
            if not isinstance(item.get('evidence_locators'), list) or not item['evidence_locators']:
                return False
            if item.get('confidence') not in {'high', 'medium', 'low'}:
                return False
    for item in value['related_projects']:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ('project', 'relationship')):
            return False
        if item.get('confidence') not in {'high', 'medium', 'low'}:
            return False
    return True


def canonicalize_analysis(value: Any) -> dict[str, Any] | None:
    """Map known model field aliases into the PAW semantic schema.

    Qwen does not expose a JSON-schema flag and has returned several equivalent
    field names across calls. This adapter preserves evidence locators while
    keeping the persisted analysis shape deterministic.
    """
    if not isinstance(value, dict) or not isinstance(value.get('summary'), str):
        return None

    aliases = {
        'project_signals': ('project_signals', 'project_evolution_signals'),
        'timeline': ('timeline',),
        'metrics': ('metrics',),
        'decisions_or_plans': ('decisions_or_plans', 'decisions', 'decisions_and_changes'),
        'conflicts': ('conflicts',),
        'risks': ('risks',),
        'ambiguities': ('ambiguities', 'open_items', 'empty_or_absent_sections'),
        'facts': ('facts',),
        'inferences': ('inferences',),
    }

    def locators(item: Any) -> list[Any]:
        return item.get('evidence_locators', []) if isinstance(item, dict) else []

    def text_for(item: Any, section: str) -> str:
        if not isinstance(item, dict):
            return str(item)
        for key in ('content', 'statement', 'description', 'summary', 'note', 'item', 'risk', 'decision', 'event'):
            if isinstance(item.get(key), str) and item[key].strip():
                return item[key]
        if section == 'metrics':
            parts = [str(item.get(k)) for k in ('name', 'value', 'value_in_document', 'unit') if item.get(k) is not None]
            return '：'.join(parts)
        return ''

    result: dict[str, Any] = {key: [] for key in ANALYSIS_SECTIONS}
    result['related_projects'] = []
    result['summary'] = value['summary']
    for section, names in aliases.items():
        items: list[Any] = []
        for name in names:
            candidate = value.get(name)
            if isinstance(candidate, list):
                items.extend(candidate)
        for item in items:
            content = text_for(item, section)
            if not content:
                continue
            confidence = item.get('confidence', 'medium') if isinstance(item, dict) else 'medium'
            result[section].append({
                'content': content,
                'evidence_locators': locators(item),
                'confidence': confidence if confidence in {'high', 'medium', 'low'} else 'medium',
            })

    related: list[Any] = value.get('related_projects', []) if isinstance(value.get('related_projects'), list) else []
    for item in related:
        if not isinstance(item, dict):
            continue
        project = item.get('project') or item.get('project_name') or item.get('signal_project') or item.get('signal')
        relationship = item.get('relationship') or item.get('basis') or ''
        if project is None:
            continue
        confidence = item.get('confidence', 'medium')
        result['related_projects'].append({
            'project': str(project), 'relationship': str(relationship),
            'confidence': confidence if confidence in {'high', 'medium', 'low'} else 'medium',
            'evidence_locators': locators(item),
        })
    result['needs_visual_review'] = bool(value.get('needs_visual_review', False))
    reason = value.get('visual_review_reason') or value.get('needs_visual_review_reason') or value.get('visual_review_reasons') or ''
    result['visual_review_reason'] = '; '.join(str(x) for x in reason) if isinstance(reason, list) else str(reason)
    return result if analysis_shape_ok(result) else None


def extract_analysis(wrapper: Any) -> dict[str, Any]:
    if isinstance(wrapper, dict):
        structured = wrapper.get('structured_output')
        if analysis_shape_ok(structured):
            return structured
        if wrapper.get('is_error') is True or str(wrapper.get('status', '')).upper() in {'ERROR', 'FAILED'}:
            detail = str(wrapper.get('result') or wrapper.get('response') or wrapper.get('error') or wrapper.get('status'))
            raise ModelReportedError(quota_kind(detail) or 'MODEL_ERROR', detail)
        for key in ('result', 'response', 'content', 'final', 'text'):
            value = wrapper.get(key)
            if isinstance(value, str) and value.strip():
                try:
                    parsed = parse_jsonish(value)
                    adapted = canonicalize_analysis(parsed)
                    if adapted is not None:
                        return adapted
                except ToolCallIncomplete:
                    raise
                except Exception:
                    continue
        adapted = canonicalize_analysis(wrapper)
        if adapted is not None:
            return adapted
    if isinstance(wrapper, str):
        parsed = parse_jsonish(wrapper)
        adapted = canonicalize_analysis(parsed)
        if adapted is not None:
            return adapted
    raise ValueError('structured semantic payload not found')


def parse_credits(wrapper: Any) -> float:
    if not isinstance(wrapper, dict):
        return 0.0
    for key in ('total_credits', 'credits'):
        value = wrapper.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    usage = wrapper.get('modelUsage')
    if isinstance(usage, dict):
        total = 0.0
        for entry in usage.values():
            if isinstance(entry, dict) and isinstance(entry.get('credits'), (int, float)):
                total += float(entry['credits'])
        return total
    return 0.0


def quota_kind(text: str) -> str | None:
    low = (text or '').lower()
    if any(x.lower() in low for x in QUOTA_HARD):
        return 'HARD_QUOTA'
    if any(x.lower() in low for x in RATE_LIMIT):
        return 'RATE_LIMIT'
    return None


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def reader_registry() -> dict[str, Any]:
    if not READER_REGISTRY_PATH.is_file():
        return {}
    try:
        value = read_json(READER_REGISTRY_PATH)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def reader_spec(batch: str) -> dict[str, Any]:
    value = reader_registry().get('batches', {}).get(batch, {})
    return value if isinstance(value, dict) else {}


def file_sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def metadata_for_batch(batch: str) -> dict[str, dict[str, Any]]:
    p = BASE / 'manifests' / f'{batch}_artifact_manifest.json'
    data = read_json(p)
    result = {}
    for row in data.get('artifacts', []):
        result[str(row.get('source_version_id'))] = row
    return result


def reader_gate_pass(batch: str) -> bool:
    spec = reader_spec(batch)
    p = Path(str(spec.get('gate_path', '')))
    if not spec or spec.get('gate') != 'PASS' or not p.is_file():
        return False
    if not p.is_file():
        return False
    try:
        return file_sha256(p) == spec.get('gate_sha256') and read_json(p).get('READER_FIDELITY_GATE') == 'PASS'
    except Exception:
        return False


def reader_gate_failed(batch: str) -> bool:
    # FAIL is never a canonical semantic input.  Keeping this false prevents
    # an old FAIL directory from being interpreted as a production route.
    return False


def ledger_entries(batch: str) -> list[dict[str, Any]]:
    spec = reader_spec(batch)
    p = Path(str(spec.get('ledger_path', '')))
    if not spec or not p.is_file():
        return []
    data = read_json(p)
    entries = [x for x in data.get('entries', []) if x.get('status') == 'COMPLETED' and x.get('result_path')]
    return sorted(entries, key=lambda x: str(x.get('source_lineage', {}).get('source_version_id', '')))


def authoritative_meta(batch: str, entry: dict[str, Any], manifest: dict[str, dict[str, Any]]) -> dict[str, Any]:
    lineage = entry.get('source_lineage', {})
    svid = str(lineage.get('source_version_id', ''))
    m = manifest.get(svid, {})
    return {
        'batch': batch,
        'source_id': str(lineage.get('source_id') or m.get('source_id') or ''),
        'source_version_id': svid,
        'stable_object_id': str(lineage.get('stable_object_id') or m.get('stable_object_id') or ''),
        'version_marker': str(lineage.get('version_marker') or m.get('version_marker') or ''),
        'title': str(m.get('title') or m.get('display_name') or entry.get('artifact', {}).get('display_name') or ''),
        'inventory_path': str(m.get('inventory_path') or ''),
        'family': str(m.get('family') or entry.get('artifact', {}).get('family') or ''),
        'project_tier': str(m.get('project_tier') or ('CORE' if batch in CORE_BATCHES else 'REFERENCE')),
        'paw_project_name': str(m.get('paw_project_name') or ''),
        'paw_project_id': m.get('paw_project_id'),
        'raw_sha256': str(m.get('sha256') or entry.get('artifact', {}).get('sha256') or ''),
        'reader_result_path': str(entry.get('result_path')),
    }


def base_prompt(meta: dict[str, Any], *, model_role: str, reference_mode: bool = False) -> str:
    project_mode = (
        '这是 PAW 明确核心项目，必须细读并保留项目演进、规则、数据、时间线、冲突与证据。'
        if not reference_mode else
        '这是非核心/关联资料，做完整证据保留但语义分析以关联扫描为主；发现与六个核心项目存在规则、数据、时间线或方法论关系时要明确指出。'
    )
    return f'''你正在执行 PAW Source First 的本地分析平面任务。只分析随任务提供的 ReaderResult，不使用网页、不搜索互联网、不调用外部知识，不修改任何文件、WPS、PAW Formal State、Knowledge、Project、Data、Todo、Candidate。\n\n模型职责：{model_role}\n{project_mode}\n\n权威 Source 元数据由确定性控制器提供，禁止模型自行推断或改写：\nsource_id={meta['source_id']}\nsource_version_id={meta['source_version_id']}\nraw_sha256={meta['raw_sha256']}\ntitle={meta['title']}\ninventory_path={meta['inventory_path']}\nproject_tier={meta['project_tier']}\npaw_project_name={meta['paw_project_name']}\n六个核心项目={','.join(CORE_PROJECT_NAMES)}\n\n规则：\n1. ReaderResult 是当前模型唯一事实输入；Raw Source/SourceVersion/Parser Evidence 的证据优先级高于你的推理。\n2. facts 只写 Reader Evidence 明确支持的事实；inferences 必须与 facts 分开，不得把推断改写成事实。\n3. 所有事实、指标、决定、冲突、风险必须给 evidence_locators 数组，尽量使用 ReaderResult 中真实路径，例如 text_blocks[3]、tables[1]、structured_semantics.slides[2]、relationships[4]、visual_pages[0]。\n4. 任何数字、比例、人数、金额、日期、指标值只能在 ReaderResult 中存在可定位证据时写入；否则放入 ambiguities，不得补算、猜测或沿用历史聊天数字。\n5. 不要把文件夹机械等同于正式 Project；只输出 related_projects 关系信号。一个 Source 可以关联多个项目。\n6. 不丢弃历史信息、空章节、版本冲突、异常值、口径差异和未决事项。\n7. summary 可以浓缩，但不能代替 facts/evidence。\n8. 如 PPT/PDF/图表/流程图的颜色、箭头、连接、分组、布局可能改变业务语义，needs_visual_review=true，并写明原因。\n9. 输出只允许为符合结构化 schema 的 JSON，不要 Markdown，不要解释你的过程。'''


def validate_locators(analysis: dict[str, Any], reader: dict[str, Any]) -> dict[str, Any]:
    invalid: list[str] = []
    def exists(locator: str) -> bool:
        token_re = re.compile(r'(?:^|\.)([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]')
        pos = 0
        value: Any = reader
        matches = list(token_re.finditer(locator))
        if not matches or matches[0].start() != 0:
            return False
        for match in matches:
            if match.start() != pos:
                return False
            key, index = match.groups()
            try:
                if key is not None:
                    if not isinstance(value, dict) or key not in value:
                        return False
                    value = value[key]
                else:
                    if not isinstance(value, list) or int(index) >= len(value):
                        return False
                    value = value[int(index)]
            except (KeyError, IndexError, TypeError, ValueError):
                return False
            pos = match.end()
        return pos == len(locator)

    for sec in ANALYSIS_SECTIONS:
        for item in analysis.get(sec, []) if isinstance(analysis.get(sec), list) else []:
            locs = item.get('evidence_locators', []) if isinstance(item, dict) else []
            if not locs:
                invalid.append(f'{sec}:missing_locator')
                continue
            for loc in locs:
                if not isinstance(loc, str):
                    invalid.append(f'{sec}:non_string_locator'); continue
                if not exists(loc):
                    invalid.append(loc)
    return {'status': 'PASS' if not invalid else 'NEEDS_REVIEW', 'invalid_locators': invalid[:100], 'invalid_count': len(invalid)}


def normalize(meta: dict[str, Any], model: str, raw_wrapper: Any, analysis: dict[str, Any], reader: dict[str, Any], credits: float = 0.0) -> dict[str, Any]:
    # Controller-owned lineage always wins over model-provided lineage.
    analysis = dict(analysis)
    for forbidden in ('source_id', 'source_version', 'source_version_id', 'raw_sha256'):
        analysis.pop(forbidden, None)
    return {
        'status': 'SUCCESS',
        'result_success': True,
        'model': model,
        'completed_at': now(),
        'authoritative_lineage': {
            'source_id': meta['source_id'],
            'source_version_id': meta['source_version_id'],
            'stable_object_id': meta['stable_object_id'],
            'version_marker': meta['version_marker'],
            'raw_sha256': meta['raw_sha256'],
            'reader_result_path': meta['reader_result_path'],
        },
        'routing': {
            'batch': meta['batch'], 'project_tier': meta['project_tier'],
            'paw_project_name': meta['paw_project_name'], 'paw_project_id': meta['paw_project_id'],
            'inventory_path': meta['inventory_path'], 'title': meta['title'], 'family': meta['family'],
        },
        'credits': credits,
        'evidence_validation': validate_locators(analysis, reader),
        'analysis': analysis,
    }


def failure_payload(meta: dict[str, Any], model: str, reason: str, detail: str, returncode: int | None = None) -> dict[str, Any]:
    return {
        'status': 'FAILED', 'result_success': False, 'model': model, 'failed_at': now(), 'reason': reason,
        'returncode': returncode, 'detail': detail[-12000:],
        'authoritative_lineage': {'source_id': meta['source_id'], 'source_version_id': meta['source_version_id'], 'raw_sha256': meta['raw_sha256'], 'reader_result_path': meta['reader_result_path']},
        'routing': {'batch':meta['batch'],'project_tier':meta['project_tier'],'paw_project_name':meta['paw_project_name'],'inventory_path':meta['inventory_path'],'title':meta['title'],'family':meta['family']},
    }


def semantic_paths(model_dir: str, meta: dict[str, Any]) -> tuple[Path, Path]:
    stem = safe_component(meta['source_version_id'])
    root = Path(os.environ.get('PAW_SEMANTIC_OUTPUT_ROOT', str(BASE / 'semantic')))
    directory = root / model_dir / meta['batch']
    return directory / f'{stem}.json', directory / f'{stem}.raw.txt'


def write_result(path: Path, payload: dict[str, Any]) -> bool:
    """Write one source result, never replacing an already successful result."""
    if result_success(path):
        return False
    atomic_json(path, payload)
    return True


def save_model_streams(raw_path: Path, stdout: str, stderr: str, attempt: int) -> None:
    stem = raw_path.name[:-len('.raw.txt')] if raw_path.name.endswith('.raw.txt') else raw_path.stem
    suffix = '' if attempt == 1 else f'.attempt{attempt}'
    atomic_text(raw_path.with_name(stem + suffix + '.raw.txt'), stdout or '')
    atomic_text(raw_path.with_name(stem + suffix + '.stderr.txt'), stderr or '')


def classify_model_failure(stdout: str, stderr: str, returncode: int | None = None) -> str:
    return quota_kind((stdout or '') + '\n' + (stderr or '')) or 'MODEL_ERROR'


def qoder_command(reader_path: Path, prompt: str) -> list[str]:
    # Read is the only built-in tool. No shell, edit, write, web, or fetch tool is enabled.
    return [
        QODER, '--model', QWEN_MODEL, '--reasoning-effort', 'high',
        '--permission-mode', 'dont_ask', '--cwd', str(reader_path.parent),
        '--attachment', str(reader_path),
        '--output-format', 'json', '--max-output-tokens', '12000',
        '--max-model-request-retries', '1', '--tools', 'Read',
        '--allowed-tools', 'Read', '--disallowed-tools', 'WebSearch',
        '--disallowed-tools', 'WebFetch', '--no-session-persistence',
        '--print', prompt,
    ]


def agy_stream_command(model: str, log_path: Path) -> list[str]:
    return [
        AGY, '--model', model, '--effort', 'high', '--mode', 'plan',
        '--output-format', 'stream-json', '--input-format', 'stream-json',
        '--json-schema', str(SCHEMA_PATH), '--print-timeout', '30m',
        '--disable-slash-commands', '--sandbox', '--log-file', str(log_path),
        '--print', '',
    ]


def extract_agy_stream_result(stdout: str) -> Any:
    final: Any = None
    for line in (stdout or '').splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get('event') == 'result':
            final = event.get('result')
        elif event.get('status') in {'SUCCESS', 'ERROR', 'FAILED'}:
            # Current agy --print + stream-input versions sometimes emit the
            # final response directly instead of wrapping it in event=result.
            final = event
    if final is None:
        # Keep this fallback useful for mocked callers and future CLI output
        # changes while still rejecting non-JSON noise.
        final = parse_jsonish(stdout)
    return final


def write_failure(path: Path, payload: dict[str, Any]) -> bool:
    return write_result(path, payload)


def _json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def _chunk_items(items: list[Any], limit: int) -> list[tuple[list[Any], list[int]]]:
    chunks: list[tuple[list[Any], list[int]]] = []
    current: list[Any] = []
    indexes: list[int] = []
    for index, item in enumerate(items):
        candidate = current + [item]
        if current and _json_bytes(candidate) > limit:
            chunks.append((current, indexes))
            current, indexes = [], []
        current.append(item); indexes.append(index)
    if current:
        chunks.append((current, indexes))
    return chunks


def shard_reader_result(reader: dict[str, Any], meta: dict[str, Any], threshold_bytes: int = SHARD_THRESHOLD_BYTES) -> list[dict[str, Any]]:
    """Split ReaderResult by deterministic evidence containers, never by a model summary.

    Every shard is a lossless projection of one or more original ReaderResult
    ranges. The original ReaderResult is never rewritten.
    """
    if _json_bytes(reader) <= threshold_bytes:
        return [{
            **reader,
            'source_id': meta['source_id'],
            'source_version_id': meta['source_version_id'],
            'raw_sha256': meta['raw_sha256'],
            'shard_context': {
                'shard_id': 'shard-0001', 'source_id': meta['source_id'],
                'source_version_id': meta['source_version_id'], 'raw_sha256': meta['raw_sha256'],
                'locator_range': {'kind': 'full_reader_result'}, 'locator_map': {},
            },
        }]

    common = {k: v for k, v in reader.items() if k not in {'structured_semantics', 'text_blocks', 'tables', 'visual_pages', 'relationships', 'embedded_objects', 'derived_artifacts', 'warnings'}}
    units: list[tuple[str, str, list[Any]]] = []
    structured = reader.get('structured_semantics') if isinstance(reader.get('structured_semantics'), dict) else {}
    preferred = ('sheets', 'slides', 'pages', 'structured_semantics', 'named_ranges', 'drawing_parts', 'chart_parts')
    for key in preferred:
        value = structured.get(key) if key != 'structured_semantics' else None
        if isinstance(value, list) and value:
            units.append(('structured_semantics', key, value))
    for key in ('text_blocks', 'tables', 'visual_pages', 'relationships', 'embedded_objects', 'derived_artifacts', 'warnings'):
        value = reader.get(key)
        if isinstance(value, list) and value:
            units.append(('', key, value))

    if not units:
        return [{**reader, 'source_id': meta['source_id'], 'source_version_id': meta['source_version_id'], 'raw_sha256': meta['raw_sha256'], 'shard_context': {'shard_id': 'shard-0001', 'locator_range': {'kind': 'full_reader_result'}, 'locator_map': {}}}]

    shards: list[dict[str, Any]] = []
    for container, key, items in units:
        # Keep the container's non-list structural metadata in every shard.
        if container:
            shared = {k: v for k, v in structured.items() if k != key and not isinstance(v, list)}
            base = dict(common); base['structured_semantics'] = {**shared, key: []}
            original_prefix = f'structured_semantics.{key}'
        else:
            base = dict(common); base[key] = []
            original_prefix = key
        for chunk, indexes in _chunk_items(items, max(256 * 1024, threshold_bytes - _json_bytes(base) - 8192)):
            local_items = chunk
            # If one structured unit is itself oversized, split its largest
            # list field. This preserves the exact raw item fields across
            # shards and records the nested locator range.
            if len(local_items) == 1 and _json_bytes(local_items[0]) > threshold_bytes:
                item = local_items[0]
                list_key = max((k for k, v in item.items() if isinstance(v, list)), key=lambda k: _json_bytes(item[k]), default=None) if isinstance(item, dict) else None
                if list_key:
                    nested = item[list_key]
                    for nested_chunk, nested_indexes in _chunk_items(nested, max(256 * 1024, threshold_bytes - 32768)):
                        copy_item = dict(item); copy_item[list_key] = nested_chunk
                        original_index = indexes[0]
                        shard = dict(base)
                        if container: shard['structured_semantics'] = {**shard['structured_semantics'], key: [copy_item]}
                        else: shard[key] = [copy_item]
                        mapping = {f'{original_prefix}[0].{list_key}[{i}]': f'{original_prefix}[{original_index}].{list_key}[{j}]' for i, j in enumerate(nested_indexes)}
                        shards.append((shard, {'kind': 'nested', 'path': original_prefix, 'index': original_index, 'field': list_key, 'indexes': nested_indexes}, mapping))
                    continue
            shard = dict(base)
            if container: shard['structured_semantics'] = {**shard['structured_semantics'], key: local_items}
            else: shard[key] = local_items
            mapping = {f'{original_prefix}[{i}]': f'{original_prefix}[{j}]' for i, j in enumerate(indexes)}
            shards.append((shard, {'kind': 'range', 'path': original_prefix, 'indexes': indexes, 'start': min(indexes), 'end': max(indexes)}, mapping))

    result: list[dict[str, Any]] = []
    for number, (shard, locator_range, mapping) in enumerate(shards, 1):
        shard_id = f'shard-{number:04d}'
        shard['source_id'] = meta['source_id']; shard['source_version_id'] = meta['source_version_id']; shard['raw_sha256'] = meta['raw_sha256']
        shard['shard_context'] = {
            'shard_id': shard_id, 'source_id': meta['source_id'],
            'source_version_id': meta['source_version_id'], 'raw_sha256': meta['raw_sha256'],
            'locator_range': locator_range, 'locator_map': mapping,
            'original_reader_bytes': _json_bytes(reader), 'lossless_projection': True,
        }
        result.append(shard)
    return result


def _remap_locator(locator: str, mapping: dict[str, str]) -> str:
    for local, original in sorted(mapping.items(), key=lambda pair: len(pair[0]), reverse=True):
        if locator == local:
            return original
        if locator.startswith(local + '.'):
            return original + locator[len(local):]
    return locator


def merge_shard_analyses(records: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    merged: dict[str, Any] = {section: [] for section in ANALYSIS_SECTIONS}
    summaries: list[str] = []
    seen: set[str] = set()
    needs_visual = False; reasons: list[str] = []
    for shard_info, analysis in records:
        context = shard_info.get('shard_context', {})
        mapping = context.get('locator_map', {}) if isinstance(context.get('locator_map'), dict) else {}
        if isinstance(analysis.get('summary'), str) and analysis['summary'].strip():
            summaries.append(f"[{context.get('shard_id', 'shard')}] {analysis['summary'].strip()}")
        needs_visual = needs_visual or analysis.get('needs_visual_review') is True
        reason = analysis.get('visual_review_reason')
        if reason: reasons.append(str(reason))
        for section in ANALYSIS_SECTIONS:
            values = analysis.get(section) if isinstance(analysis.get(section), list) else []
            for item in values:
                item = dict(item) if isinstance(item, dict) else item
                if isinstance(item, dict) and isinstance(item.get('evidence_locators'), list):
                    item['evidence_locators'] = [_remap_locator(str(x), mapping) for x in item['evidence_locators']]
                marker = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                if marker not in seen:
                    seen.add(marker); merged[section].append(item)
    merged['summary'] = '\n'.join(summaries)
    merged['related_projects'] = []
    # related_projects is not in ANALYSIS_SECTIONS but is required by schema.
    for shard_info, analysis in records:
        for item in analysis.get('related_projects', []) if isinstance(analysis.get('related_projects'), list) else []:
            marker = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if marker not in seen:
                seen.add(marker); merged['related_projects'].append(item)
    merged['needs_visual_review'] = needs_visual
    merged['visual_review_reason'] = '; '.join(dict.fromkeys(reasons))
    return merged


def _run_qwen_once(meta: dict[str, Any], reader_path: Path, reader: dict[str, Any], final_path: Path, raw_path: Path, prompt: str, shard_context: dict[str, Any] | None = None) -> dict[str, Any]:
    if result_success(final_path):
        return {'status':'SKIPPED_EXISTS','path':str(final_path),'credits':0.0}
    started = now()
    last_detail = ''
    for attempt in (1, 2):
        attempt_prompt = prompt if attempt == 1 else prompt + '\n\n上一回合只返回了 Read 工具请求。请不要输出工具调用或解释，直接基于已附加且可读取的 ReaderResult 完成任务，并只输出最终符合 schema 的 JSON。'
        try:
            proc = subprocess.run(qoder_command(reader_path, attempt_prompt), cwd=str(reader_path.parent), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=MODEL_CALL_TIMEOUT_SECONDS, check=False)
            stdout, stderr = proc.stdout or '', proc.stderr or ''
            save_model_streams(raw_path, stdout, stderr, attempt)
            if proc.returncode != 0:
                kind = classify_model_failure(stdout, stderr, proc.returncode)
                payload = failure_payload(meta, QWEN_MODEL, kind, stdout + '\n[STDERR]\n' + stderr, proc.returncode)
                write_failure(final_path, payload)
                return {'status':'FAILED','kind':kind,'path':str(final_path),'credits':0.0}
            wrapper = parse_jsonish(stdout)
            analysis = extract_analysis(wrapper)
            credits = parse_credits(wrapper)
            payload = normalize(meta, QWEN_MODEL, wrapper, analysis, reader, credits)
            payload['started_at'] = started
            if shard_context is not None:
                payload['shard_context'] = shard_context
            if not write_result(final_path, payload):
                return {'status':'SKIPPED_EXISTS','path':str(final_path),'credits':0.0}
            return {'status':'SUCCESS','path':str(final_path),'credits':credits}
        except ToolCallIncomplete as exc:
            last_detail = str(exc)
            if attempt == 1:
                continue
            payload = failure_payload(meta, QWEN_MODEL, 'MODEL_PROTOCOL_INCOMPLETE', last_detail)
            write_failure(final_path, payload)
            return {'status':'FAILED','kind':'MODEL_PROTOCOL_INCOMPLETE','path':str(final_path),'credits':0.0}
        except ModelReportedError as exc:
            payload = failure_payload(meta, QWEN_MODEL, exc.kind, str(exc))
            write_failure(final_path, payload)
            return {'status':'FAILED','kind':exc.kind,'path':str(final_path),'credits':0.0}
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ''
            stderr = exc.stderr if isinstance(exc.stderr, str) else ''
            save_model_streams(raw_path, stdout, stderr, attempt)
            if attempt == 1:
                last_detail = stdout + '\n[TIMEOUT]\n' + stderr
                time.sleep(2)
                continue
            payload = failure_payload(meta, QWEN_MODEL, 'TIMEOUT', last_detail or (stdout + '\n[TIMEOUT]\n' + stderr))
            write_failure(final_path, payload)
            return {'status':'FAILED','kind':'TIMEOUT','path':str(final_path),'credits':0.0}
        except Exception as exc:
            if attempt == 1:
                last_detail = repr(exc)
                time.sleep(2)
                continue
            payload = failure_payload(meta, QWEN_MODEL, 'CONTROLLER_EXCEPTION', last_detail or repr(exc))
            write_failure(final_path, payload)
            return {'status':'FAILED','kind':'CONTROLLER_EXCEPTION','path':str(final_path),'credits':0.0}
    raise AssertionError('unreachable')


def run_qwen(meta: dict[str, Any]) -> dict[str, Any]:
    final_path, raw_path = semantic_paths('qwen', meta)
    reader_path = Path(meta['reader_result_path'])
    reader = read_json(reader_path)
    prompt = base_prompt(meta, model_role='Qwen3.8-Max：证据型主阅读。优先高召回地提取事实、指标、规则、版本、时间线与冲突；宁可保留歧义，不得自行补全。', reference_mode=False)
    return _run_qwen_once(meta, reader_path, reader, final_path, raw_path, prompt)


def run_qwen_sharded(meta: dict[str, Any]) -> dict[str, Any]:
    final_path, _ = semantic_paths('qwen', meta)
    if result_success(final_path):
        return {'status': 'SKIPPED_EXISTS', 'path': str(final_path), 'credits': 0.0, 'sharded': False}
    reader_path = Path(meta['reader_result_path']); reader = read_json(reader_path)
    shard_values = shard_reader_result(reader, meta)
    shard_root = Path(os.environ.get('PAW_SEMANTIC_OUTPUT_ROOT', str(BASE / 'semantic'))) / 'qwen_shards' / meta['batch'] / safe_component(meta['source_version_id'])
    records: list[tuple[dict[str, Any], dict[str, Any]]] = []; shard_manifest = []; total_credits = 0.0
    for shard in shard_values:
        context = shard['shard_context']; shard_id = context['shard_id']
        reader_shard_path = shard_root / f'{shard_id}.reader.json'
        shard_result_path = shard_root / f'{shard_id}.json'; shard_raw_path = shard_root / f'{shard_id}.raw.txt'
        atomic_json(reader_shard_path, shard)
        shard_prompt = base_prompt(meta, model_role='Qwen3.8-Max：证据型主阅读。当前输入是 ReaderResult 的确定性 shard；完整读取本 shard 的所有字段，使用其中真实 locator，不要总结后丢弃原文，也不要调用工具。', reference_mode=False)
        result = _run_qwen_once(meta, reader_shard_path, shard, shard_result_path, shard_raw_path, shard_prompt, context)
        shard_manifest.append({'shard_id': shard_id, 'reader_path': str(reader_shard_path), 'result_path': str(shard_result_path), 'locator_range': context['locator_range'], 'status': result.get('status')})
        if result.get('status') not in {'SUCCESS', 'SKIPPED_EXISTS'}:
            failure = failure_payload(meta, QWEN_MODEL, result.get('kind', 'SHARD_FAILED'), f'Qwen shard failed: {shard_id}')
            failure['sharding'] = {'threshold_bytes': SHARD_THRESHOLD_BYTES, 'source_bytes': _json_bytes(reader), 'shards': shard_manifest}
            write_failure(final_path, failure)
            return {'status': 'FAILED', 'kind': result.get('kind', 'SHARD_FAILED'), 'path': str(final_path), 'credits': total_credits, 'sharded': True}
        total_credits += float(result.get('credits') or 0.0)
        shard_payload = read_json(shard_result_path)
        records.append((shard, shard_payload.get('analysis', {})))
    merged = merge_shard_analyses(records)
    payload = normalize(meta, QWEN_MODEL, {}, merged, reader, total_credits)
    payload['sharding'] = {'threshold_bytes': SHARD_THRESHOLD_BYTES, 'source_bytes': _json_bytes(reader), 'shard_count': len(shard_values), 'shards': shard_manifest, 'lossless': True, 'merge': 'deterministic_concatenate_dedupe_with_locator_remap'}
    payload['started_at'] = now()
    if not write_result(final_path, payload):
        return {'status': 'SKIPPED_EXISTS', 'path': str(final_path), 'credits': 0.0, 'sharded': True}
    return {'status': 'SUCCESS', 'path': str(final_path), 'credits': total_credits, 'sharded': True, 'shard_count': len(shard_values)}


def run_agy(meta: dict[str, Any], *, visual: bool = False) -> dict[str, Any]:
    model = VISUAL_MODEL if visual else AGY_MODEL
    model_dir = 'gemini31pro' if visual else 'agy'
    final_path, raw_path = semantic_paths(model_dir, meta)
    if result_success(final_path):
        return {'status':'SKIPPED_EXISTS','path':str(final_path)}
    reader_path = Path(meta['reader_result_path'])
    reader = read_json(reader_path)
    if visual:
        prompt = base_prompt(meta, model_role='Gemini 3.1 Pro High：视觉证据专项。必须读取 ReaderResult 中 visual_pages/slide/page 相关派生文件，重点判断箭头、连接、分组、颜色、图表、版面层级是否承载业务语义；不要仅重复文本抽取。', reference_mode=False)
    else:
        prompt = base_prompt(meta, model_role='Gemini 3.7 Flash High：独立第二阅读器与项目关系/风险发现器。既要提取证据事实，也要明确分离 inference，重点发现跨项目关系、风险、冲突、遗漏和需要视觉复核之处。', reference_mode=meta['project_tier'] != 'CORE')
    # Agy has no attachment flag. Embed the already materialized ReaderResult so
    # headless mode never needs a command tool merely to read its input.
    prompt += '\n\n以下是唯一允许使用的 ReaderResult（已由控制器读取并内嵌）。不要调用任何工具、不要执行命令：\n' + json.dumps(reader, ensure_ascii=False)
    workdir = str(reader_path.parent)
    log_root = Path(os.environ.get('PAW_SEMANTIC_LOG_ROOT', str(BASE / 'logs' / 'semantic')))
    log_path = log_root / model_dir / meta['batch'] / f"{safe_component(meta['source_version_id'])}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = agy_stream_command(model, log_path)
    stream_input = json.dumps({'event': 'user', 'message': {'role': 'user', 'content': prompt}}, ensure_ascii=False) + '\n'
    started = now()
    try:
        proc = subprocess.run(cmd, cwd=workdir, input=stream_input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=MODEL_CALL_TIMEOUT_SECONDS, check=False)
        stdout, stderr = proc.stdout or '', proc.stderr or ''
        save_model_streams(raw_path, stdout, stderr, 1)
        if proc.returncode != 0:
            kind = classify_model_failure(stdout, stderr, proc.returncode)
            payload = failure_payload(meta, model, kind, stdout + '\n[STDERR]\n' + stderr, proc.returncode); write_failure(final_path, payload)
            return {'status':'FAILED','kind':kind,'path':str(final_path)}
        wrapper = extract_agy_stream_result(stdout)
        analysis = extract_analysis(wrapper)
        payload = normalize(meta, model, wrapper, analysis, reader, 0.0)
        payload['started_at'] = started
        if not write_result(final_path, payload):
            return {'status':'SKIPPED_EXISTS','path':str(final_path)}
        return {'status':'SUCCESS','path':str(final_path)}
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ''
        stderr = exc.stderr if isinstance(exc.stderr, str) else ''
        save_model_streams(raw_path, stdout, stderr, 1)
        payload = failure_payload(meta, model, 'TIMEOUT', stdout + '\n[TIMEOUT]\n' + stderr); write_failure(final_path, payload)
        return {'status':'FAILED','kind':'TIMEOUT','path':str(final_path)}
    except ModelReportedError as exc:
        payload = failure_payload(meta, model, exc.kind, str(exc)); write_failure(final_path, payload)
        return {'status':'FAILED','kind':exc.kind,'path':str(final_path)}
    except ToolCallIncomplete as exc:
        payload = failure_payload(meta, model, 'MODEL_PROTOCOL_INCOMPLETE', str(exc)); write_failure(final_path, payload)
        return {'status':'FAILED','kind':'MODEL_PROTOCOL_INCOMPLETE','path':str(final_path)}
    except Exception as exc:
        payload = failure_payload(meta, model, 'CONTROLLER_EXCEPTION', repr(exc)); write_failure(final_path, payload)
        return {'status':'FAILED','kind':'CONTROLLER_EXCEPTION','path':str(final_path)}


def agy_available(state: dict[str, Any]) -> bool:
    until = state.get('agy', {}).get('cooldown_until')
    if not until:
        return True
    try:
        dt = datetime.fromisoformat(until)
        return datetime.now(dt.tzinfo) >= dt
    except Exception:
        return True


def visual_available(state: dict[str, Any]) -> bool:
    visual = state.get('visual', {})
    if not visual.get('enabled', True):
        return False
    until = visual.get('cooldown_until')
    if not until:
        return True
    try:
        dt = datetime.fromisoformat(until)
        return datetime.now(dt.tzinfo) >= dt
    except Exception:
        return True


def retryable_failure(kind: str | None) -> bool:
    return kind in {'CONTROLLER_EXCEPTION', 'MODEL_ERROR', 'MODEL_PROTOCOL_INCOMPLETE', 'TIMEOUT'}


def write_heartbeat(state: dict[str, Any], *, phase: str = 'RUNNING') -> None:
    """Publish liveness without racing the larger semantic checkpoint."""
    atomic_json(HEARTBEAT_PATH, {
        'pid': os.getpid(),
        'at': now(),
        'phase': phase,
        'status': state.get('status'),
        'current_batch': state.get('current_batch'),
    })


def heartbeat_loop(stop_event: Any) -> None:
    # This sidecar remains live during a 30-minute model call. It is separate
    # from semantic_state.json so heartbeat writes cannot overwrite a source
    # checkpoint being committed by the controller thread.
    while not stop_event.wait(15):
        try:
            try:
                state = read_json(STATE_PATH)
            except (OSError, ValueError, TypeError):
                state = {'status': 'RUNNING', 'current_batch': None}
            write_heartbeat(state)
        except OSError:
            pass


def mark_pending(kind: str, meta: dict[str, Any], reason: str) -> None:
    directory = BASE / 'queues' / f'{kind}_pending_items' / meta['batch']
    path = directory / f"{safe_component(meta['source_version_id'])}.json"
    atomic_json(path, {'queued_at':now(),'reason':reason,'source_version_id':meta['source_version_id'],'source_id':meta['source_id'],'batch':meta['batch'],'project':meta['paw_project_name'],'reader_result_path':meta['reader_result_path']})


def clear_pending(kind: str, meta: dict[str, Any]) -> None:
    path = BASE / 'queues' / f'{kind}_pending_items' / meta['batch'] / f"{safe_component(meta['source_version_id'])}.json"
    try: path.unlink()
    except FileNotFoundError: pass


def should_visual(meta: dict[str, Any]) -> tuple[bool, str]:
    if meta['project_tier'] != 'CORE' or meta['family'].upper() not in {'PPTX','PDF'}:
        return False, ''
    try:
        reader = read_json(Path(meta['reader_result_path']))
    except Exception:
        return False, ''
    flags = reader.get('fidelity_flags', {}) if isinstance(reader.get('fidelity_flags'), dict) else {}
    if int(flags.get('chart_count') or 0) > 0:
        return True, 'reader_chart_count>0'
    if int(flags.get('connector_count') or 0) > 0:
        return True, 'reader_connector_count>0'
    title = meta.get('title','')
    if any(k in title for k in VISUAL_KEYWORDS):
        return True, 'important_visual_document_title'
    agy_path, _ = semantic_paths('agy', meta)
    if result_success(agy_path):
        try:
            a = read_json(agy_path).get('analysis', {})
            if a.get('needs_visual_review') is True:
                return True, 'agy_requested_visual_review'
        except Exception:
            pass
    return False, ''


def canonical_source_catalog() -> list[dict[str, Any]]:
    """Build routing only from canonical_reader_batches.json and its ledgers."""
    catalog: list[dict[str, Any]] = []
    for batch in ORDER:
        if not reader_gate_pass(batch):
            continue
        manifest = metadata_for_batch(batch)
        for index, entry in enumerate(ledger_entries(batch), 1):
            meta = authoritative_meta(batch, entry, manifest); meta['source_index'] = index
            catalog.append(meta)
    return catalog


def _model_result_path(worker: str, meta: dict[str, Any]) -> Path:
    return semantic_paths({'flash': 'agy', 'qwen': 'qwen', 'visual': 'gemini31pro'}[worker], meta)[0]


def _worker_cooldown_until(state: dict[str, Any], worker: str) -> str | None:
    value = state.get('workers', {}).get(worker, {}).get('cooldown_until')
    legacy = state.get({'flash': 'agy', 'qwen': 'qwen', 'visual': 'visual'}[worker], {}).get('cooldown_until')
    return value or legacy


def worker_available(state: dict[str, Any], worker: str) -> bool:
    if not state.get('workers', {}).get(worker, {}).get('enabled', True):
        return False
    if worker == 'qwen':
        q = state.get('qwen', {})
        if not q.get('enabled', True) or float(q.get('credits_consumed_this_run', 0.0)) >= QWEN_SOFT_CREDIT_CAP:
            return False
    if worker == 'flash' and not state.get('agy', {}).get('enabled', True):
        return False
    if worker == 'visual' and not state.get('visual', {}).get('enabled', True):
        return False
    until = _worker_cooldown_until(state, worker)
    if not until:
        return True
    try:
        dt = datetime.fromisoformat(until); return datetime.now(dt.tzinfo) >= dt
    except Exception:
        return True


def qwen_priority(meta: dict[str, Any]) -> tuple[int, int, int]:
    # CORE is the complete Qwen scope. Flash conflict/ambiguity/data signals
    # are a deterministic priority boost, never a coverage dependency.
    signal_score = 0
    try:
        analysis = read_json(semantic_paths('agy', meta)[0]).get('analysis', {})
        signal_score += 4 * bool(analysis.get('conflicts'))
        signal_score += 3 * bool(analysis.get('ambiguities'))
        signal_score += 2 * bool(analysis.get('metrics'))
        signal_score += 1 * bool(analysis.get('risks') or analysis.get('decisions_or_plans'))
    except (OSError, ValueError, TypeError):
        pass
    return (-signal_score, ORDER.index(meta['batch']), int(meta.get('source_index', 0)))


def next_worker_source(worker: str, catalog: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any] | None:
    candidates = []
    for meta in catalog:
        if meta['source_version_id'] in ACTIVE_MODEL_SOURCES[worker]:
            continue
        if worker == 'qwen' and meta['project_tier'] != 'CORE':
            continue
        if worker == 'visual' and not should_visual(meta)[0]:
            continue
        if result_success(_model_result_path(worker, meta)):
            continue
        if not worker_available(state, worker):
            continue
        candidates.append(meta)
    if not candidates:
        return None
    if worker == 'qwen':
        return sorted(candidates, key=qwen_priority)[0]
    worker_state = state.get('workers', {}).get(worker, {})
    cursor = worker_state.get('cursor', {})
    cursor_key = (str(cursor.get('batch') or ''), int(cursor.get('index') or 0))
    ordered = sorted(candidates, key=lambda m: (ORDER.index(m['batch']), int(m.get('source_index', 0))))
    cursor_position = (ORDER.index(cursor_key[0]), cursor_key[1]) if cursor_key[0] in ORDER else None
    for meta in ordered:
        position = (ORDER.index(meta['batch']), int(meta.get('source_index', 0)))
        if cursor_position is None or position > cursor_position:
            return meta
    return ordered[0]


def _set_model_cooldown(state: dict[str, Any], worker: str, kind: str) -> None:
    until = (datetime.now(timezone.utc).astimezone() + timedelta(seconds=AGY_QUOTA_COOLDOWN_SECONDS)).isoformat()
    state['workers'][worker]['cooldown_until'] = until
    state['workers'][worker]['last_error'] = kind
    legacy = {'flash': 'agy', 'qwen': 'qwen', 'visual': 'visual'}[worker]
    if legacy != 'qwen':
        state[legacy]['cooldown_until'] = until; state[legacy]['pause_reason'] = kind


def record_worker_result(worker: str, meta: dict[str, Any], result: dict[str, Any]) -> None:
    kind = result.get('kind')
    status = result.get('status')
    with STATE_MUTEX:
        state = load_state(); ws = state['workers'][worker]
        ws.update({'status': 'IDLE', 'active_source_version_id': None, 'last_source': meta['source_version_id'], 'attempts': int(ws.get('attempts', 0)) + 1, 'cursor': {'batch': meta['batch'], 'index': meta['source_index']}})
        worker_checkpoint(worker, cursor=ws['cursor'], last_source=meta['source_version_id'], last_status=status)
        if status == 'SUCCESS':
            ws['successes'] = int(ws.get('successes', 0)) + 1; ws['last_success'] = meta['source_version_id']; ws['last_error'] = None; ws['cooldown_until'] = None
            clear_pending({'flash': 'agy', 'qwen': 'qwen', 'visual': 'visual'}[worker], meta)
            if worker == 'qwen':
                credit = float(result.get('credits') or 0.0)
                state['qwen']['credits_consumed_this_run'] = round(float(state['qwen'].get('credits_consumed_this_run', 0.0)) + credit, 6)
                if state['qwen']['credits_consumed_this_run'] >= QWEN_SOFT_CREDIT_CAP:
                    state['qwen']['enabled'] = False; state['qwen']['pause_reason'] = 'SOFT_CREDIT_CAP'; state['qwen']['paused_at'] = now()
            append_event(f'{worker.upper()}_WORKER_SUCCESS', batch=meta['batch'], source_version_id=meta['source_version_id'], credits=result.get('credits', 0.0))
        elif status == 'SKIPPED_EXISTS':
            ws['reused_successes'] = int(ws.get('reused_successes', 0)) + 1; ws['last_success'] = meta['source_version_id']; ws['last_error'] = None
        else:
            ws['last_error'] = kind or 'FAILED'
            mark_pending({'flash': 'agy', 'qwen': 'qwen', 'visual': 'visual'}[worker], meta, kind or 'FAILED')
            if kind in {'HARD_QUOTA', 'RATE_LIMIT'}:
                _set_model_cooldown(state, worker, kind)
                if worker == 'visual' and kind == 'HARD_QUOTA':
                    state['visual']['pending_quota'] = int(state['visual'].get('pending_quota', 0)) + 1
            append_event(f'{worker.upper()}_WORKER_FAILED', batch=meta['batch'], source_version_id=meta['source_version_id'], reason=kind)
        save_state(state); atomic_json(WORKERS_STATE_PATH, state['workers'])


def run_worker_model(worker: str, meta: dict[str, Any]) -> dict[str, Any]:
    if worker == 'flash':
        return run_agy(meta)
    if worker == 'visual':
        return run_agy(meta, visual=True)
    reader_path = Path(meta['reader_result_path'])
    return run_qwen_sharded(meta) if reader_path.stat().st_size > SHARD_THRESHOLD_BYTES else run_qwen(meta)


STOP_EVENT = threading.Event()


class QueueWorker(threading.Thread):
    def __init__(self, worker: str, catalog_loader: Any):
        super().__init__(name=f'semantic-{worker}-worker', daemon=False)
        self.worker = worker; self.catalog_loader = catalog_loader

    def run(self) -> None:
        initial = load_state().get('workers', {}).get(self.worker, {})
        worker_checkpoint(self.worker, cursor=initial.get('cursor', {'batch': None, 'index': 0}), phase='STARTING')
        save_worker_state(self.worker, status='IDLE', pid=os.getpid())
        write_worker_heartbeat(self.worker, phase='STARTING')
        while not STOP_EVENT.is_set():
            try:
                state = load_state(); catalog = self.catalog_loader(); meta = next_worker_source(self.worker, catalog, state)
                if meta is None:
                    save_worker_state(self.worker, status='IDLE', active_source_version_id=None)
                    write_worker_heartbeat(self.worker, phase='IDLE', queue=self.worker)
                    STOP_EVENT.wait(WORKER_POLL_SECONDS); continue
                source_id = meta['source_version_id']
                ACTIVE_MODEL_SOURCES[self.worker].add(source_id)
                save_worker_state(self.worker, status='RUNNING', active_source_version_id=source_id, last_source=source_id, last_error=None)
                worker_checkpoint(self.worker, cursor={'batch': meta['batch'], 'index': meta['source_index']}, active_source_version_id=source_id, phase='RUNNING')
                write_worker_heartbeat(self.worker, phase='RUNNING', source_version_id=source_id, batch=meta['batch'])
                try:
                    result = run_worker_model(self.worker, meta)
                except Exception as exc:
                    result = {'status': 'FAILED', 'kind': 'CONTROLLER_EXCEPTION', 'detail': repr(exc)}
                record_worker_result(self.worker, meta, result)
                write_worker_heartbeat(self.worker, phase='IDLE', last_source=source_id, last_status=result.get('status'), last_error=result.get('kind'))
                STOP_EVENT.wait(0.1)
            except Exception as exc:
                save_worker_state(self.worker, status='ERROR', last_error=repr(exc))
                write_worker_heartbeat(self.worker, phase='ERROR', error=repr(exc))
                STOP_EVENT.wait(WORKER_COOLDOWN_SECONDS)
        save_worker_state(self.worker, status='STOPPED', active_source_version_id=None)
        write_worker_heartbeat(self.worker, phase='STOPPED')


def summarize_batch(batch: str, catalog: list[dict[str, Any]]) -> dict[str, Any]:
    sources = [m for m in catalog if m['batch'] == batch]
    stats = {'sources': len(sources), 'expected': len(sources), 'covered': 0, 'coverage_ok': 0, 'qwen_success': 0, 'flash_success': 0, 'qwen_ok': 0, 'agy_ok': 0, 'visual_selected': 0, 'visual_success': 0, 'visual_ok': 0, 'visual_pending_quota': 0, 'needs_review': 0, 'failed_retryable': 0, 'last_source': None}
    for meta in sources:
        qp, _ = semantic_paths('qwen', meta); ap, _ = semantic_paths('agy', meta); vp, _ = semantic_paths('gemini31pro', meta)
        q = result_success(qp); a = result_success(ap); selected, _ = should_visual(meta); v = result_success(vp)
        stats['covered'] += int(a); stats['coverage_ok'] += int(a); stats['qwen_success'] += int(q); stats['qwen_ok'] += int(q); stats['flash_success'] += int(a); stats['agy_ok'] += int(a); stats['visual_selected'] += int(selected); stats['visual_success'] += int(selected and v); stats['visual_ok'] += int(selected and v)
        try:
            if selected and read_json(vp).get('reason') in {'HARD_QUOTA', 'VISUAL_PENDING_QUOTA'}: stats['visual_pending_quota'] += 1
        except (OSError, ValueError, TypeError): pass
        for path in (qp, ap, vp):
            try:
                obj = read_json(path)
                if obj.get('status') == 'SUCCESS' and obj.get('evidence_validation', {}).get('status') == 'NEEDS_REVIEW': stats['needs_review'] += 1
                if obj.get('status') == 'FAILED' and retryable_failure(obj.get('reason')): stats['failed_retryable'] += 1
            except (OSError, ValueError, TypeError): pass
        stats['last_source'] = meta['source_version_id']
    return stats


def reconcile_state(state: dict[str, Any], catalog: list[dict[str, Any]]) -> None:
    state.setdefault('batches', {})
    for batch in ORDER:
        sources = [m for m in catalog if m['batch'] == batch]
        if not sources: continue
        bstate = state['batches'].setdefault(batch, {})
        progress = summarize_batch(batch, catalog)
        bstate.update({'reader_gate': 'PASS', 'source_count': len(sources), 'progress': progress, 'updated_at': now()})
        # Correct stale reader-gate labels from pre-canonical routing without
        # rerunning Reader or touching any historical semantic result.
        if bstate.get('status') == 'BLOCKED_READER_GATE': bstate['status'] = 'RUNNING'
        if progress['flash_success'] == progress['expected']:
            bstate['status'] = 'SEMANTIC_COMPLETE' if progress['visual_ok'] == progress['visual_selected'] else 'SEMANTIC_COMPLETE_VISUAL_PENDING'
        else:
            bstate['status'] = 'RUNNING'
    state['current_batch'] = next((m['batch'] for m in catalog if not result_success(semantic_paths('agy', m)[0])), None)


def refresh_overall_progress(state: dict[str, Any], catalog: list[dict[str, Any]] | None = None) -> None:
    catalog = catalog if catalog is not None else canonical_source_catalog()
    expected = len(catalog); batches = {batch: summarize_batch(batch, catalog) for batch in ORDER}
    flash_success = sum(x['flash_success'] for x in batches.values()); qwen_success = sum(x['qwen_success'] for x in batches.values()); visual_success = sum(x['visual_success'] for x in batches.values()); visual_selected = sum(x['visual_selected'] for x in batches.values()); visual_pending = sum(x['visual_pending_quota'] for x in batches.values()); needs_review = sum(x['needs_review'] for x in batches.values()); failed = sum(x['failed_retryable'] for x in batches.values())
    state['overall'] = {'expected': expected, 'covered': flash_success, 'coverage': f'{flash_success}/{expected}', 'qwen_success': qwen_success, 'flash_success': flash_success, 'visual_success': visual_success, 'visual_selected': visual_selected, 'visual_pending_quota': visual_pending, 'needs_review': needs_review, 'failed_retryable': failed}
    state['coverage_breakdown'] = {'basic_semantic_coverage': {'model': AGY_MODEL, 'success': flash_success, 'expected': expected, 'coverage': f'{flash_success}/{expected}', 'gate': 'FLASH_ONLY'}, 'qwen_deep_read': {'model': QWEN_MODEL, 'scope': 'CORE', 'success': qwen_success, 'expected': sum(1 for m in catalog if m['project_tier'] == 'CORE'), 'coverage': f"{qwen_success}/{sum(1 for m in catalog if m['project_tier'] == 'CORE')}"}, 'visual_obligations': {'model': VISUAL_MODEL, 'selected': visual_selected, 'success': visual_success, 'pending_quota': visual_pending, 'coverage': f'{visual_success}/{visual_selected}'}}
    for batch, stats in batches.items():
        if batch in state.get('batches', {}): state['batches'][batch]['progress'] = stats


def all_reader_done() -> bool:
    reg = reader_registry(); return bool(reg.get('required_source_count') == 710 and reg.get('batches') and all(reader_gate_pass(batch) for batch in ORDER))


def all_queues_complete(state: dict[str, Any], catalog: list[dict[str, Any]]) -> bool:
    if any(not result_success(semantic_paths('agy', m)[0]) for m in catalog): return False
    if state.get('qwen', {}).get('enabled', True) and float(state.get('qwen', {}).get('credits_consumed_this_run', 0.0)) < QWEN_SOFT_CREDIT_CAP and any(m['project_tier'] == 'CORE' and not result_success(semantic_paths('qwen', m)[0]) for m in catalog): return False
    if state.get('visual', {}).get('enabled', True) and any(should_visual(m)[0] and not result_success(semantic_paths('gemini31pro', m)[0]) for m in catalog): return False
    return True


def acquire_lock() -> None:
    if LOCK_PATH.exists():
        try:
            pid = int(LOCK_PATH.read_text().strip())
            os.kill(pid, 0)
            raise SystemExit(f'semantic supervisor already running pid={pid}')
        except ProcessLookupError:
            LOCK_PATH.unlink(missing_ok=True)
        except ValueError:
            LOCK_PATH.unlink(missing_ok=True)
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.write(fd, str(os.getpid()).encode()); os.close(fd)


def main() -> int:
    acquire_lock()
    state = load_state(); catalog = canonical_source_catalog(); reconcile_state(state, catalog); refresh_overall_progress(state, catalog); save_state(state); atomic_json(WORKERS_STATE_PATH, state.get('workers', {}))
    append_event('SEMANTIC_SUPERVISOR_STARTED', pid=os.getpid(), qwen_model=QWEN_MODEL, agy_model=AGY_MODEL, visual_model=VISUAL_MODEL, qwen_soft_credit_cap=QWEN_SOFT_CREDIT_CAP)
    def stop_handler(signum: int, frame: Any) -> None:
        STOP_EVENT.set()
    signal.signal(signal.SIGTERM, stop_handler); signal.signal(signal.SIGINT, stop_handler)
    stop_heartbeat = threading.Event()
    heartbeat = threading.Thread(target=heartbeat_loop, args=(stop_heartbeat,), name='semantic-heartbeat', daemon=True)
    heartbeat.start()
    workers = [QueueWorker(name, canonical_source_catalog) for name in WORKER_NAMES]
    for worker in workers: worker.start()
    try:
        while not STOP_EVENT.is_set():
            catalog = canonical_source_catalog()
            # Reload while holding the same mutex used by workers' checkpoint
            # commits; otherwise a main-loop progress refresh can overwrite a
            # worker's newer cursor/status with a stale whole-state snapshot.
            with STATE_MUTEX:
                state = load_state()
                reconcile_state(state, catalog); refresh_overall_progress(state, catalog)
                state['status'] = 'RUNNING'
                save_state(state)
            write_heartbeat(state)
            if state.get('agy',{}).get('cooldown_until') and agy_available(state):
                state['agy']['cooldown_until'] = None; state['agy']['pause_reason'] = None; save_state(state); append_event('AGY_COOLDOWN_ENDED')
            if state.get('visual',{}).get('cooldown_until') and visual_available(state):
                state['visual']['cooldown_until'] = None; state['visual']['pause_reason'] = None; save_state(state); append_event('VISUAL_COOLDOWN_ENDED')
            if all_reader_done() and all_queues_complete(state, catalog):
                state['status'] = 'SEMANTIC_COMPLETE_ALL_QUEUES'; state['current_batch'] = None; state['finished_at'] = now(); save_state(state); append_event('SEMANTIC_SUPERVISOR_ALL_QUEUES_COMPLETE'); break
            STOP_EVENT.wait(POLL_SECONDS)
    finally:
        STOP_EVENT.set()
        for worker in workers: worker.join(timeout=5)
        stop_heartbeat.set()
        try:
            write_heartbeat(state, phase='STOPPING')
        except OSError:
            pass
        try: LOCK_PATH.unlink()
        except OSError: pass


if __name__ == '__main__':
    raise SystemExit(main())
