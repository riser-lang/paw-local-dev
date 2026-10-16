import json
import pytest
from backend.pm import cockpit,decision_copy,storage
from tests.unit.test_versions import database
from tests.unit.test_cockpit import prepare


@pytest.mark.parametrize('kind,op,payload,expected',[
    ('todo','create',{'project_id':'p','title':'整理验收。'},'新增'),
    ('todo','complete',{'project_id':'p','id':'t'},'完成'),
    ('todo','archive',{'project_id':'p','id':'t'},'归档'),
    ('knowledge','create',{'project_id':'p','title':'技术说明。','content':'原型已交付。'},'知识'),
    ('data','create',{'project_id':'p','name':'样例指标','unit':'份','value':3},'指标'),
    ('pm_section','update',{'project_id':'p','section_id':'progress','text':'原型已交付。'},'进度与里程碑'),
])
def test_specific_candidate_outcomes_do_not_repeat_excerpt_as_question(kind,op,payload,expected):
    copy=decision_copy.candidate({'target_type':kind,'operation':op,'payload':payload},{'p':'冲刺项目'})
    assert expected in copy['if_yes'] and '【冲刺项目】' in copy['if_yes']
    assert '？' in copy['question'] and '。？' not in copy['question']
    assert '是否批准：' not in copy['question']
    assert '保持' in copy['if_no'] and copy['if_yes']!=copy['if_no']


def test_project_update_names_actual_columns_and_proposed_values(database):
    proposal={'target_type':'project','operation':'update','payload':{'id':'p',
        'latest_progress_summary':'原型已完成。','next_action_summary':'整理验收。'}}
    with storage.connect() as conn:
        prepare(conn)
        conn.execute('INSERT INTO candidate VALUES(?,?,?,?,?,?)',('c','pending',storage.now(),json.dumps(proposal),'本周完成原型。','[]'))
        before=conn.total_changes
        item=cockpit.inbox(conn)[0]
        assert conn.total_changes==before
    assert item['question']=='更新【星河项目】的进度与里程碑、下一步？'
    assert '进度与里程碑改为「原型已完成。」' in item['if_yes']
    assert '下一步改为「整理验收。」' in item['if_yes']
    assert item['excerpt']=='本周完成原型。' and item['excerpt'] not in item['question']
    assert '保持现状' in item['if_no']


@pytest.mark.parametrize('payload,expected',[
    ({'project_id':'p','new_summary':'原型已完成。'},'原型已完成。'),
    ({'project_id':'p'},'待批准的原始陈述。'),
])
def test_legacy_project_alias_and_excerpt_match_approval_projection(payload,expected):
    from backend.pm import project_payload
    before=dict(payload)
    approved=project_payload.with_excerpt(project_payload.normalize_aliases(payload),'待批准的原始陈述。')
    shown=decision_copy.candidate({'target_type':'project','operation':'update','payload':payload},{'p':'冲刺项目'},'待批准的原始陈述。')
    assert approved['latest_progress_summary']==expected
    assert '进度与里程碑改为「'+expected+'」' in shown['if_yes']
    assert shown['question']=='更新【冲刺项目】的进度与里程碑？'
    assert payload==before


def test_project_summary_alias_names_the_actual_dossier_section():
    shown=decision_copy.candidate({'target_type':'project','operation':'update',
        'payload':{'id':'p','summary':'已进入联调。'}},{'p':'冲刺项目'},'原始陈述。')
    assert '一句话现状改为「已进入联调。」' in shown['if_yes']
    assert '摘要改为' not in shown['if_yes']
