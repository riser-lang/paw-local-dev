"""Project explanations are AI drafts. Only explicit adoption can propose formal writes."""
import json
import logging
import re
from backend.core.config import DB_PATH
from backend.provider.adapter import get_active_provider
from backend.pm import storage
from backend.pm.contracts import validate_contract

logger = logging.getLogger("paw.project_analyzer")


class ProjectAnalyzer:
    def synthesize_explainer_with_ai(self, project, todos, knowledge_items):
        from backend.pm.semantic import invoke
        provider = get_active_provider()
        data = {'project': project, 'todos': todos, 'knowledge': knowledge_items}
        with storage.connect(DB_PATH) as conn:
            result = invoke('project_explainer', data, provider, conn)
        return result['output'] if result['status'] == 'draft' else None

    def analyze_projects(self, max_projects=15, force=False):
        # Reads and draft writes are separated from the formal-state transaction.
        with storage.connect(DB_PATH) as conn:
            projects = [dict(r) for r in conn.execute("SELECT * FROM project WHERE status NOT IN ('merged','archived','deleted') ORDER BY updated_at DESC,id")]
        results = []
        for project in projects:
            if len(results) >= max_projects:
                break
            with storage.connect(DB_PATH) as conn:
                if not force and storage.derived(conn, project['id']):
                    continue
                todos = [dict(r) for r in conn.execute('SELECT id,title,description,priority FROM todo WHERE project_id=? ORDER BY created_at DESC LIMIT 5', (project['id'],))]
                knowledge = [dict(r) for r in conn.execute('SELECT k.id,k.title,k.one_line_summary FROM knowledge_page k JOIN knowledge_project_link l ON l.knowledge_id=k.id WHERE l.project_id=? ORDER BY k.updated_at DESC LIMIT 5', (project['id'],))]
            value = self.synthesize_explainer_with_ai(project, todos, knowledge)
            if value is None:
                continue
            with storage.connect(DB_PATH) as conn:
                ident = storage.save_derived(conn, project['id'], value, {'project_id':project['id'],'todos':[t['id'] for t in todos],'knowledge':[k['id'] for k in knowledge]})
            results.append({'id': project['id'], 'name': project['name'], 'draft_id': ident, 'explainer': value, 'label':'AI 生成','formal_state':False})
        return results


project_analyzer = ProjectAnalyzer()
