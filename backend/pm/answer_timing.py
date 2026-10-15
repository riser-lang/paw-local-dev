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
            stats['total_seconds']=round(time.perf_counter()-started,4)
            if isinstance(result,dict):
                result['timings']=dict(stats)
            return result
        finally:
            stats['total_seconds']=round(time.perf_counter()-started,4)
            logger.info('Assistant timings %s',stats)
            current.reset(token)
    return call
