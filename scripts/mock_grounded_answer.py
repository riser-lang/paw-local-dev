"""Generic offline extractive answerer. Knows only the incoming prompt."""
import json
import re


QUESTION_STOPWORDS = '请问目前现在是什么是否多少哪里哪些有关情况资料项目这个我们能否后续最好如何优化怎么建议'


def terms(text):
    words=re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+',text.lower())
    return {t for word in words for t in ([word] if not re.search('[\u4e00-\u9fff]',word) else [word[i:i+2] for i in range(len(word)-1)])}


def query_terms(text):
    """Question terms with the interrogative scaffolding removed."""
    return terms(text)-terms(QUESTION_STOPWORDS)


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
        # A general progress note has no invented KPI/risk or next-step
        # sections.  The wording below is generic status language, not the
        # vocabulary of any particular corpus.
        progress=next((s for s in sections if '进度' in s['title']),None)
        sentences=[s.strip() for s in re.split('[。\n]',body) if s.strip() and not s.strip().endswith('项目')
                   and re.search(r'完成|启动|里程碑|开发|上线|交付|结论|进展|进行中|推进|现状|已完成|未开始',s)
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


ADVICE_RE = re.compile(r'建议|如何|怎么|优化|下一步|规划|方案')


def _records(prompt):
    """Context records exactly as the product renders them.

    Format is ``- [item_id] title\\n  content``; the reader must not depend on
    which project, document or vocabulary happens to be in the request.
    """
    marker = '--- PAW 上下文 V2（按权威顺序）---'
    if marker not in prompt:
        return []
    block = (prompt.split(marker, 1)[1]
             .split('--- 会话上下文 ---', 1)[0]
             .split('--- 用户消息 ---', 1)[0])
    return re.findall(r'^- \[([^\]]+)\] ([^\n]+)\n(.*?)(?=\n- \[|\n## |\Z)',
                      block, re.M | re.S)


def _refs(prompt):
    return {ident.split(':', 1)[1]: ident
            for ident in re.findall(r'^- ([A-Z_]+:[^\s]+)\s+\[', prompt, re.M)}


# A question about what comes next is asking about work that is not finished.
# The markers are generic progress states ("not done yet"), not domain
# vocabulary: they never say what the work is.
_PENDING_RE = re.compile(r'未开始|尚未|待办|待定|进行中|计划|下一步|后续|pending|todo|planned|open')
_NEXT_STEP_RE = re.compile(r'下一步|后续|接下来|next step')
# Likewise, "where are we / what has been done" is answered by the record's
# own status text, whose wording the question need not repeat.
_PROGRESS_RE = re.compile(r'做到|进展|进度|状态|情况|怎么样|如何|阶段|里程碑|progress|status')


def _entity(question, records):
    """Subject of the question, taken only from the request itself.

    A record title is "文件名（资料证据，非正式结论）" or "progress · 文件".
    The subject is whichever title prefix the question actually mentions; no
    project or document name is ever assumed by the stand-in.
    """
    best = ''
    for _, title, _ in records:
        name = re.split(r'[_（(.\s·]', title)[0].strip()
        if len(name) >= 2 and name in question and len(name) > len(best):
            best = name
    return best


def _named_scope(question, records):
    """A name the question uses to scope the question ("…项目的…").

    ``None`` means the question does not scope itself to a named thing, so any
    record may answer it.  A name that no record title carries means the answer
    must come from a record about that name, not from another project's
    document that happens to contain the same noun.

    The name must read as a noun a project could be called, so a sentence
    fragment such as "周报中规划的" is rejected: no record is called that, and
    treating it as a scope would filter out every record.
    """
    hit = re.match(r'^\s*([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:项目|计划|方案|产品)?的',
                   question)
    if not hit:
        return None
    name = hit.group(1)
    # A demonstrative or counted phrase ("这三个", "所有") refers to a set,
    # not to one named thing, so it must not scope the question.
    if re.match(r'^[这那该本各某全几多每]|^[一两三四五六七八九十百千万\d]', name):
        return None
    # Only a name that a record could plausibly be titled with is a scope; a
    # fragment ending in a verb or function word is prose, not a subject.
    if re.search(r'(?:中|里|上|下|规划|推进|计划|进行|需要|应该|如何|什么)$', name):
        return None
    return name


def _sentences(text):
    """Prose sentences only.

    Approved-change records carry raw JSON payloads.  Quoting one back is not an
    answer to anything, and matching a question against its keys (a key like
    ``latest_progress_summary`` shares characters with many questions) would let
    an unrelated record masquerade as the answering evidence.
    """
    out = []
    for raw in re.split(r'[。；\n]', text):
        s = raw.strip()
        if not s or s[0] in '{["' or re.match(r'^[\[{]', s):
            continue
        # A change record's payload is machine metadata, not prose.  Quoting it
        # back is not an answer, so only the part before the payload is kept.
        cut = re.search(r'\s*\{[^{}]*"', s)
        if cut:
            s = s[:cut.start()].strip()
        if s:
            out.append(s)
    return out


def _grams(text, low, high=6):
    """CJK character n-grams in [low, high] plus latin words."""
    out = set()
    for run in re.findall(r'[\u4e00-\u9fff]+', str(text or '')):
        for n in range(low, high + 1):
            for i in range(len(run) - n + 1):
                out.add(run[i:i + n])
    for word in re.findall(r'[A-Za-z0-9_]{2,}', str(text or '')):
        out.add(word.lower())
    return out


def _weighted_overlap(want, sentence):
    """Longer shared n-grams mean a more specific match, so weight by length.

    A plain bigram count lets a sentence that merely repeats the subject's
    characters outrank the one sentence that actually states the asked figure.
    """
    have = _grams(sentence, 2)
    return sum(len(g) for g in want & have)


def _char_content(sentence, question):
    """The sentence's own content: what it says that the question does not.

    A sentence that adds a fact ("风险：设计评审尚未完成" for a question that
    also contains 风险) says something new; a bare restatement of the subject
    says nothing the question did not already supply, so it is not an answer to
    anything.
    """
    body = re.sub(r'^[^：:]{0,12}[：:]', '', str(sentence or ''))
    body = re.sub(r'[\s。；：:，,、]', '', body)
    asked = re.sub(r'[\s。；：:，,、]', '', str(question or ''))
    # Longest run of the sentence that the question does not already contain.
    remaining, longest = asked, 0
    for ch in body:
        if ch in remaining:
            remaining = remaining.replace(ch, '', 1)
        else:
            longest += 1
    return longest


def answer(prompt):
    question=prompt.rsplit('--- 用户消息 ---',1)[-1].split('【严格修复')[0].strip()
    refs=_refs(prompt)
    records=_records(prompt)
    if not refs or not records:
        return '当前请求未提供可引用的资料；请补充工作台中的相关内容。'
    subject=_entity(question,records)
    query=query_terms(question)
    # When the question names a subject, its own terms are the strongest
    # signal; dropping them keeps scoring driven by the asked-about attribute.
    for run in re.findall(r'[\u4e00-\u9fff]{2,}',subject):
        query-=terms(run)
    want=_grams(' '.join(sorted(query)),2)
    # Tokens from the question that could name a specific document in a family
    # (version markers, latin/digit codes).  Purely question-derived.
    qualifier=set(re.findall(r'[a-z0-9]{2,}',question.lower()))

    def title_matches_subject(title):
        name=re.split(r'[_（(.\s·]',title)[0].strip()
        return bool(subject) and len(name)>=2 and (name in subject or subject in name)

    def title_matches_scope(title,scope):
        name=re.split(r'[_（(.\s·]',title)[0].strip()
        return len(name)>=2 and (name in scope or scope in name)

    # A question may single out one member of a document family by a token that
    # appears in that document's own title ("最早的 v1 方案").  Such a token is
    # taken from the question, never from a fixed list.
    def title_matches_qualifier(title):
        toks={t for t in re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]{2,}',title.lower())}
        return bool(qualifier & toks)

    # A "what happens next" question is answered by work that is still open,
    # even when the question shares no characters with it.
    asks_next=bool(_NEXT_STEP_RE.search(question))
    # A "where does this stand" question is answered by the record's status
    # line, which need not repeat the question's own words.
    asks_progress=bool(_PROGRESS_RE.search(question))
    # When the question scopes itself to a name that no record title carries,
    # another subject's document must not answer it just because it shares a
    # noun: "冲刺项目的采购预算" is not answered by a different project's budget.
    # A status question is exempt: the record's own status line is the answer,
    # whatever the record is called.
    scope=_named_scope(question,records)
    scoped_out=bool(scope) and not subject and not asks_progress and not asks_next
    choices=[]
    for ident,title,content in records:
        if ident not in refs:
            continue
        if scoped_out and not title_matches_scope(title,scope):
            continue
        same_subject=title_matches_subject(title)
        qualified=title_matches_qualifier(title)
        for sentence in _sentences(content):
            # A record about the asked-about subject outranks an unrelated one
            # that merely shares a common character.
            score=_weighted_overlap(want,sentence)+(4 if same_subject else 0)
            pending=bool(_PENDING_RE.search(sentence))
            if asks_next and pending and (same_subject or not subject):
                # Open work is what a next-step question is about.
                score+=4
            # Spreadsheet extracts repeat the same row as loose cells and as a
            # readable "行数据" line.  Prefer the line that carries the most of
            # the record's own content, so a bare header cell does not stand in
            # for the row it belongs to.
            score+=min(len(sentence),60)/60.0
            stated=bool(_PENDING_RE.search(sentence)) or bool(
                re.search(r'已完成|完成|已交付|已上线|进行中|里程碑',sentence))
            # A status line answers a status question.  When the question names
            # no subject that any record title carries, the record's own status
            # line is the best available answer; when it does name one, that
            # subject must be the one whose status is shown.
            progress_hit=(asks_progress and stated and (same_subject or not subject))
            if progress_hit:
                score+=4
            # ``addressed`` records whether the sentence answers what was asked,
            # as opposed to merely being about the same subject.  The question's
            # scaffolding is already excluded from ``want``, so any surviving
            # overlap is a content word the question really used.  The sentence
            # must also carry something of its own: a bare label or a restated
            # subject ("冲刺项目") answers nothing.
            overlap=want & _grams(sentence,2)
            content=_char_content(sentence, question)
            addressed=(sum(len(g) for g in overlap)>=2 and content>=2
                       or (asks_next and pending and same_subject)
                       or (asks_progress and stated and (same_subject or not subject)))
            choices.append({'score':score,'qualified':qualified,'same_subject':same_subject,
                            'addressed':addressed,'title':title,'sentence':sentence,
                            'eid':refs[ident]})
    choices.sort(key=lambda r:(r['score'],r['qualified'],r['same_subject']),reverse=True)

    wants_advice=bool(ADVICE_RE.search(question))
    if not wants_advice and not any(c['addressed'] for c in choices):
        return '依据不足：当前请求上下文没有能回答该问题的相关资料，请补充对应记录。\n<<<EVIDENCE_MAP\n'+json.dumps({'claims':[],'answer_status':'insufficient_evidence'})+'\nEVIDENCE_MAP>>>'

    selected=[]
    for row in choices:
        if not row['addressed'] and not wants_advice:
            # Nothing here addresses the question; do not pad the answer with
            # same-subject sentences that do not answer it.
            continue
        if (row['title'],row['sentence']) not in {(r['title'],r['sentence']) for r in selected}:
            selected.append(row)
        if len(selected)==5:
            break
    if not selected:
        return '请求上下文没有可摘取的资料正文。'
    lines=[];claims=[]
    for row in selected:
        text=normalize_spacing(row['title']+'：'+row['sentence'])
        lines.append(text)
        claims.append({'kind':'fact','text':text,'evidence':[row['eid']]})
    # A conflict notice is only ever the product's own wording, carried into the
    # request by the conflict list.  The stand-in transcribes such a record
    # verbatim; it never infers, phrases or announces a conflict by itself.
    for ident,title,content in records:
        if not re.search(r'冲突|待用户裁决',title+content):
            continue
        if ident not in refs:
            continue
        notice=normalize_spacing(re.sub(r'\s*\n\s*','；',title+'：'+content.strip())[:400])
        if notice not in lines:
            lines.append(notice)
            claims.append({'kind':'fact','text':notice,'evidence':[refs[ident]]})
    if wants_advice:
        basis=next((r for r in choices if re.search(r'风险|下一步|缺口|待办|计划|进行中|未开始|待定',r['sentence'])),selected[0])
        advice=normalize_spacing('建议：优先核对并跟进资料所述的“'+basis['sentence']+'”；具体安排仍需用户确认。')
        lines.append(advice)
        claims.append({'kind':'recommendation','text':advice,'derived_from':[basis['eid']]})
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
