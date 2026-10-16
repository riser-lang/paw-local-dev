import json
from backend.pm import answer_advice as advice


def contract():
    return {'advice_allowed':True,'context_items':[{'layer':'SOURCE','item_id':'a','title':'原型记录',
        'content':'原型v1已完成；李明负责复核。'}],'evidence_index':[{'evidence_id':'SOURCE:a','title':'原型记录'}]}


def raw(claims):return '任意可见文案\n<<<EVIDENCE_MAP\n'+json.dumps({'claims':claims},ensure_ascii=False)+'\nEVIDENCE_MAP>>>'


def test_remove_only_unbacked_sentences_and_keep_facts_and_supported_people():
    text=raw([{'kind':'fact','text':'原型v1已完成。','evidence':['SOURCE:a']},
        {'kind':'recommendation','text':'建议由李明负责复核。建议在987天内完成。建议由顾时安负责跟进。','derived_from':['SOURCE:a']}])
    result,stats=advice.clean(text,contract())
    visible,mapping=advice.extract(result)
    assert stats=={'removed':2,'reasons':{'uncited_number':1,'uncited_person':1}}
    assert '原型v1已完成。' in visible and '建议由李明负责复核。' in visible
    assert '987' not in result and '顾时安' not in result
    assert len(mapping['claims'])==2 and '建议（基于以上依据的推断）' in visible
    assert '〔依据：原型记录〕' in advice.display(result,contract())


def test_version_number_does_not_authorize_duration_and_tools_are_not_people():
    assert advice.unsupported('建议在1天内复核。','原型v1已完成')=='uncited_number'
    assert advice.unsupported('建议检查 Redis 的连接。','缓存需要复核') is None
    assert advice.unsupported('建议安排Alice负责复核。','原型已完成')=='uncited_person'


def test_invalid_basis_drops_derived_claim_without_creating_fake_fact_citations():
    text=raw([{'kind':'fact','text':'原型已完成。','evidence':[]},
        {'kind':'analysis','text':'建议继续。','derived_from':['UNKNOWN:a']}])
    result,stats=advice.clean(text,contract());_,mapping=advice.extract(result)
    assert stats['reasons']=={'invalid_basis':1}
    assert mapping['claims']==[{'kind':'fact','text':'原型已完成。','evidence':[]}]


def test_fact_only_and_malformed_maps_are_left_to_existing_gate():
    text=raw([{'kind':'fact','text':'原型已完成。','evidence':['SOURCE:a']}])
    assert advice.clean(text,{'advice_allowed':False})[0]==text
    malformed='原型已完成。\n<<<EVIDENCE_MAP {invalid EVIDENCE_MAP>>>'
    assert advice.clean(malformed,contract())[0]==malformed
