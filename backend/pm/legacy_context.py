"""Size-bound the existing project endpoint without changing small responses."""
from .storage import encoded
from .budget import size


def bounded_legacy(value, budget):
    if size(value) <= budget:
        return value
    project = value['project']
    result = {
        'project': {k: v for k, v in project.items() if k not in ('description', 'project_handbook')},
        'todos': [], 'knowledge': [], 'pending': [], 'sources': [],
        'context_truncated': True, 'stage_summaries': [],
        'sources_url': '/v1/pm/projects/' + project['id'] + '/sources',
    }
    explainer = value.get('explainer') or value.get('project_explainer')
    if isinstance(explainer, dict):
        result['explainer'] = result['project_explainer'] = {k: v[:200] if isinstance(v, str) else v for k, v in explainer.items()}
    # Current facts remain clearly separated from extractive summaries of old material.
    for item in value.get('knowledge', []):
        summary = {'summary': str(item.get('one_line_summary') or item.get('title') or '')[:100],
                   'summary_kind': 'stored_excerpt', 'source_url': '/v1/web/workspace/knowledge/' + str(item['id'])}
        result['stage_summaries'].append(summary)
        if size(result) > budget:
            result['stage_summaries'].pop()
            break
    # A single oversized stored project field must not defeat the budget either.
    for key, item in list(result['project'].items()):
        if isinstance(item, str) and key != 'id' and len(item) > 200:
            result['project'][key] = item[:200]
    if size(result) > budget:
        result.pop('explainer', None)
        result.pop('project_explainer', None)
        result['stage_summaries'] = []
    if size(result) > budget:
        result['project'] = {'id':project['id'],'name':str(project.get('name',''))[:80]}
    if size(result) > budget:
        raise ValueError('上下文预算不足以保留来源索引')
    return result
