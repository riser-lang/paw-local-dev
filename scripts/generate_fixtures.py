"""Generate test fixtures for PAW test suites: docx, xlsx, pptx, pdf."""
from pathlib import Path
import docx
import openpyxl
from pptx import Presentation
from pptx.util import Inches, Pt

fixtures_dir = Path("tests/fixtures")
fixtures_dir.mkdir(parents=True, exist_ok=True)

# 1. DOCX
doc = docx.Document()
doc.add_heading("云原生治理与端侧系统升级规范", level=1)
doc.add_paragraph("项目名称：云原生数据治理工程")
doc.add_paragraph("本项目核心目标为实现云原生治理与离线端侧高保真运行，目前已完成基础架构梳理与端侧适配。")
doc.add_paragraph("重点工作：完成端到端流水线交付验收，并于今日召开系统联调推进会。")
table = doc.add_table(rows=3, cols=3)
table.cell(0, 0).text = "模块名称"
table.cell(0, 1).text = "负责人"
table.cell(0, 2).text = "当前状态"
table.cell(1, 0).text = "Reader 引擎"
table.cell(1, 1).text = "自动化测试组"
table.cell(1, 2).text = "已就绪"
table.cell(2, 0).text = "Governance 审核"
table.cell(2, 1).text = "治理服务组"
table.cell(2, 2).text = "联调中"
doc.save(str(fixtures_dir / "sample_doc.docx"))

# 2. XLSX
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "项目里程碑"
ws.append(["项目代号", "阶段", "目标描述", "截止时间", "风险等级"])
ws.append(["PAW-01", "执行中", "离线优先架构搭建与测试环境对齐", "2026-10-15", "低"])
ws.append(["PAW-02", "规划中", "端侧模型替身桥接机制实施", "2026-10-16", "中"])
ws.append(["PAW-03", "已完成", "安装包解包与字节码递归校验", "2026-10-14", "低"])
wb.save(str(fixtures_dir / "sample_table.xlsx"))

# 3. PPTX
prs = Presentation()
slide = prs.slides.add_slide(prs.slide_layouts[0])
title = slide.shapes.title
subtitle = slide.placeholders[1]
title.text = "PAW 2.5.0 架构演进与自动化验收汇报"
subtitle.text = "基于真实证据链的严肃个人工作操作系统\n汇报人：系统测试架构组"

slide2 = prs.slides.add_slide(prs.slide_layouts[1])
slide2.shapes.title.text = "核心交付要点"
tf = slide2.placeholders[1].text_frame
tf.text = "1. 离线端侧高保真解析支持 (Word, Excel, PPT, PDF)"
p2 = tf.add_paragraph()
p2.text = "2. 治理闭环：AI 提议 -> 人工审核 -> 幂等原子落盘"
p3 = tf.add_paragraph()
p3.text = "3. 待办项：组织系统回归演练，核实数据库快照机制"
prs.save(str(fixtures_dir / "sample_slides.pptx"))

# 4. Minimal valid PDF
pdf_path = fixtures_dir / "sample_report.pdf"
# Minimal standard 1-page PDF stream
pdf_bytes = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 120 >>
stream
BT
/F1 14 Tf
72 712 Td
(PAW Personal Work OS System Verification Report) Tj
0 -24 Td
(Project: Cloud Native Architecture Evolution) Tj
ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000244 00000 n 
0000000416 00000 n 
trailer
<< /Size 6 /Root 1 0 R >>
startxref
495
%%EOF
"""
pdf_path.write_bytes(pdf_bytes)

print("Generated all 4 test fixtures in tests/fixtures/")
from scripts.build_exam_corpus import build_corpus
build_corpus()
print("Generated exam corpus in tests/fixtures/exam/")
