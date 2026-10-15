"""Bound the entire wire JSON in UTF-8; original evidence remains separately readable."""
from .storage import encoded


def size(value):
    return len(encoded(value).encode('utf-8'))


def bounded_context(dossier, budget):
    if size(dossier) <= budget:
        return dossier
    result = {'project_id': dossier['project']['id'], 'truncated': True,
              'sources_url': '/v1/pm/projects/' + dossier['project']['id'] + '/sources', 'stage_summaries': []}
    if size(result) > budget:
        raise ValueError('预算不足以保留来源索引')
    for section in dossier['sections']:
        # Extractive stage summaries, never invented semantic summaries.
        for revision in ([section['current']] if section['current'] else []) + section['history']:
            item = {'section_id': section['id'], 'summary': revision['text'][:100],
                    'source_url': revision['source_url'], 'summary_kind': 'stored_excerpt'}
            result['stage_summaries'].append(item)
            if size(result) > budget:
                result['stage_summaries'].pop()
                return result
    return result


def model_messages(system, value, budget):
    """Include schema/instructions and JSON escaping in the model-input budget."""
    protected = {'id','project_id','source_version_id','source_url','url','change_id','revision_id','section_id'}
    def messages(data):
        return [{'role':'system','content':system}, {'role':'user','content':encoded(data)}]
    full = messages(value)
    if size(full) <= budget:
        return full
    def excerpt(item, chars, count, key=''):
        if isinstance(item, str) and key not in protected and len(item) > chars:
            return item[:chars] + '…（阶段节选）'
        if isinstance(item, list):
            return [excerpt(v,chars,count) for v in item[:count]]
        if isinstance(item, dict):
            return {k:excerpt(v,chars,count,k) for k,v in item.items()}
        return item
    for chars,count in ((400,8),(160,5),(64,3),(16,1)):
        result = messages(excerpt(value,chars,count))
        if size(result) <= budget:
            return result
    raise ValueError('上下文预算不足以保留任务契约和来源索引')
