"""C-08: pure, sentence-local treatment of derived advice."""
import json
import re
from collections import Counter

OPEN='<<<EVIDENCE_MAP';CLOSE='EVIDENCE_MAP>>>'
DERIVED={'analysis','recommendation','suggestion','inference'}
NUMBERS=re.compile(r'\d+(?:\.\d+)?\s*(?:万元|小时|分钟|%|％|天|周|月|年|人|万|元|次|个|条|份)?|[一二三四五六七八九十百千万两]+(?:万元|小时|分钟|天|周|月|年|人|元|次|个|条|份)')
PEOPLE=re.compile(r'(?:建议(?:由|让|安排|指定)?|由|让|交给|指定|安排|邀请|请)([\u4e00-\u9fff]{2,4}?)(?:来)?(?:负责|跟进|处理|接手|牵头|承担|参与)|(?:负责人|联系人|顾问)[：:\s]*([\u4e00-\u9fff]{2,4})(?=[，。；\s]|$)|(?:由|让|请|安排|指定|负责人[：:]?)\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)(?=\s*(?:负责|跟进|接手|牵头))|(?i:assign|ask|appoint|invite)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)(?=\s+(?:to|as))')
ROLES={'用户','团队','项目组','现有团队','负责人','相关人员','相关团队'}


def extract(raw):
    match=re.search(re.escape(OPEN)+r'\s*(.*?)\s*'+re.escape(CLOSE),raw or '',re.S)
    if not match:return raw or '',None
    try:return (raw[:match.start()]+raw[match.end():]).strip(),json.loads(match[1])
    except (ValueError,TypeError):return raw or '',None


def tokens(text):return {re.sub(r'\s+','',n).replace('％','%') for n in NUMBERS.findall(text)}


def unsupported(text,basis):
    if tokens(text)-tokens(basis):return 'uncited_number'
    compact=re.sub(r'\s+','',basis)
    names={name for groups in PEOPLE.findall(text) for name in groups if name}
    if any(name not in ROLES and re.sub(r'\s+','',name) not in compact for name in names):return 'uncited_person'
    if re.search(r'(用户|团队|项目|系统).{0,8}已(?:批准|决定|确认采用|确定采用)',text):return 'advice_as_decision'
    return None


def clean(raw,contract,kind_of=None):
    if not contract.get('advice_allowed'):return raw,{'removed':0,'reasons':{}}
    visible,mapping=extract(raw)
    if not isinstance(mapping,dict) or not isinstance(mapping.get('claims'),list):
        return raw,{'removed':0,'reasons':{},'status':'unverified_map'}
    context={str(i['layer'])+':'+str(i['item_id']):str(i.get('title',''))+'\n'+str(i.get('content','')) for i in contract.get('context_items',[])}
    known={i['evidence_id'] for i in contract.get('evidence_index',[])}
    facts=[];advice=[];reasons=Counter()
    for claim in mapping['claims']:
        if not isinstance(claim,dict) or not isinstance(claim.get('text'),str):
            return raw,{'removed':0,'reasons':{},'status':'unverified_map'}
        kind=kind_of(claim) if kind_of else claim.get('kind','fact')
        if kind not in DERIVED:
            facts.append({**claim,'kind':'fact'});continue
        refs=claim.get('derived_from') or claim.get('evidence') or []
        if not isinstance(refs,list) or not refs or any(not isinstance(r,str) or r not in known for r in refs):
            reasons['invalid_basis']+=1;continue
        basis='\n'.join(context.get(r,'') for r in refs)
        for sentence in re.split(r'(?<=[。！？])\s*|\n+',claim['text']):
            sentence=sentence.strip()
            if not sentence:continue
            reason=unsupported(sentence,basis)
            if reason:reasons[reason]+=1
            else:advice.append({**claim,'kind':'analysis' if kind=='analysis' else 'recommendation','text':sentence,'derived_from':refs})
    # Facts are not made acceptable here: the existing grounding gate checks
    # every retained fact and its original references after this projection.
    lines=[c['text'] for c in facts]
    if advice:lines+=['建议（基于以上依据的推断）']+[c['text'] for c in advice]
    elif reasons:lines+=['建议部分未能逐句核对依据，已保留可核对的事实。']
    if not lines:
        # Nothing survived the projection.  An answer that reports insufficient
        # evidence carries no claims by design, so the prose must be preserved
        # rather than rebuilt from claims; dropping it would erase the model's
        # "依据不足" statement and fail the turn closed on an empty answer.
        body=visible.strip()
        if body:
            return (body+'\n'+OPEN+'\n'+json.dumps(mapping,ensure_ascii=False)+'\n'+CLOSE,
                    {'removed':sum(reasons.values()),'reasons':dict(reasons)})
    output='\n'.join(lines)+'\n'+OPEN+'\n'+json.dumps({**mapping,'claims':facts+advice},ensure_ascii=False)+'\n'+CLOSE
    return output,{'removed':sum(reasons.values()),'reasons':dict(reasons)}


def display(raw,contract):
    visible,mapping=extract(raw)
    if not contract.get('advice_allowed') or not isinstance(mapping,dict):return visible
    index={i['evidence_id']:i for i in contract.get('evidence_index',[])}
    for claim in mapping.get('claims',[]):
        if claim.get('kind','fact') in DERIVED:continue
        titles=list(dict.fromkeys(index[r]['title'] for r in claim.get('evidence',[]) if r in index))
        if titles:visible=visible.replace(claim['text'],claim['text']+'〔依据：'+'、'.join(titles)+'〕',1)
    return visible
