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
    def synthesize_explainer_with_ai(self, project, todos, knowledge_items, provider=None):
        from backend.pm.semantic import invoke
        provider = provider or get_active_provider()
        data = {'project': project, 'todos': todos, 'knowledge': knowledge_items}
        with storage.connect(DB_PATH) as conn:
            result = invoke('project_explainer', data, provider, conn)
        return result['output'] if result['status'] == 'draft' else None

    def analyze_projects(self, max_projects=15, force=False):
        self.last_attempted=0
        # Reads and draft writes are separated from the formal-state transaction.
        with storage.connect(DB_PATH) as conn:
            projects = [dict(r) for r in conn.execute("SELECT * FROM project WHERE status NOT IN ('merged','archived','deleted') ORDER BY updated_at DESC,id")]
        from backend.pm import suggestions,daily
        results = [];self.last_attempted=0
        start=getattr(self,'_rotation',0)%len(projects) if projects else 0
        selected=(projects[start:]+projects[:start])[:max(0,max_projects)]
        self._rotation=(start+len(selected))%len(projects) if projects else 0
        for project in selected:
            # Reserve this item before external work, including failed model attempts.
            self.last_attempted+=1
            provider=get_active_provider()
            with storage.connect(DB_PATH) as conn:
                cached=storage.derived(conn,project['id'])
                todos = [dict(r) for r in conn.execute('SELECT id,title,description,priority FROM todo WHERE project_id=? ORDER BY created_at DESC LIMIT 5', (project['id'],))]
                knowledge = [dict(r) for r in conn.execute('SELECT k.id,k.title,k.one_line_summary FROM knowledge_page k JOIN knowledge_project_link l ON l.knowledge_id=k.id WHERE l.project_id=? ORDER BY k.updated_at DESC LIMIT 5', (project['id'],))]
            attempted_explainer=bool(getattr(provider,'api_key',None)) and (force or not cached)
            value=self.synthesize_explainer_with_ai(project,todos,knowledge,provider) if force or not cached else None
            ident=cached[0]['id'] if cached else None
            if value is not None:
                with storage.connect(DB_PATH) as conn:
                    ident=storage.save_derived(conn,project['id'],value,{'project_id':project['id'],'todos':[t['id'] for t in todos],'knowledge':[k['id'] for k in knowledge]})
            advice=suggestions.generate(project['id'],provider,force=force)
            check=daily.run(project['id'],provider)
            if not attempted_explainer and advice['status'] in ('cached','needs_key','needs_evidence') and check['status']=='cached':
                self.last_attempted-=1
            if value is not None or advice.get('items'):
                results.append({'id':project['id'],'name':project['name'],'draft_id':ident,
                    'explainer':value or (cached[0]['content'] if cached else None),'advice':advice,'health_check':check,
                    'label':'AI 生成','formal_state':False})
        return results


project_analyzer = ProjectAnalyzer()
