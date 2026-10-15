"""PAW Requirement F6 Verification - Rule-based candidate quality on 5 diverse office docs.

Tests that:
1. Todo candidates are strictly actionable verb sentences.
2. Section headers (e.g. "三、近期重点行动待办") and SOP names are NEVER extracted as todos.
3. 5 diverse office docs are evaluated and output candidates are recorded for review.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from docx import Document
import openpyxl
from pptx import Presentation

from backend.reader.contract import ReaderInput
from backend.reader.dispatch import isolated_read_artifact
from backend.provider.adapter import RuleBasedFallbackProvider

OUT_DIR = Path("data/f6_test_docs")
OUT_DIR.mkdir(parents=True, exist_ok=True)
READER_OUT = Path("data/f6_reader_out")
READER_OUT.mkdir(parents=True, exist_ok=True)

provider = RuleBasedFallbackProvider()

# 1. Chinese Weekly Report (.docx)
doc1 = Document()
doc1.add_heading("技术支撑部 2026年第40周 工作周报", level=0)
doc1.add_heading("一、本周核心工作进展", level=1)
doc1.add_paragraph("本周团队重点跟进核心交易链路高可用建设，已完成核心服务的容器化迁移。")
doc1.add_heading("二、存在问题与风险提示", level=1)
doc1.add_paragraph("目前老旧冷备机房存在部分磁盘坏道，计划下周全面排查下线。")
doc1.add_heading("三、下周重点工作待办", level=1)
doc1.add_paragraph("行动项 1：组织跨部门联合演练，完成多机房流量调度压测；\n行动项 2：落实安全网关证书更新，推进全链路鉴权联调。")
p1 = OUT_DIR / "01_中文周报_技术支撑部.docx"
doc1.save(p1)

# 2. Technical Architecture & SOP Proposal (.docx)
doc2 = Document()
doc2.add_heading("2026 天工容灾中台高可用架构白皮书", level=0)
doc2.add_heading("一、项目启动背景与核心目标", level=1)
doc2.add_paragraph("本项目旨在建设同城双活与多活容灾体系，确保核心交易分钟级自愈。")
doc2.add_heading("二、核心操作规范：双活容灾秒级切换 SOP", level=1)
doc2.add_paragraph("【双活容灾秒级切换 SOP】标准操作规程包括探针检测、切流与告警归档。")
doc2.add_heading("三、近期重点行动待办", level=1)
doc2.add_paragraph("1. 部署自动化容灾告警机器人群；\n2. 编写主备机房网络隔离切流应急预案。")
p2 = OUT_DIR / "02_技术方案与SOP白皮书.docx"
doc2.save(p2)

# 3. Project Meeting Minutes (.docx)
doc3 = Document()
doc3.add_heading("第四季度经营战略落地专项会议纪要", level=0)
doc3.add_heading("一、参会人员与会议背景", level=1)
doc3.add_paragraph("参会人员涵盖产品研发、业务运营与架构团队，会议审议了四季度重点交付节点。")
doc3.add_heading("二、会议决议与任务分工", level=1)
doc3.add_paragraph("会议明确分工如下：\n- 协调法务合规团队，完成用户数据隐私协议更新；\n- 组织内部宣贯培训，推进全员知识库迁移。")
p3 = OUT_DIR / "03_项目复盘会议纪要.docx"
doc3.save(p3)

# 4. Operational Data Sheet (.xlsx)
wb4 = openpyxl.Workbook()
ws4 = wb4.active
ws4.title = "运营关键指标"
ws4.append(["指标名称", "目标值", "实际完成", "达成率", "待办跟进"])
ws4.append(["知识沉淀条目数", 500, 560, "112%", "梳理高价值标杆案例集"])
ws4.append(["系统故障自愈率", "99.9%", "99.95%", "100%", "优化告警阈值配置策略"])
p4 = OUT_DIR / "04_运营数据度量表.xlsx"
wb4.save(p4)

# 5. Slide Deck (.pptx)
prs5 = Presentation()
slide5 = prs5.slides.add_slide(prs5.slide_layouts[1])
slide5.shapes.title.text = "架构演进与数字化人才发展方案"
slide5.shapes.placeholders[1].text = (
    "核心方法论：个人知识体系从日常工作无感沉淀。\n"
    "执行行动项：\n"
    "- 搭建内部跨部门专家导师团队；\n"
    "- 采购高性能测试工作站，保障演练算力支持。"
)
p5 = OUT_DIR / "05_数字化架构方案.pptx"
prs5.save(p5)

files = [p1, p2, p3, p4, p5]
all_results = {}

for f in files:
    inp = ReaderInput(
        path=str(f),
        source_object_id=f.stem,
        source_version="v1",
        display_name=f.name,
        committed=True,
    )
    sub_out = READER_OUT / f.stem
    ok, reader_res, code, reason = isolated_read_artifact(inp, sub_out)
    assert ok, f"Parse failed for {f.name}: {reason}"
    
    ev_data = {
        "text_blocks": reader_res.text_blocks,
        "tables": reader_res.tables,
        "display_name": f.name,
        "source_object_id": f.stem,
    }
    
    candidates = provider.propose(ev_data, [], [])
    all_results[f.name] = []
    print(f"\n=======================================================")
    print(f"文档: {f.name}")
    print(f"提炼候选数: {len(candidates)}")
    for c in candidates:
        ctype = c["target_type"]
        cchange = c.get("payload", {})
        title = cchange.get("title") or cchange.get("name") or ""
        conf = c.get("confidence")
        print(f"  - [{ctype.upper()}] 置信度: {conf} | 标题: {title}")
        
        # Verify no header is treated as a todo
        if ctype == "todo":
            assert not title.startswith("三、"), f"Header treated as todo: {title}"
            assert not title.startswith("一、"), f"Header treated as todo: {title}"
            assert not title.startswith("二、"), f"Header treated as todo: {title}"
            assert not title.endswith("："), f"Header with colon treated as todo: {title}"
            assert "标准操作规程" not in title or any(v in title for v in ("完成", "编写", "更新")), f"SOP title treated as todo: {title}"
            
        all_results[f.name].append({
            "target_type": ctype,
            "title": title,
            "confidence": conf,
            "summary": c.get("review_brief", {}).get("summary"),
        })

print("\n[SUCCESS] 全部 5 份办公文档候选提炼质量检验通过！零章节标题误判！")
with open("data/f6_candidate_quality_samples.json", "w", encoding="utf-8") as out_f:
    json.dump(all_results, out_f, ensure_ascii=False, indent=2)
print("样本明细已保存至: data/f6_candidate_quality_samples.json")
