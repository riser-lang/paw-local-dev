from types import SimpleNamespace
from backend.search.assistant_bridge import evaluate_search_trigger
from backend.search.service import SearchService
from backend.pm.answer_timing import measured,current
from scripts.mock_grounded_answer import answer


def test_internal_document_zero_evidence_cannot_start_web_search():
    settings=SimpleNamespace(web_search_mode='auto')
    allowed,reason=evaluate_search_trigger('周报中规划的核心业务推进目标是什么？','custom',0,settings)
    assert not allowed and reason=='workspace_evidence_or_document_question'
    assert not evaluate_search_trigger('介绍当前进展','custom',3,settings)[0]


def test_fallback_backends_share_one_deadline(monkeypatch):
    clock=[100.0];budgets=[]
    class Backend:
        name='slow'
        def search(self,request):
            budgets.append(request.timeout_seconds)
            clock[0]+=6
            return []
    monkeypatch.setattr('backend.search.service.time.monotonic',lambda:clock[0])
    result=SearchService(backends=[Backend(),Backend(),Backend()]).search('虚构查询',timeout_seconds=8)
    assert budgets==[8,2]
    assert result['fallback_trace'][-1]=='total_search_deadline'


def test_numeric_timing_attaches_to_result_without_prompt():
    @measured
    def run():
        current.get()['model_calls']+=1
        return {'response':'未公开内容'}
    response=run()
    assert response['timings']['model_calls']==1
    assert 'response' not in response['timings']
    assert current.get() is None


def test_generic_mock_selects_context_and_never_fabricates_document():
    prompt='可用的证据 id：\n- CONFIRMED_KNOWLEDGE:s1 [EVIDENCE] 虚构资料\n--- PAW 上下文 V2（按权威顺序）---\n- [s1] 自拟资料\n  温室已完成浇水。风险是缺少遮阳网。\n--- 用户消息 ---\n如何处理温室风险？'
    result=answer(prompt)
    assert '缺少遮阳网' in result and '建议：' in result
    assert 'CONFIRMED_KNOWLEDGE:s1' in result
    assert 'sample_doc' not in result and '云原生' not in result
