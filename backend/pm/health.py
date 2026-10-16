"""Persist observed local operations; a page read never probes the model."""
import hashlib
import shutil
from datetime import datetime,timedelta,timezone
from . import storage

LABELS={'patrol':'巡航','scan':'扫描','backup':'备份','model':'模型连通','disk':'磁盘'}


def migrate(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS pm_system_health(key TEXT PRIMARY KEY,
      last_success_at TEXT,last_attempt_at TEXT NOT NULL,state TEXT NOT NULL,summary TEXT NOT NULL,
      context_fingerprint TEXT)''')


def identity(credentials):
    if not credentials.get('api_key'):return None
    return hashlib.sha256('\0'.join(str(credentials.get(k) or '') for k in ('base_url','model','api_key')).encode()).hexdigest()


def record(key,success,summary,context=None):
    if key not in LABELS:raise ValueError('未知本地状态项')
    with storage.connect() as conn:
        conn.execute('''INSERT INTO pm_system_health VALUES(?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET
          last_success_at=CASE WHEN excluded.state='ok' THEN excluded.last_success_at ELSE pm_system_health.last_success_at END,
          last_attempt_at=excluded.last_attempt_at,state=excluded.state,summary=excluded.summary,
          context_fingerprint=excluded.context_fingerprint''',
          (key,storage.now() if success else None,storage.now(),'ok' if success else 'error',summary,context))


def observe(key,success,summary,context=None):
    # Telemetry failure must not turn a committed business operation into an HTTP failure.
    try:record(key,success,summary,context)
    except Exception:
        import logging
        logging.getLogger('paw.pm.health').warning('本地状态记录暂不可用：%s',key)


def read(conn,data_dir,model_identity=None,at=None):
    at=at or datetime.now(timezone.utc);rows={r['key']:dict(r) for r in conn.execute('SELECT * FROM pm_system_health')};items=[]
    for key,label in LABELS.items():
        row=rows.get(key)
        item={'key':key,'label':label,'state':row['state'] if row else 'unknown',
            'summary':row['summary'] if row else '尚无成功记录','last_success_at':row['last_success_at'] if row else None}
        if key=='patrol' and item['last_success_at']:
            try:
                last=datetime.fromisoformat(item['last_success_at'].replace('Z','+00:00'))
                if last.tzinfo is None:last=last.replace(tzinfo=timezone.utc)
                if at-last>timedelta(hours=3):item.update(state='error',summary='最后成功巡航已超过 3 小时，请检查巡航是否开启')
            except ValueError:item.update(state='unknown',summary='巡航成功时间无效，请重新运行')
        if key=='model':
            if not model_identity:item.update(state='unknown',summary='需要填写 Key 才能测试模型连通')
            elif not row or row['context_fingerprint']!=model_identity:item.update(state='unknown',summary='当前模型配置尚未测试连接')
        if key=='disk':
            try:
                usage=shutil.disk_usage(data_dir)
                item.update(state='error' if usage.free<1024**3 else 'ok',summary='可用空间 '+str(round(usage.free/1024**3,1))+' GB',
                    last_success_at=storage.now() if usage.free>=1024**3 else item['last_success_at'])
            except OSError:item.update(state='unknown',summary='暂时无法读取本地磁盘空间')
        if key=='backup':
            check=conn.execute('SELECT * FROM pm_backup_selfcheck ORDER BY created_at DESC,id DESC LIMIT 1').fetchone()
            item['detail']='恢复自检：'+('通过' if check['ok'] else '失败')+'；'+check['created_at'] if check else '尚未进行恢复自检'
        items.append(item)
    return items
