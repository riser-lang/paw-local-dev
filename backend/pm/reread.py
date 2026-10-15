"""Bounded targeted rereading, independent of the old no-history planner dedup."""
import hashlib
import json
import uuid
from pathlib import Path
from . import storage

REASONS={'new_version','conflict','low_coverage'}


def enqueue(conn,sid,reason,trigger_ref):
    if reason not in REASONS:raise ValueError('不允许无条件复读')
    conn.execute('INSERT OR IGNORE INTO pm_reread_job VALUES(?,?,?,?,\'pending\',0,NULL,?,?)',
        (str(uuid.uuid4()),sid,reason,trigger_ref,storage.now(),storage.now()))


def _perform(job):
    from backend.reader.dispatch import isolated_read_artifact
    from backend.reader.contract import ReaderInput
    from .coverage import record,save_extraction,evidence_body
    from .updates import process_source
    from backend.provider.adapter import get_active_provider
    result={'status':'failed','reason':'source_unavailable'}
    try:
        path=Path(job['path']) if job['path'] else None
        if path and path.is_file():
            from backend.reader.contract import artifact_sha256
            digest=artifact_sha256(path)
            if digest!=job['source_version_id']:
                result={'status':'blocked','reason':'source_bytes_changed'}
            else:
                from backend.core.config import DATA_DIR
                output=DATA_DIR/'reread'/job['id']
                ok,read,error,_=isolated_read_artifact(ReaderInput(str(path),source_object_id=path.stem,source_version=digest[:12],display_name=path.name,raw_artifact_sha256=digest),output,timeout=60)
                if ok and read:
                    evidence=read.to_dict()
                    with storage.connect() as conn:
                        coverage=record(conn,digest,path.name,evidence)
                        save_extraction(conn,digest,evidence)
                    body=evidence_body(evidence)
                    # Reassess already-retained full evidence, not a duplicate general proposal batch.
                    target=json.loads(job['trigger_ref']).get('section_id') if job['reason']=='conflict' else None
                    assessment=process_source(digest,get_active_provider(),only_sections=[target] if target else None)
                    result={'status':'completed','reader_type':read.reader_type,'coverage':coverage,
                        'new_extraction_body':body,'assessment_status':assessment['status'],
                        'remaining_limitations':coverage['limitations']}
                else:
                    result={'status':'failed','reason':error or 'read_failed'}
    except Exception:
        # Do not persist paths, model credentials or exception text in diagnostic results.
        result={'status':'failed','reason':'reread_failed'}
    return result


def run(limit=5):
    # Claim one at a time; never hold the SQLite writer while parsing/model work runs.
    limit=max(1,min(10,int(limit)))
    outcomes=[]
    for _ in range(limit):
        with storage.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("SELECT j.*,l.path FROM pm_reread_job j LEFT JOIN pm_source_location l ON l.source_version_id=j.source_version_id WHERE j.status='pending' ORDER BY j.created_at,j.id LIMIT 1").fetchone()
            if not row:break
            job=dict(row)
            conn.execute("UPDATE pm_reread_job SET status='running',attempts=attempts+1,updated_at=? WHERE id=?",(storage.now(),job['id']))
        result=_perform(job)
        with storage.connect() as conn:
            conn.execute('UPDATE pm_reread_job SET status=?,result_json=?,updated_at=? WHERE id=?',
                         (result['status'],storage.encoded(result),storage.now(),job['id']))
        outcomes.append({'id':job['id'],'source_version_id':job['source_version_id'],'reason':job['reason'],'status':result['status']})
    return {'processed':len(outcomes),'limit':limit,'items':outcomes}
