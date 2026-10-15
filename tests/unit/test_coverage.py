import json
import pytest
from backend.pm import storage
from backend.pm.coverage import measure,record,evidence_body,save_extraction
from backend.pm.reread import enqueue
from tests.unit.test_versions import database


def test_pdf_unread_image_page_is_explicit_not_counted_as_text():
    value=measure({'reader_type':'PDF','structured_semantics':{'pages':[{'page':1,'classification':'native-text'},{'page':2,'classification':'image-only'}]}})
    assert value['ratio']==.5
    assert value['units'][1]['read'] is False and 'OCR' in value['units'][1]['reason']


def test_word_does_not_invent_physical_page_count():
    value=measure({'reader_type':'DOCX','text_blocks':[{'text':'首段'}],'tables':[{'rows':[[{'text':'单元格'}]]}]})
    assert value['total_count']==2 and value['read_count']==2
    assert {u['kind'] for u in value['units']}=={'paragraph','table'}
    assert '分页未确认' in value['limitations'][0]


def test_blank_slide_is_not_claimed_as_semantically_read():
    value=measure({'reader_type':'PPTX','structured_semantics':{'presentation':{'slide_count':2}},'text_blocks':[{'slide':1,'text':'已读'}]})
    assert value['ratio']==.5 and value['units'][1]['read'] is False


def test_reread_requires_trigger_and_is_idempotent(database):
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM pm_reread_job').fetchone()[0]==0
        with pytest.raises(ValueError,match='无条件'):
            enqueue(conn,'a','always','test')
        enqueue(conn,'a','low_coverage','a');enqueue(conn,'a','low_coverage','a')
        enqueue(conn,'b','conflict','c')
        assert conn.execute("SELECT COUNT(*) FROM pm_reread_job WHERE status='pending'").fetchone()[0]==2


def test_spreadsheet_cells_are_in_semantic_evidence_even_without_text_blocks():
    body=evidence_body({'structured_semantics':{'sheets':[{'title':'进展','cells':[{'coordinate':'A1','display_value':'已交付'}]}]}})
    assert '进展' in body and 'A1：已交付' in body


def test_reread_preserves_initial_snapshot_and_pins_new_extraction(database):
    with storage.connect() as conn:
        storage.store_source(conn,'a','资料','第一轮遗漏了表格')
        ident=save_extraction(conn,'a',{'text_blocks':[{'text':'第二轮补全资料'}]})
        assert storage.source(conn,'a')['body']=='第二轮补全资料'
        assert storage.source(conn,'a','initial')['body']=='第一轮遗漏了表格'
        assert storage.source(conn,'a',ident)['body']=='第二轮补全资料'


def test_reread_round_is_bounded_and_does_not_repeat_completed_trigger(database,monkeypatch):
    from backend.pm import reread
    processed=[]
    def perform(job):
        processed.append(job['id'])
        return {'status':'completed','reader_type':'DOCX'}
    monkeypatch.setattr(reread,'_perform',perform)
    with storage.connect() as conn:
        for index in range(13):enqueue(conn,str(index),'low_coverage',str(index))
    assert reread.run(99)['processed']==10
    assert reread.run(5)['processed']==3
    assert reread.run(5)['processed']==0
    assert len(set(processed))==13
    with storage.connect() as conn:
        assert {r[0] for r in conn.execute('SELECT attempts FROM pm_reread_job')}=={1}
