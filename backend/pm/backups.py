"""Bounded automatic SQLite snapshots and isolated restore drills (stdlib only)."""
import json
import re
import sqlite3
import tempfile
import threading
import uuid
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from . import storage,health

_lock=threading.RLock()
AUTO_TAGS={'pre_batch_commit','post_batch_commit','pre_auto_convergence','daily'}
KEY_TABLES=('project','todo','knowledge_page','candidate','review_decision','approved_change','commit_record','pm_section_revision')


def migrate(conn):
    conn.executescript('''CREATE TABLE IF NOT EXISTS pm_backup_policy(id INTEGER PRIMARY KEY CHECK(id=1),max_count INTEGER NOT NULL,max_bytes INTEGER NOT NULL);
      INSERT OR IGNORE INTO pm_backup_policy VALUES(1,7,536870912);
      CREATE TABLE IF NOT EXISTS pm_backup_selfcheck(id TEXT PRIMARY KEY,backup_filename TEXT NOT NULL,
        ok INTEGER NOT NULL,counts_json TEXT NOT NULL,cleaned INTEGER NOT NULL,created_at TEXT NOT NULL);''')


def paths():return storage.runtime_paths()


def policy():
    with storage.connect() as conn:return dict(conn.execute('SELECT max_count,max_bytes FROM pm_backup_policy WHERE id=1').fetchone())


def set_policy(max_count,max_bytes):
    if not 1<=max_count<=100 or max_bytes<65536:raise ValueError('自动备份数量须为 1–100，容量至少 64 KiB')
    with _lock:
        latest=next((b for b in listing() if b['automatic'] and readable(Path(b['path']))),None)
        if latest and latest['size_bytes']>max_bytes:raise ValueError('容量不足以保留最新备份，请提高上限')
        with storage.connect() as conn:conn.execute('UPDATE pm_backup_policy SET max_count=?,max_bytes=? WHERE id=1',(max_count,max_bytes))
        rotate()
    return policy()


def confined(path,root):
    root=root.resolve();path=path.resolve()
    if not path.is_relative_to(root) or path==root:raise ValueError('备份路径越界')
    return path


def counts(conn):
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {name:conn.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in KEY_TABLES if name in tables}


def readable(file):
    try:
        with closing(sqlite3.connect(file.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
            return conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project'").fetchone())
    except (OSError,sqlite3.Error):return False


def listing():
    _,root=paths();root.mkdir(parents=True,exist_ok=True);items=[]
    for file in root.glob('workspace_backup_*.db'):
        if file.is_symlink() or not file.is_file():continue
        meta={}
        try:meta=json.loads(file.with_suffix('.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        # Legacy tagged automatic snapshots are included; unclassified files are preserved.
        tag=meta.get('tag') or next((t for t in AUTO_TAGS if file.stem.endswith('_'+t)),'unknown')
        automatic=meta.get('automatic',tag in AUTO_TAGS)
        stat=file.stat()
        items.append({'filename':file.name,'path':str(file),'tag':tag,'automatic':bool(automatic),
            'size_bytes':stat.st_size,'size_mb':round(stat.st_size/1024**2,2),'mtime':stat.st_mtime,
            'formatted_time':datetime.fromtimestamp(stat.st_mtime,timezone.utc).isoformat(),
            'created_at':meta.get('created_at'),'counts':meta.get('counts'),'integrity':meta.get('integrity')})
    return sorted(items,key=lambda b:(b['mtime'],b['filename']),reverse=True)


def rotate(max_keep=None):
    with _lock:
        limits=policy();keep=min(max_keep,limits['max_count']) if max_keep else limits['max_count']
        automatic=[b for b in listing() if b['automatic']];total=0;retained=0;removed=0
        verified={b['filename']:readable(Path(b['path'])) for b in automatic}
        latest=next((b for b in automatic if verified[b['filename']]),None)
        if automatic and latest is None:
            health.observe('backup',False,'自动备份均未通过完整性检查，已保留文件，请创建新备份')
            raise ValueError('没有可读的自动备份，停止轮转以保留恢复线索')
        # Protect the newest readable copy even if a newer file was corrupted later.
        if latest:automatic=[latest]+[b for b in automatic if b['filename']!=latest['filename']]
        _,root=paths()
        for backup in automatic:
            if verified[backup['filename']] and (retained==0 or retained<keep and total+backup['size_bytes']<=limits['max_bytes']):
                total+=backup['size_bytes'];retained+=1;continue
            file=confined(Path(backup['path']),root)
            file.unlink();removed+=1
            meta=confined(file.with_suffix('.json'),root)
            if meta.is_file() and not meta.is_symlink():meta.unlink()
        return removed


def create(tag='manual',automatic=None):
    automatic=tag in AUTO_TAGS if automatic is None else automatic
    db,root=paths();root.mkdir(parents=True,exist_ok=True)
    if not db.is_file():raise FileNotFoundError('数据库尚未建立')
    with _lock:
        name='workspace_backup_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')+'_'+re.sub(r'[^a-zA-Z0-9_-]','_',tag)[:60]+'_'+uuid.uuid4().hex[:8]+'.db'
        file=confined(root/name,root);staging=confined(root/(name+'.pending'),root)
        try:
            with closing(sqlite3.connect(str(db),timeout=30)) as src:
                estimate=src.execute('PRAGMA page_count').fetchone()[0]*src.execute('PRAGMA page_size').fetchone()[0]
                if automatic and estimate>policy()['max_bytes']:raise ValueError('自动备份容量上限不足以保存当前数据库，请提高上限')
                with closing(sqlite3.connect(str(staging))) as dest:src.backup(dest)
            with closing(sqlite3.connect(str(staging))) as copied:
                integrity=copied.execute('PRAGMA integrity_check').fetchone()[0]
                if integrity!='ok':raise RuntimeError('新备份完整性校验失败')
                snapshot_counts=counts(copied);table_count=copied.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            size=staging.stat().st_size
            if automatic and size>policy()['max_bytes']:raise ValueError('自动备份超出容量上限，保留之前已验证的备份')
            staging.replace(file)
            meta={'tag':tag,'automatic':automatic,'created_at':storage.now(),'counts':snapshot_counts,'integrity':integrity,'table_count':table_count}
            file.with_suffix('.json').write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8')
            rotate()
            health.observe('backup',True,'备份已完成并通过 SQLite 完整性检查')
            return {'ok':True,'backup_filename':name,'backup_path':str(file),**meta,'size_bytes':size,'size_mb':round(size/1024**2,2)}
        except Exception:
            if staging.is_file():confined(staging,root).unlink()
            health.observe('backup',False,'备份失败，请检查磁盘和自动备份容量上限')
            raise


def selfcheck():
    with _lock:
        choices=listing()
        if not choices:raise ValueError('尚无可供自检的备份')
        backup=choices[0];db,root=paths();origin=confined(Path(backup['path']),root)
        checks=root/'.restore-check';checks.mkdir(exist_ok=True);ident=uuid.uuid4().hex;ok=False;actual={};error=None
        try:
            with tempfile.TemporaryDirectory(prefix=ident+'-',dir=str(checks)) as temp:
                target=confined(Path(temp)/'workspace.db',checks)
                if target.resolve()==db.resolve():raise ValueError('自检目标不能是正式数据库')
                with closing(sqlite3.connect(origin.as_uri()+'?mode=ro',uri=True)) as src:
                    expected=counts(src)
                    with closing(sqlite3.connect(str(target))) as dest:src.backup(dest)
                with closing(sqlite3.connect(str(target))) as restored:
                    actual=counts(restored);ok=restored.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and actual==expected
                if backup['counts'] is not None:ok=ok and actual==backup['counts']
                temp_name=Path(temp).name
        except (OSError,sqlite3.Error,ValueError):error='备份恢复或关键表检查失败'
        cleaned=not any(checks.glob(ident+'-*'))
        with storage.connect() as conn:conn.execute('INSERT INTO pm_backup_selfcheck VALUES(?,?,?,?,?,?)',(ident,backup['filename'],int(ok),json.dumps(actual),int(cleaned),storage.now()))
        health.observe('backup',ok and cleaned,'恢复自检通过，关键表记录数一致，临时副本已清理' if ok and cleaned else '恢复自检失败，请保留现有备份并检查')
        return {'ok':ok and cleaned,'backup_filename':backup['filename'],'counts':actual,'cleaned':cleaned,'temp_dir_name':locals().get('temp_name'), 'error':error}
