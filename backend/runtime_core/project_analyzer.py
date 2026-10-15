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
        provider = get_active_provider()
        if not (getattr(provider, 'api_key', None) and hasattr(provider, 'chat_completion')):
            return None
        data = {'project': project, 'todos': todos, 'knowledge': knowledge_items}
        validate_contract('project_explainer', data, 'input')
        result, usage, error = provider.chat_completion(messages=[
            {'role':'system','content': '为项目生成30秒说明。仅基于提供的资料；未知之处写“资料不足”。返回 JSON，四个非空字符串字段：one_sentence、current_state、key_open_issue、next_step。它是 AI 生成的建议，不是正式事实，不得编造数字或决定。'},
            {'role':'user','content': storage.encoded(data)}], temperature=0.2, max_tokens=600)
        if error or not result:
            return None
        cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', result.strip())
        try:
            value = json.loads(cleaned)
            validate_contract('project_explainer', value, 'output')
            return value
        except (ValueError, TypeError):
            with storage.connect(DB_PATH) as conn:
                storage.rejection(conn, 'project_explainer', 'output_schema_invalid')
            return None

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
