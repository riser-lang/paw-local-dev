import json
import sqlite3
from contextlib import closing
from datetime import datetime,timedelta,timezone
from pathlib import Path
import pytest
from backend.pm import storage,backups,health
from tests.unit.test_versions import database


@pytest.fixture(autouse=True)
def isolated(database,monkeypatch,tmp_path):
    monkeypatch.setattr(storage,'runtime_paths',lambda:(database,tmp_path/'backups'))


def test_wal_snapshot_count_limit_and_manual_preservation(database):
    with storage.connect() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
    backups.set_policy(3,8*1024**2)
    manual=backups.create('manual',False)
    for _ in range(6):backups.create('post_batch_commit')
    rows=backups.listing();automatic=[r for r in rows if r['automatic']]
    assert len(automatic)==3 and sum(r['size_bytes'] for r in automatic)<=8*1024**2
    assert Path(manual['backup_path']).is_file()
    assert len({r['filename'] for r in rows})==4
    with closing(sqlite3.connect(automatic[0]['path'])) as copied,storage.connect() as live:
        assert copied.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert backups.counts(copied)==backups.counts(live)


def test_byte_limit_rotates_even_when_count_limit_allows_more(database):
    first=backups.create('post_batch_commit')
    backups.set_policy(20,first['size_bytes']*2)
    for _ in range(5):backups.create('post_batch_commit')
    rows=[r for r in backups.listing() if r['automatic']]
    assert len(rows)<=2 and sum(r['size_bytes'] for r in rows)<=backups.policy()['max_bytes']
    with pytest.raises(ValueError,match='最新备份'):backups.set_policy(1,65536)


def test_restore_drill_verifies_counts_and_cleans_its_own_copy(database):
    original=backups.create('post_batch_commit')
    with storage.connect() as conn:before=backups.counts(conn)
    result=backups.selfcheck()
    assert result['ok'] and result['cleaned'] and result['counts']==before
    root=backups.paths()[1]
    assert not (root/'.restore-check'/result['temp_dir_name']).exists()
    assert Path(original['backup_path']).exists()
    with storage.connect() as conn:
        assert backups.counts(conn)==before
        status=next(r for r in health.read(conn,root) if r['key']=='backup')
        assert '恢复自检通过' in status['summary'] and '恢复自检：通过' in status['detail']


def test_corrupt_backup_selfcheck_fails_without_touching_formal_data(database):
    original=backups.create('manual',False)
    Path(original['backup_path']).write_bytes(b'invalid sqlite fixture')
    with storage.connect() as conn:before=backups.counts(conn)
    result=backups.selfcheck()
    assert not result['ok'] and result['cleaned']
    with storage.connect() as conn:
        assert backups.counts(conn)==before
        assert next(r for r in health.read(conn,backups.paths()[1]) if r['key']=='backup')['state']=='error'


def test_over_quota_snapshot_preserves_latest_verified_copy(database):
    original=backups.create('post_batch_commit')
    backups.set_policy(2,original['size_bytes'])
    with storage.connect() as conn:
        conn.execute('CREATE TABLE quota_fixture(value BLOB)')
        conn.execute('INSERT INTO quota_fixture VALUES(?)',(b'x'*500000,))
    with pytest.raises(ValueError,match='容量上限'):backups.create('post_batch_commit')
    assert Path(original['backup_path']).is_file()
    assert not list(backups.paths()[1].glob('*.pending'))


def test_health_records_real_success_failure_staleness_and_config_identity(database):
    identity=health.identity({'base_url':'local-test','model':'mock','api_key':'fake'})
    health.record('patrol',True,'巡航完成');health.record('scan',True,'扫描完成');health.record('model',True,'响应成功',identity)
    with storage.connect() as conn:
        last=conn.execute("SELECT last_success_at FROM pm_system_health WHERE key='scan'").fetchone()[0]
    health.record('scan',False,'扫描失败')
    with storage.connect() as conn:
        conn.execute("UPDATE pm_system_health SET last_success_at=? WHERE key='patrol'",((datetime.now(timezone.utc)-timedelta(hours=4)).isoformat(),))
        rows={r['key']:r for r in health.read(conn,backups.paths()[1].parent,identity)}
        assert rows['patrol']['state']=='error' and '3 小时' in rows['patrol']['summary']
        assert rows['scan']['state']=='error' and rows['scan']['last_success_at']==last
        assert rows['model']['state']=='ok'
        no_key={r['key']:r for r in health.read(conn,backups.paths()[1].parent,None)}
        assert no_key['model']['state']=='unknown' and 'Key' in no_key['model']['summary']


def test_backup_path_cannot_escape_managed_directory(database,tmp_path):
    with pytest.raises(ValueError,match='越界'):backups.confined(tmp_path/'outside.db',tmp_path/'backups')


def test_rotation_protects_last_readable_copy_when_newer_auto_file_is_corrupted(database):
    good=backups.create('post_batch_commit');newer=backups.create('post_batch_commit')
    Path(newer['backup_path']).write_bytes(b'corrupted later')
    backups.set_policy(1,8*1024**2)
    rows=[r for r in backups.listing() if r['automatic']]
    assert [r['filename'] for r in rows]==[good['backup_filename']]
    assert backups.readable(Path(good['backup_path']))
