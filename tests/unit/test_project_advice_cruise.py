from types import SimpleNamespace
from backend.pm import storage,suggestions
from tests.unit.test_versions import database


def test_bounded_cruise_rotates_projects_and_never_holds_a_model_write_lock(database,monkeypatch):
    from backend.runtime_core import project_analyzer as module
    with storage.connect() as conn:
        conn.execute('CREATE TABLE todo(id TEXT,title TEXT,description TEXT,priority TEXT,project_id TEXT,created_at TEXT)')
        conn.execute('CREATE TABLE knowledge_page(id TEXT,title TEXT,one_line_summary TEXT,updated_at TEXT)')
        conn.execute('CREATE TABLE knowledge_project_link(knowledge_id TEXT,project_id TEXT)')
        for index in range(20):
            conn.execute("INSERT INTO project(id,name,status,updated_at) VALUES(?,?,'active','same')",(str(index),str(index)))
    visited=[]
    monkeypatch.setattr(module,'get_active_provider',lambda:SimpleNamespace(api_key='fake-unit-key'))
    def generate(pid,provider,force=False):
        with storage.connect() as other:
            storage.rejection(other,'unit','no lock')
        visited.append(pid)
        return {'status':'draft','items':[{'id':'nonformal'}]}
    monkeypatch.setattr(suggestions,'generate',generate)
    analyzer=module.ProjectAnalyzer()
    monkeypatch.setattr(analyzer,'synthesize_explainer_with_ai',lambda *args:None)
    assert analyzer.analyze_projects(0)==[] and analyzer.last_attempted==0
    for _ in range(3):
        result=analyzer.analyze_projects(10)
        assert len(result)==10 and analyzer.last_attempted==10
    assert len(set(visited[:21]))==21
    with storage.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM approved_change').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM pm_section_revision').fetchone()[0]==0
