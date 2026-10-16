"""Explain pending mutations from their payload, without a model or a write."""
from . import storage,project_payload


FIELD_LABELS={'name':'项目名称','title':'标题','description':'说明','content':'知识正文',
    'summary':'摘要','status':'状态','stage':'进度与里程碑·阶段','latest_progress_summary':'进度与里程碑',
    'next_action_summary':'下一步','due_at':'截止日期','start_at':'开始日期','unit':'单位',
    'value':'指标数值','target_value':'目标值','definition':'指标定义','priority':'优先级','due_date':'截止日期'}
STATUS_LABELS={'open':'待办','in_progress':'进行中','done':'已完成','completed':'已完成','archived':'已归档'}


def quoted(value):
    text=str(value if value is not None else '清空').strip()
    return '「'+text+'」'


def candidate(proposal,projects,raw_excerpt=None):
    payload=proposal.get('payload') or {};kind=proposal.get('target_type');op=proposal.get('operation','create')
    if kind=='project' and op=='update':
        payload=project_payload.with_excerpt(project_payload.normalize_aliases(payload),raw_excerpt)
    if kind=='project':
        payload=project_payload.normalize_aliases(payload,project_payload.REVIEW_ALIASES)
        payload={k:v for k,v in payload.items() if k not in project_payload.REVIEW_ALIASES}
    pid=payload.get('project_id') or (payload.get('id') if kind=='project' and op!='create' else None)
    name=projects.get(pid) or (payload.get('name') if kind=='project' and op=='create' else None)
    scope='【'+name+'】' if name else '工作区'
    labels=dict(FIELD_LABELS)
    if kind=='project':
        for section in storage.config()['sections']:
            for field in section.get('project_fields',[]):labels[field]=section['title']+('·阶段' if field=='stage' else '')
    excerpt=str(payload.get('text') or payload.get('title') or payload.get('content') or payload.get('description') or '')
    changes='；'.join(labels[k]+('设为' if op=='create' else '改为')+quoted(STATUS_LABELS.get(str(v),v) if k=='status' else v)
        for k,v in payload.items() if k in labels and not (op=='create' and k in ('name','title')))
    if kind=='pm_section':
        section=next((s['title'] for s in storage.config()['sections'] if s['id']==payload.get('section_id')),'指定栏目')
        question='把这条结论写入'+scope+'的'+section+'？'
        yes=scope+'的'+quoted(section)+'改为'+quoted(payload.get('text'))+'。'
        no='拒绝这条结论；'+scope+'的'+quoted(section)+'保持现状。'
    elif kind=='project':
        question=('新建'+scope+'项目？') if op=='create' else '更新'+scope+'的'+('、'.join(dict.fromkeys(labels[k] for k in payload if k in labels)) or '项目记录')+'？'
        yes=('新建'+scope+'项目；' if op=='create' else scope+'：')+(changes or '保留现有字段')+'。'
        no=('不新建该项目。' if op=='create' else '拒绝这次更新；'+scope+'的项目记录保持现状。')
    elif kind in ('todo','knowledge','data'):
        noun={'todo':'待办','knowledge':'知识','data':'指标'}[kind]
        verb={'create':'新增','update':'修改','complete':'完成','restore':'恢复','archive':'归档','revise':'修订'}.get(op,'修改')
        question='在'+scope+verb+'这条'+noun+'？'
        identity=quoted(payload.get('title') or payload.get('name')) if payload.get('title') or payload.get('name') else '所选'+noun
        yes=scope+'：'+verb+identity+('；'+changes if changes else '')+'。'
        no='不'+verb+'这条'+noun+'；'+scope+'的'+noun+'记录保持现状。'
    elif kind=='knowledge_project_link':
        linking=op=='link';question=('将这条知识关联到' if linking else '解除这条知识与')+scope+('？' if linking else '的关联？')
        yes=('新增' if linking else '解除')+'知识与'+scope+'的关联，知识正文不变。'
        no='保留当前知识关联。'
    elif kind=='project_merge':
        keeper=projects.get(payload.get('keeper_project_id'),'所选主项目')
        duplicates='、'.join(projects.get(p,'待合并项目') for p in payload.get('duplicate_project_ids',[]))
        question='将重复项目合并到【'+keeper+'】？';yes='将'+duplicates+'的记录归入【'+keeper+'】，重复项目按合并规则处理。';no='不合并；各项目及其归属保持现状。'
    else:
        question='执行这条待审核变更？';yes='执行详情中列出的'+str(kind or '未知类型')+'变更；请先查看原文和来源。';no='拒绝这条变更，正式记录保持现状。'
    return {'question':question,'if_yes':yes,'if_no':no,'project_id':pid,'excerpt':excerpt}
