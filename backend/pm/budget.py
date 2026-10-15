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
