"""Generic offline extractive answerer. Knows only the incoming prompt."""
import json
import re


def terms(text):
    words=re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+',text.lower())
    return {t for word in words for t in ([word] if not re.search('[\u4e00-\u9fff]',word) else [word[i:i+2] for i in range(len(word)-1)])}


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
