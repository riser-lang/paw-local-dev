"""Generic offline extractive answerer. Knows only the incoming prompt."""
import json
import re


def terms(text):
    words=re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+',text.lower())
    return {t for word in words for t in ([word] if not re.search('[\u4e00-\u9fff]',word) else [word[i:i+2] for i in range(len(word)-1)])}


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
                   and re.search(r'完成|启动|联调|原型|里程碑|开发|上线|交付',s)
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


def answer(prompt):
    question=prompt.rsplit('--- 用户消息 ---',1)[-1].split('【严格修复')[0].strip()
    refs={ident.split(':',1)[1]:ident for ident in re.findall(r'^- ([A-Z_]+:[^\s]+)\s+\[',prompt,re.M)}
    if not refs or '--- PAW 上下文 V2（按权威顺序）---' not in prompt:
        return '当前请求未提供可引用的资料；请补充工作台中的相关内容。'
    block=prompt.split('--- PAW 上下文 V2（按权威顺序）---',1)[1].split('--- 会话上下文 ---',1)[0].split('--- 用户消息 ---',1)[0]
    records=re.findall(r'^- \[([^\]]+)\] ([^\n]+)\n(.*?)(?=\n- \[|\n## |\Z)',block,re.M|re.S)
    query=terms(question)
    choices=[]
    for ident,title,content in records:
        if ident not in refs:
            continue
        for sentence in re.split(r'[。；\n]',content):
            sentence=sentence.strip()
            if sentence:
                score=len(query & terms(title+' '+sentence))
                choices.append((score,title,sentence,refs[ident]))
    choices.sort(key=lambda row:row[0],reverse=True)
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
        text=title+'：'+sentence
        lines.append(text)
        claims.append({'kind':'fact','text':text,'evidence':[eid]})
    if re.search(r'建议|如何|怎么|优化|下一步',question):
        basis=next((r for r in choices if re.search(r'风险|下一步|缺口|待办',r[1]+' '+r[2])),selected[0])
        advice='建议：优先核对并跟进资料所述的“'+basis[2]+'”；具体安排仍需用户确认。'
        lines.append(advice)
        claims.append({'kind':'recommendation','text':advice,'derived_from':[basis[3]]})
    return '资料摘录（非正式资料保留待确认属性）：\n'+'\n'.join(lines)+'\n<<<EVIDENCE_MAP\n'+json.dumps({'claims':claims},ensure_ascii=False)+'\nEVIDENCE_MAP>>>'
