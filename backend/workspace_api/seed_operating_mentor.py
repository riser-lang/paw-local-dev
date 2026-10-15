"""Seed the first formal Knowledge page without inventing Source records.

This is a one-time, bounded seed command used during the v0.1 rollout. It
resolves the project by its stable name and resolves only exact, non-history
wiki_page titles. Missing or ambiguous evidence is recorded in
``evidence_pending`` instead of being fabricated.
"""

from __future__ import annotations

from typing import Any

from . import repository as repo
from .db import connect, migrate

PROJECT_NAME = "运营导师体系（Project Knowledge 样板）"
KNOWLEDGE_TITLE = "运营导师体系"
KNOWLEDGE_SLUG = "yunying-daoshi-system"

SOURCE_SPECS = [
    ("01 武汉学校运营导师管理制度（FY27版本）.docx", "primary", "FY27制度正式文档"),
    ("01 FY27运营导师分层规则说明_简化版.docx", "primary", "FY27分层规则"),
    ("01 FY27运营导师薪资方案说明_方案2正式版.docx", "primary", "FY27薪资正式方案"),
    ("01 运营导师年度优秀评优方案-业绩上浮权重版.docx", "supporting", "年度优秀评优方案"),
    ("01 运营导师数据看板.docx", "data", "运营导师数据看板"),
    ("01 2026年4月运营导师带教情况.docx", "data", "FY26 4月带教情况"),
    ("01 2026年5月运营导师带教情况.docx", "data", "FY26 5月带教情况"),
    ("01 2026年6月运营导师带教情况.docx", "data", "FY26 6月带教情况"),
    ("01 2026年7月运营导师带教情况.docx", "data", "FY26 7月带教情况"),
    ("01 运营导师所带徒弟离职情况表.docx", "data", "导师所带徒弟离职情况"),
    ("01 运营导师月报简表-新版.docx", "supporting", "真实导师月报"),
    ("01 0702会议纪要_运营导师项目条线会议.docx", "supporting", "7月2日条线会议材料"),
    ("01 鄂人字【2026】8号关于对FY26武汉学校校级运营导师竞聘结果公示的通知.docx", "supporting", "第三批聘任公示"),
]

CONTENT: dict[str, Any] = {
    "project_positioning": (
        "运营导师是持续运行的“导师供给—带教执行—过程质检—业务结果—留存质量—"
        "分层评级—薪酬激励—评优—续聘/退出—下一批扩充”机制，不是单次培训项目。"
    ),
    "core_conclusions": [
        "FY26 4-7月导师供给由约23人扩至30人并趋稳。",
        "统一口径下学员结果中枢较4月改善，但月间波动和长尾问题仍明显。",
        "不能用少数极端高绩效样本或简单平均值评价项目，应优先看达标率、中位数、0业绩占比和低于60%长尾占比。",
        "留存指标必须按离职可控性分类，不能把全部离职直接归责导师。",
        "真实月报证明一线带教存在“结果+过程动作+诊断+下一阶段措施+附件证据”的运行闭环。",
        "FY27新制度尚未正式生效，因此当前只能建立FY26基线和评价FY27方案设计。",
    ],
    "operating_model": [
        "导师选拔/聘任",
        "带教执行",
        "月报/质检",
        "学员业务结果",
        "留存质量",
        "S/A/B分层",
        "薪资激励",
        "年度优秀评优",
        "续聘/退出",
        "下一批导师供给",
    ],
    "current_rules": [
        "FY27分层采用四维固定评分：业绩达标、留存率、月均带教规模、学员平均业绩上浮。",
        "自然落层，不按人数强制切分。",
        "评优不直接参与分层加分。",
        "述职用于边界复核和方法沉淀，不改变基础评分表。",
        "年度优秀评优与S/A/B分层是两套不同机制。",
    ],
    "metrics": [
        {"month": "2026-04", "导师": 23, "总带教记录": 93, "统一分析记录": 73, ">=100%达标率": "28.8%", "中位数": "52%", "0/未开单": "17.8%", "<60%": "50.7%"},
        {"month": "2026-05", "导师": 29, "总带教记录": 95, "统一分析记录": 81, ">=100%达标率": "46.9%", "中位数": "81%", "0/未开单": "12.3%", "<60%": "37.0%"},
        {"month": "2026-06", "导师": 30, "总带教记录": 95, "统一分析记录": 84, ">=100%达标率": "38.1%", "中位数": "75.32%", "0%": "9.5%", "<60%": "38.1%"},
        {"month": "2026-07", "导师": 30, "总带教记录": 87, "统一分析记录": 82, ">=100%达标率": "48.8%", "中位数": "87.43%", "0%": "13.4%", "<60%": "31.7%"},
        "统计口径：仅带教15天以上记录进入量化；百分比字段才进入量化；“未开单”按0；仅写“开单”而无百分比不进入达标率/中位数；产能预警“脱离/未脱离”不与百分比混算。",
    ],
    "findings": [
        "4→7月结果中枢整体改善，但6月有回落。",
        "7月四个月中达标率和中位数最高。",
        "高中班级部5-7月表现较稳定，是优先提炼方法的候选部门。",
        "高中一对一部极端值最多，不宜使用平均值。",
        "智慧学习部存在高低并存。",
        "当前只能描述相关性和运行表现，不能证明导师机制对业绩改善存在因果关系。",
    ],
    "timeline": [
        {"date": "2026-04-21", "event": "FY26第三批运营导师公示，9人"},
        {"date": "2026-07-02", "event": "条线会议：薪酬、分层、带教目标、评优、项目复盘规则重构节点"},
        {"date": "2026-08-04", "event": "FY27新一轮导师报名/推荐截止"},
        {"date": "2026-08-05", "event": "学校审核截止"},
        {"date": "2026-09-01", "event": "FY27武汉学校运营导师管理制度拟正式执行"},
    ],
    "risks": [
        "月度表结构存在变化。",
        "自动文字总结和底层行数据曾不一致。",
        "极端高值使均值失真。",
        "不同学员阶段不能直接混为一个群体。",
        "离职率分母未最终统一。",
    ],
    "exceptions": [
        "A：导师/管理可干预",
        "B：组织可干预",
        "C：弱可控/不可控",
        "D：被动离职/绩效淘汰",
    ],
    "unresolved_items": [
        "FY27制度正文与简化分层规则的正式文书层级最终确认。",
        "7月完整离职原因。",
        "超大PPT/PDF中的方法论和复盘。",
        "FY27 9月后新制度真实运行效果。",
        "HR/业务最终统一有效带教与离职率分母。",
    ],
    "lessons_learned": [
        "评价导师体系不能只看明星样本。",
        "不能使用简单平均业绩作为主指标。",
        "结果指标必须与过程证据结合。",
        "留存必须区分可控性。",
        "项目知识应保留规则版本和数据口径。",
    ],
    "data_quality_notes": [
        "不同月份列结构不同。",
        "4-6月与7月统计结构有变化。",
        "“开单”与百分比字段共存。",
        "7-15天与15天以上记录共存。",
        "自动总结曾和底层数据不一致。",
    ],
    "validation_framework": {
        "baseline": "FY26 4-7月",
        "comparison": "FY27 new mechanism",
        "metrics": [
            "导师供给",
            "有效带教规模",
            "=100%达标率",
            "业绩中位数",
            "0业绩占比",
            "<60%长尾占比",
            "15/30/90天留存",
            "可干预离职率",
            "产能预警脱离率",
            "部门差异",
            "薪资投入变化",
        ],
    },
}


def _resolve_project(conn) -> str:
    rows = conn.execute("SELECT id FROM project WHERE name=?", (PROJECT_NAME,)).fetchall()
    if len(rows) != 1:
        raise ValueError(f"Expected exactly one project named {PROJECT_NAME!r}, found {len(rows)}")
    return str(rows[0]["id"])


def _resolve_sources(conn) -> tuple[list[dict[str, str]], list[str]]:
    links: list[dict[str, str]] = []
    pending: list[str] = []
    for title, role, note in SOURCE_SPECS:
        rows = conn.execute(
            "SELECT id FROM wiki_page WHERE title=? AND category NOT LIKE '%历史版本%' ORDER BY updated_at DESC",
            (title,),
        ).fetchall()
        if len(rows) == 1:
            links.append({"source_page_id": str(rows[0]["id"]), "source_role": role, "note": note})
        elif not rows:
            pending.append(f"Source Registry 中未找到：{title}")
        else:
            pending.append(f"Source Registry 中存在多个同名候选，未自动选择：{title}")
    return links, pending


def seed() -> dict[str, Any]:
    migrate()
    with connect() as conn:
        project_id = _resolve_project(conn)
        source_links, pending = _resolve_sources(conn)
    content = dict(CONTENT)
    content["evidence_pending"] = pending + [
        "运营导师数据看板.ksheet 尚未在当前 wiki_page Source Registry 中出现，待后续正式索引后补链。",
        "运营导师所带徒弟离职情况.ksheet 尚未在当前 wiki_page Source Registry 中出现，当前仅引用已核验的 DOCX 表格。",
    ]
    detail = repo.upsert_knowledge({
        "title": KNOWLEDGE_TITLE,
        "slug": KNOWLEDGE_SLUG,
        "knowledge_type": "project",
        "one_line_summary": "运营导师体系已经形成从导师供给到带教、评价、激励和续聘/退出的长期运营闭环；FY26 4-7月结果中枢较4月改善，但部门差异、长尾学员和离职可控性仍需精细化管理，FY27新机制应以统一基线验证实际效果。",
        "applicability": "武汉学校运营导师体系；FY26运行基线 + FY27拟执行制度设计。",
        "status": "draft",
        "content_json": content,
        "change_summary": "Initial v1.0 release candidate compiled from FY27 rules, FY26 4-7 operating data, mentor monthly reports, retention data and compensation design.",
        "project_links": [{"project_id": project_id, "relation_type": "primary"}],
        "source_links": source_links,
        "human_confirmed": False,
    })
    return {
        "knowledge": detail,
        "resolved_source_count": len(source_links),
        "evidence_pending_count": len(pending) + 2,
        "project_id": project_id,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(seed(), ensure_ascii=False))
