"""Opt-in boot diagnostics: stage names and numeric timing, never user content."""
import json
import os
import time
from pathlib import Path

_started=time.monotonic()


def event(stage,**details):
    target=os.environ.get('PAW_BOOT_TRACE')
    if not target:return
    safe={k:v for k,v in details.items() if k in {'port','requested_port','error_type'} and isinstance(v,(str,int))}
    row={'stage':stage,'pid':os.getpid(),'elapsed_seconds':round(time.monotonic()-_started,4),**safe}
    try:
        with Path(target).open('a',encoding='utf-8') as handle:handle.write(json.dumps(row,ensure_ascii=True)+'\n')
    except OSError:pass
