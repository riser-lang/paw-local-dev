"""Shared, pure projection of legacy project draft fields."""
ALIASES={'new_summary':'latest_progress_summary','update_summary':'latest_progress_summary',
    'progress':'latest_progress_summary','statement':'latest_progress_summary','current_stage':'stage',
    'next_action':'next_action_summary','next_step':'next_action_summary'}
FIELDS=('name','description','status','stage','priority','due_date',
    'next_action_summary','latest_progress_summary','project_handbook')
REVIEW_ALIASES={'current_stage':'stage','next_action':'next_action_summary',
    'summary':'description','progress':'latest_progress_summary'}


def normalize_aliases(payload,aliases=ALIASES):
    result=dict(payload)
    for source,dest in aliases.items():
        if result.get(source) not in (None,'') and result.get(dest) in (None,''):
            result[dest]=result[source]
    return result


def with_excerpt(payload,excerpt):
    result=dict(payload)
    if excerpt and not any(result.get(f) not in (None,'') for f in FIELDS if f!='name'):
        result['latest_progress_summary']=str(excerpt)
    return result
