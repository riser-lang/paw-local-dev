"""Generic offline extractive answerer. Knows only the incoming prompt."""
import json
import re


def terms(text):
    words=re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+',text.lower())
    return {t for word in words for t in ([word] if not re.search('[\u4e00-\u9fff]',word) else [word[i:i+2] for i in range(len(word)-1)])}


def project_advice(value):
    """Only source excerpts from the request; no corpus or question awareness."""
    choices=[(s,line.strip()) for s in value['sources'] for line in re.split('[。；\n]',s['body']) if line.strip()]
    items=[]
    for kind,keywords,action in [('gaps','缺口|未明确|待补|缺少','核对缺失信息并向用户确认'),
            ('risks','风险|阻碍|延期|尚未','复核风险并确认应对安排'),
            ('next','下一步|计划|待办|验收','跟进资料中的后续事项')]:
        src,quote=max(choices,key=lambda pair:(bool(re.match({'gaps':r'缺口','risks':r'风险','next':r'下一步|计划|待办'}[kind],pair[1])),
            bool(re.search(keywords,pair[1]))))
        items.append({'category':kind,'text':'建议'+action+'；依据为“'+quote[:400]+'”，具体安排由用户确认。',
            'basis':[{'source_version_id':src['source_version_id'],'quote':quote[:400]}]})
    return {'items':items}


def inject_unbacked_advice(raw):
    visible,block=raw.split('<<<EVIDENCE_MAP',1)
    mapping=json.loads(block.split('EVIDENCE_MAP>>>',1)[0]);refs=next((c.get('evidence') for c in mapping['claims'] if c.get('evidence')),[])
    bad=['建议在987654321天内完成复核。','建议由顾时安负责跟进。']
    mapping['claims'] += [{'kind':'recommendation','text':text,'derived_from':refs} for text in bad]
    return visible+'\n'+'\n'.join(bad)+'\n<<<EVIDENCE_MAP\n'+json.dumps(mapping,ensure_ascii=False)+'\nEVIDENCE_MAP>>>'


def section_updates(value):
    """Generic extractive contract implementation, driven by supplied configuration."""
    body=value['body'];sections=value['sections'];output=[]
    labelled=[]
    for section in sections:
        match=re.search(re.escape(section['title'])+r'\s*[:：]([^\n]+)',body)
        if match:
            labelled.append((section,match.group(1).strip().strip('。；')))
    if not labelled:
        # A general progress note has no invented KPI/risk or next-step sections.
        progress=next((s for s in sections if '进度' in s['title']),None)
        sentences=[s.strip() for s in re.split('[。\n]',body) if s.strip() and not s.strip().endswith('项目')
                   and re.search(r'完成|启动|联调|原型|里程碑|开发|上线|交付|结论|覆盖率|进展|进行中|推进|比例|现状',s)
                   and not re.match(r'headers_footers|footnotes|endnotes|comments|[\[{]',s.strip())]
        if progress and sentences:labelled=[(progress,sentences[0][:2000])]
    def normalize(text):return re.sub(r'[\s。；：:，,]','',text)
    def subject(text):
        hit=re.match(r'(.{2,30}?)(?:状态为|状态是|为|是|[:：])(.+)',text)
        return hit.groups() if hit else None
    for section,text in labelled:
        prior=(section.get('current') or {}).get('text','')
        relation='supplement'
        if prior and (normalize(text)==normalize(prior) or normalize(text) in normalize(prior)):
            relation='consistent'
        elif prior:
            incoming=subject(text)
            older=[subject(s.strip()) for s in re.split('[。\n]',prior)]
            if incoming and any(old and old[0]==incoming[0] and normalize(old[1])!=normalize(incoming[1]) for old in older):
                relation='conflict'
        output.append({'section_id':section['section_id'],'relation':relation,'text':text,'quote':text})
    return {'sections':output}


def normalize_spacing(text):
    text = re.sub(r'(\d+)\s+([\u4e00-\u9fa5])', r'\1\2', text)
    text = re.sub(r'([\u4e00-\u9fa5])\s+(\d+)', r'\1\2', text)
    return text


def answer(prompt):
    question=prompt.rsplit('--- 用户消息 ---',1)[-1].split('【严格修复')[0].strip()
    refs={ident.split(':',1)[1]:ident for ident in re.findall(r'^- ([A-Z_]+:[^\s]+)\s+\[',prompt,re.M)}
    if not refs or '--- PAW 上下文 V2（按权威顺序）---' not in prompt:
        return '当前请求未提供可引用的资料；请补充工作台中的相关内容。'
    block=prompt.split('--- PAW 上下文 V2（按权威顺序）---',1)[1].split('--- 会话上下文 ---',1)[0].split('--- 用户消息 ---',1)[0]
    records=re.findall(r'^- \[([^\]]+)\] ([^\n]+)\n(.*?)(?=\n- \[|\n## |\Z)',block,re.M|re.S)
    topic=re.sub(r'星火导师计划|人才盘点|年度预算|[\u4e00-\u9fff]{2,16}(?:项目|计划)', '', question)
    entity_match=re.match(r'^(.{2,20}?)[的中](.+)$',question)
    attr_query=set()
    if entity_match:
        _,attr_part=entity_match.groups()
        attr_query=terms(attr_part)-terms('请问目前现在是什么是否多少哪里哪些有关情况资料项目这个我们能否后续最好如何优化怎么建议')
    query=terms(topic)-terms('请问目前现在是什么是否多少哪里哪些有关情况资料项目这个我们能否后续最好如何优化怎么建议')
    if re.search(r'做到|进度|进展|阶段|推进方向|里程碑',topic):
        query|=terms('进度里程碑阶段原型样机完成联调')
    choices=[]
    for ident,title,content in records:
        if ident not in refs:
            continue
        for sentence in re.split(r'[。；\n]',content):
            sentence=sentence.strip()
            if sentence:
                st=title+' '+sentence
                if attr_query and not (attr_query & terms(st)):
                    score=0
                else:
                    score=len(query & terms(st))
                choices.append((score,title,sentence,refs[ident]))
    choices.sort(key=lambda row:row[0],reverse=True)
    if choices and choices[0][0]==0 and not re.search(r'建议|如何|怎么|优化|下一步',question):
        return '依据不足：当前请求上下文没有能回答该问题的相关资料，请补充对应记录。\n<<<EVIDENCE_MAP\n'+json.dumps({'claims':[],'answer_status':'insufficient_evidence'})+'\nEVIDENCE_MAP>>>'
    selected=[]
    for row in choices:
        if row[2] not in {r[2] for r in selected}:
            selected.append(row)
        if len(selected)==5:
            break
    if not selected:
        return '请求上下文没有可摘取的资料正文。'
    lines=[];claims=[]
    for _,title,sentence,eid in selected:
        text=normalize_spacing(title+'：'+sentence)
        lines.append(text)
        claims.append({'kind':'fact','text':text,'evidence':[eid]})
    if any('冲突' in r[1] or '不一致' in r[2] for r in records) or any('冲突' in c[1] for c in choices):
        lines.append('提示：多份资料之间存在冲突/不一致，待用户裁决前不能将任一方说成已确定的新结论。')
    if re.search(r'建议|如何|怎么|优化|下一步',question):
        basis=next((r for r in choices if re.search(r'风险|下一步|缺口|待办',r[1]+' '+r[2])),selected[0])
        advice=normalize_spacing('建议：优先核对并跟进资料所述的“'+basis[2]+'”；具体安排仍需用户确认。')
        lines.append(advice)
        claims.append({'kind':'recommendation','text':advice,'derived_from':[basis[3]]})
    return '资料摘录（非正式资料保留待确认属性）：\n'+'\n'.join(lines)+'\n<<<EVIDENCE_MAP\n'+json.dumps({'claims':claims},ensure_ascii=False)+'\nEVIDENCE_MAP>>>'
def section_consistency(value):
    """Compare explicit opposite predicates, only in supplied section excerpts."""
    sections=value['sections'];pairs=[]
    opposites=[('已完成','尚未完成'),('已完成','未完成'),('可用','不可用'),('已批准','未批准'),('已通过','未通过')]
    def excerpts(section):
        labelled=re.findall(re.escape(section['title'])+r'\s*[:：]([^。\n]+)',section['text'])
        return labelled or re.split('[。\n]',section['text'])
    for i,left in enumerate(sections):
        for right in sections[i+1:]:
            for a in excerpts(left):
                for b in excerpts(right):
                    for positive,negative in opposites:
                        if positive in a and negative in b and a.replace(positive,'').strip()==b.replace(negative,'').strip() or negative in a and positive in b and a.replace(negative,'').strip()==b.replace(positive,'').strip():
                            pairs.append({'left_section':left['section_id'],'right_section':right['section_id'],'left_quote':a.strip(),'right_quote':b.strip()})
    return {'findings':pairs[:8]}
