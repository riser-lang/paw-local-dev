"""Standing user authorization, with explicit provenance (never impersonate a click)."""
from .versions import RULE_SOURCE
import re


def requires_user(text, section_id=''):
    return section_id=='goals' or bool(re.search(r'新建项目|新增项目|新项目|KPI|目标(?:值|定义|调整|改为|设为|提高|降低|为|是|[:：])|政策|规章|制度|绩效|考核|奖金|人选|人事|招聘|任命|人员(?:选择|调整)|赛制|赛程|竞赛规则',text,re.I))


def apply_rule(target, operation, payload, refs, rule):
    from backend.governance.committer import create_review_decision_atomic, commit_approved_change
    if (target,operation,rule) not in (('pm_version','rule','R1/R2'),('pm_section','update','R3')):
        raise ValueError('未授权的自动规则')
    if target=='pm_section' and not payload.get('assessment_id'):
        raise ValueError('自动栏目写入必须有语义判定')
    # The Committer rechecks family identity and ranking inside its writer transaction.
    _, cid = create_review_decision_atomic(None,'approve',reviewer='standing_user_rule',
        review_channel='standing_user_rule',approval_source='direct_user',
        original_payload=payload,review_notes=RULE_SOURCE+'；'+rule,
        target_type=target,operation=operation,source_refs=refs)
    result=commit_approved_change(cid)
    if result['result']!='success':
        raise ValueError(result.get('error','规则写入失败'))
    return {**result,'change_id':cid}
