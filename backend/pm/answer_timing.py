"""Numeric-only request timing; no prompts, keys, endpoints or user content."""
from contextvars import ContextVar
from functools import wraps
import logging
import time

current = ContextVar('paw_answer_timing',default=None)
logger=logging.getLogger('paw.answer_timing')


def measured(function):
    @wraps(function)
    def call(*args,**kwargs):
        stats={'model_calls':0,'model_seconds':0.0,'prompt_bytes':0,'search_seconds':0.0}
        token=current.set(stats)
        started=time.perf_counter()
        try:
            result=function(*args,**kwargs)
            # A model cannot erase an unresolved conflict by omitting its disclaimer.
            if isinstance(result,dict):
                pid=(result.get('grounding') or {}).get('project_id')
                if pid:
                    from . import storage
                    from .updates import read_conflicts
                    with storage.connect() as conn:
                        conflicts=[c for c in read_conflicts(conn,pid) if c['status']=='open']
                    if conflicts:
                        result['conflicts']=[{'id':c['id'],'section_id':c['section_id']} for c in conflicts]
                        if '存在冲突，待用户裁决' not in str(result.get('response','')):
                            result['response']='存在冲突，待用户裁决（'+ '、'.join(sorted({c['section_id'] for c in conflicts}))+'）；以下内容不能视为该处的新结论。\n\n'+str(result.get('response',''))
            stats['total_seconds']=round(time.perf_counter()-started,4)
            if isinstance(result,dict):
                result['timings']=dict(stats)
            return result
        finally:
            stats['total_seconds']=round(time.perf_counter()-started,4)
            logger.info('Assistant timings %s',stats)
            current.reset(token)
    return call
