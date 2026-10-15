"""Reader-observed coverage, never invented page counts or OCR success."""
import json
from . import storage


def migrate(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS pm_coverage(source_version_id TEXT PRIMARY KEY,title TEXT NOT NULL,
      reader_type TEXT NOT NULL,units_json TEXT NOT NULL,read_count INTEGER NOT NULL,total_count INTEGER NOT NULL,
      ratio REAL,limitations_json TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pm_source_location(source_version_id TEXT PRIMARY KEY,path TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pm_source_extraction(id TEXT PRIMARY KEY,source_version_id TEXT NOT NULL REFERENCES pm_source(source_version_id),
      body TEXT NOT NULL,evidence_json TEXT NOT NULL,created_at TEXT NOT NULL,last_observed_at TEXT NOT NULL,UNIQUE(source_version_id,id));
    CREATE TABLE IF NOT EXISTS pm_reread_job(id TEXT PRIMARY KEY,source_version_id TEXT NOT NULL,
      reason TEXT NOT NULL,trigger_ref TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
      result_json TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
      UNIQUE(source_version_id,reason,trigger_ref));
    ''')


def evidence_body(evidence):
    parts=[str(b.get('text','')) for b in evidence.get('text_blocks',[]) if str(b.get('text','')).strip()]
    if evidence.get('tables'):parts.append(storage.encoded(evidence['tables']))
    sem=evidence.get('structured_semantics') or {}
    for sheet in sem.get('sheets',[]):
        parts.append('工作表：'+str(sheet.get('title','')))
        for cell in sheet.get('cells',[]):
            parts.append(str(cell.get('coordinate',''))+'：'+str(cell.get('display_value',cell.get('raw_value',''))))
            if cell.get('formula'):parts.append('公式：'+str(cell['formula']))
    for key in ('headers_footers','footnotes','endnotes','comments'):
        if sem.get(key):parts.append(key+'：'+storage.encoded(sem[key]))
    for slide in sem.get('slides',[]):
        if slide.get('notes'):parts.append('幻灯片备注：'+storage.encoded(slide['notes']))
    return '\n'.join(parts)


def save_extraction(conn,sid,evidence):
    import hashlib
    body=evidence_body(evidence)
    ident=hashlib.sha256((sid+'\0'+body).encode('utf-8')).hexdigest()
    timestamp=storage.now()
    conn.execute('INSERT INTO pm_source_extraction VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET last_observed_at=excluded.last_observed_at',
        (ident,sid,body,storage.encoded(evidence),timestamp,timestamp))
    return ident


def measure(evidence):
    kind=str(evidence.get('reader_type','UNKNOWN')).upper()
    sem=evidence.get('structured_semantics') or {}
    blocks=evidence.get('text_blocks') or []
    units=[];limitations=[]
    def unit(kind,ident,read,reason=''):
        units.append({'kind':kind,'id':str(ident),'read':bool(read),'reason':reason if not read else ''})
    if kind=='DOCX':
        for i,b in enumerate(blocks):unit('paragraph',i,True)
        for i,t in enumerate(evidence.get('tables') or []):unit('table',i,bool(t.get('rows')),'表格没有可读单元格')
        limitations.append('Word 实际分页未确认；覆盖率仅计已解析段落/表格，不能当作页覆盖率或图像理解率。')
    elif kind=='XLSX':
        for i,sheet in enumerate(sem.get('sheets',[])):
            unit('sheet',sheet.get('title',i),True)
            if sheet.get('cells'):unit('table',str(sheet.get('title',i))+' 单元格网格',True)
            for j,table in enumerate(sheet.get('tables',[])):unit('table',str(i)+':'+str(j),True)
            if sheet.get('images') or sheet.get('charts'):
                limitations.append('工作表 '+str(sheet.get('title',i))+' 的图表/图片只保留结构和可视证据，未声称已理解图像语义。')
        limitations.append('Excel 按工作表/结构表计数；打印分页未确认，公式缓存可能不同于实时计算值。')
    elif kind=='PPTX':
        count=sem.get('presentation',{}).get('slide_count',0)
        for number in range(1,count+1):
            readable=any(b.get('slide')==number and str(b.get('text','')).strip() for b in blocks) or any(t.get('slide')==number and t.get('rows') for t in evidence.get('tables',[]))
            unit('slide',number,readable,'该幻灯片无可提取文字/表格；图像内容尚未语义读取')
        limitations.append('幻灯片文字/表格覆盖率不代表图片、图形、动画的语义覆盖率。')
    elif kind=='PDF':
        for page in sem.get('pages',[]):
            readable=page.get('classification')=='native-text'
            unit('page',page['page'],readable,'扫描图像或空白/未解析页；未执行 OCR，不确认其语义')
    else:
        limitations.append('未能识别结构单位；覆盖率未知。')
    flags=evidence.get('fidelity_flags') or {}
    if flags.get('structure_parsed') is False or flags.get('text_layout_parsed') is False:
        units=[];limitations.append('结构读取失败，不能将结果记为完整读取。')
    limitations.extend(str(w) for w in evidence.get('warnings',[]))
    total=len(units);read=sum(u['read'] for u in units)
    return {'reader_type':kind,'units':units,'read_count':read,'total_count':total,
            'ratio':read/total if total else None,'limitations':list(dict.fromkeys(limitations))}


def record(conn,sid,title,evidence):
    measured=measure(evidence)
    conn.execute('''INSERT INTO pm_coverage VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source_version_id) DO UPDATE SET
      title=excluded.title,reader_type=excluded.reader_type,units_json=excluded.units_json,
      read_count=excluded.read_count,total_count=excluded.total_count,ratio=excluded.ratio,
      limitations_json=excluded.limitations_json,updated_at=excluded.updated_at''',
      (sid,title,measured['reader_type'],storage.encoded(measured['units']),measured['read_count'],measured['total_count'],measured['ratio'],storage.encoded(measured['limitations']),storage.now()))
    return measured


def read(conn):
    return [{**dict(r),'units':json.loads(r['units_json']),'limitations':json.loads(r['limitations_json'])}
            for r in conn.execute('SELECT * FROM pm_coverage ORDER BY updated_at DESC')]
