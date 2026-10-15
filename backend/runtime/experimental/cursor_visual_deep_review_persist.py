#!/usr/bin/env python3
"""Persist PAW Cursor visual deep-review continuation (analysis plane only).

Merges new page/image dispositions into existing canonical cursor_visual
artifacts. Does not rewrite previously grounded facts, Formal State, or Brain.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cursor_visual_admission as admission
from cursor_visual_runner import load_queue_sources, persist_source_review, save_checkpoint, load_checkpoint

STORE = ROOT / "_cursor_visual_store"
DEEP_CKPT = STORE / "deep_review_checkpoint.json"


def _queue() -> dict[str, dict]:
    return {str(s.get("source_version_id")): s for s in load_queue_sources()}


def _reader(source: dict) -> dict:
    return json.loads(Path(source["reader_result_path"]).read_text(encoding="utf-8"))


def _vp_input(reader: dict, idx: int) -> dict:
    page = reader["visual_pages"][idx]
    path = page["path"]
    return {
        "image_path": path,
        "image_sha256": page["sha256"],
        "locator": f"visual_pages[{idx}]",
        "kind": page.get("kind") or "pdf-page",
        "page": page.get("page") or (idx + 1),
        "render_mode": page.get("render_mode"),
        "dimensions": page.get("dimensions"),
        "image_read_method": "cursor_read_image_pixels",
    }


def _emb_input(reader: dict, idx: int) -> dict:
    obj = reader["embedded_objects"][idx]
    return {
        "image_path": obj["path"],
        "image_sha256": obj["sha256"],
        "locator": f"embedded_objects[{idx}]",
        "kind": obj.get("kind") or "image",
        "render_mode": "embedded",
        "image_read_method": "cursor_read_image_pixels",
    }


def fact(content: str, locator: str, sha: str, claim: str = "explicit_fact") -> dict:
    return {
        "claim_type": claim,
        "content": content,
        "evidence_locators": [locator],
        "image_sha256": sha,
        "not_in_structured_reader": True,
    }


def disp(locator: str, sha: str, status: str, note: str, page=None) -> dict:
    row = {
        "locator": locator,
        "image_sha256": sha,
        "status": status,
        "note": note,
        "evidence_locators": [locator],
    }
    if page is not None:
        row["page"] = page
    return row


def build_513687(source: dict, reader: dict) -> dict:
    vp = reader["visual_pages"]
    sha = [p["sha256"] for p in vp]
    loc = [f"visual_pages[{i}]" for i in range(len(vp))]
    inputs = [_vp_input(reader, i) for i in range(len(vp))]

    dispositions = [
        disp(loc[0], sha[0], "PRIOR_REVIEWED_GROUNDED", "封面，先前已核验，本轮不重写。", 1),
        disp(loc[1], sha[1], "REVIEWED_GROUNDED", "目录：五章结构与页码。", 2),
        disp(loc[2], sha[2], "REVIEWED_GROUNDED", "前言：新导师寄语。", 3),
        disp(loc[3], sha[3], "SKIP_REDUNDANT_CHAPTER_DIVIDER", "第一章分隔页，条目已见于目录。", 4),
        disp(loc[4], sha[4], "REVIEWED_GROUNDED", "1.1 超级销售→销售赋能者对照表及1.2开篇。", 5),
        disp(loc[5], sha[5], "PRIOR_REVIEWED_GROUNDED", "1.3 优秀导师画像，先前已核验。", 6),
        disp(loc[6], sha[6], "REVIEWED_GROUNDED", "第一章收束寄语页（印刷页脚第4页）。", 7),
        disp(loc[7], sha[7], "SKIP_REDUNDANT_CHAPTER_DIVIDER", "第二章分隔页。", 8),
        disp(loc[8], sha[8], "REVIEWED_GROUNDED", "带教三大原则/核心职责/阶段考核标准信息图。", 9),
        disp(loc[9], sha[9], "REVIEWED_GROUNDED", "2.1 0-2月零基础新人三阶段。", 10),
        disp(loc[10], sha[10], "REVIEWED_GROUNDED", "2.2 3-6月进阶；2.3 产能预警开篇。", 11),
        disp(loc[11], sha[11], "REVIEWED_GROUNDED", "2.3 四阶段纠偏；2.4 运营实习生开篇。", 12),
        disp(loc[12], sha[12], "REVIEWED_GROUNDED", "2.4 运营实习生三阶段。", 13),
        disp(loc[13], sha[13], "SKIP_REDUNDANT_CHAPTER_DIVIDER", "第三章分隔页。", 14),
        disp(loc[14], sha[14], "REVIEWED_GROUNDED", "3.1 客户开拓话术辅导。", 15),
        disp(loc[15], sha[15], "REVIEWED_GROUNDED", "3.2 需求挖掘 SPIN。", 16),
        disp(loc[16], sha[16], "REVIEWED_GROUNDED", "3.3 价值呈现 FABE 与案例故事。", 17),
        disp(loc[17], sha[17], "REVIEWED_GROUNDED", "3.4 成交/关单信号识别开篇。", 18),
        disp(loc[18], sha[18], "REVIEWED_GROUNDED", "3.4 帮助决策与转介绍。", 19),
        disp(loc[19], sha[19], "SKIP_REDUNDANT_CHAPTER_DIVIDER", "第四章分隔页。", 20),
        disp(loc[20], sha[20], "REVIEWED_GROUNDED", "4.1.1 SCRM 与企业微信。", 21),
        disp(loc[21], sha[21], "REVIEWED_GROUNDED", "4.1.2–4.1.3 谈单转化与线索外呼工具。", 22),
        disp(loc[22], sha[22], "REVIEWED_GROUNDED", "4.1.3 表单工具与 4.1.4 数据复盘工具。", 23),
        disp(loc[23], sha[23], "REVIEWED_GROUNDED", "4.2.1 经典销售书籍分层清单。", 24),
        disp(loc[24], sha[24], "REVIEWED_GROUNDED", "4.2.2 实战课程推荐。", 25),
        disp(loc[25], sha[25], "REVIEWED_GROUNDED", "4.2.3–4.2.4 文章与公众号推荐。", 26),
        disp(loc[26], sha[26], "REVIEWED_GROUNDED", "第四章收束：汉校通小程序与带教练习顺序。", 27),
        disp(loc[27], sha[27], "SKIP_REDUNDANT_CHAPTER_DIVIDER", "第五章分隔页。", 28),
        disp(loc[28], sha[28], "REVIEWED_GROUNDED", "5.1 时间精力分配与极简执行表。", 29),
        disp(loc[29], sha[29], "REVIEWED_GROUNDED", "5.2 四维收口与五步检验法。", 30),
        disp(loc[30], sha[30], "REVIEWED_GROUNDED", "5.2 收口评估表与话术。", 31),
        disp(loc[31], sha[31], "REVIEWED_GROUNDED", "5.2 通关模拟续页与 5.3 店面配合开篇。", 32),
        disp(loc[32], sha[32], "REVIEWED_GROUNDED", "5.3 导师/店面分工与四节点配合。", 33),
        disp(loc[33], sha[33], "REVIEWED_GROUNDED", "5.3.3 三级上战场与统一数据口径；5.4 开篇。", 34),
        disp(loc[34], sha[34], "REVIEWED_GROUNDED", "5.4/5.5 反馈与归属感。", 35),
        disp(loc[35], sha[35], "REVIEWED_GROUNDED", "5.5 情感/价值/成长陪伴。", 36),
        disp(loc[36], sha[36], "REVIEWED_GROUNDED", "5.6 入职1-7天与8-30天节奏。", 37),
        disp(loc[37], sha[37], "REVIEWED_GROUNDED", "5.6 2-3月攻坚与3-6月深耕。", 38),
        disp(loc[38], sha[38], "REVIEWED_GROUNDED", "5.7 团队氛围与激励。", 39),
        disp(loc[39], sha[39], "REVIEWED_GROUNDED", "5.8 信任五关键；5.9 开篇。", 40),
        disp(loc[40], sha[40], "REVIEWED_GROUNDED", "5.9 主动提问四策略。", 41),
        disp(loc[41], sha[41], "SKIP_REDUNDANT_CREDITS", "尾页编委名单与口号，无项目级标准/表。", 42),
    ]

    facts = [
        fact(
            "visual_pages[1] 为目录页，主标题「目录」，背景英文 Contents。"
            "第一章 导师角色认知与核心价值；1.1 我们为何需要导师：从“超级销售”到“销售赋能者”的转变；"
            "1.2 导师的角色定位：模范、教练、顾问、连接器；1.3 优秀导师的画像：必备心态、能力与标准。"
            "第二章 不同带教对象的分阶段带教规划；2.1 0-2月零基础新人课前顾问；2.2 3-6月进阶新人课前顾问；"
            "2.3 产能预警员工；2.4 运营实习生。"
            "第三章 核心销售能力的常见话术（客户开拓/需求挖掘/价值呈现与方案解决/成交与客户经营）。"
            "第四章 核心销售能力的辅导工具箱（常用工具清单、书籍课程文章公众号推荐）。"
            "第五章 带教中的常见问题及解决思路（5.1–5.9）。本 Source 为 image-only PDF，目录仅能从该视觉页读取。",
            loc[1], sha[1],
        ),
        fact(
            "visual_pages[2] 标题「前言」，副标题「一位老师给新导师的寄语」。正文强调从个人“超级销售”转向“销售赋能者”，"
            "以团队成长与经验传递为核心，而非个人签单。",
            loc[2], sha[2],
        ),
        fact(
            "visual_pages[4] 节 1.1「我们为何需要导师：从“超级销售”到“销售赋能者”的转变」。"
            "1.1.2 对照表列「维度 / 超级销售 / 销售赋能者」：目标（个人业绩最大化 vs 团队整体产能提升）、"
            "核心能力（个人谈判签单 vs 诊断辅导赋能）、时间分配（一线签单 vs 带教复盘赋能）、成就感来源（大单 vs 新人独立签单）。"
            "同页开篇 1.2「导师的角色定位：模范、教练、顾问、连接器」，1.2 首条为「模范：用行动做榜样」。"
            "页眉可见「新东方 XDF.CN」与手册名，页脚可见「第2页」。",
            loc[4], sha[4],
        ),
        fact(
            "visual_pages[6] 为第一章收束寄语页，页脚「第4页」。正文框强调成为导师是责任与荣耀，"
            "并再次写出从“超级销售”转为“销售赋能者”。页眉为新东方与销售导师手册。",
            loc[6], sha[6],
        ),
        fact(
            "visual_pages[8]（页脚第6页）信息图三列：带教三大原则、导师核心职责、阶段考核标准。"
            "原则可见：分层分级；先过程后结果（新人抓动作、老人抓效率、预警抓纠偏）；教+练+考（讲解→模拟→实战→复盘→通关）。"
            "职责可见：制定专属带教计划并按日/周跟进；陪听陪访陪练；记录带教日志并同步主管；解决心态/技能/流程问题；确保阶段目标与转正/达标/独立上岗。"
            "考核对象对照：0–2月新人（话术通关、工作量达标、无违规）；3–6月新人（转化率、业绩、流程标准）；"
            "产能预警员工（过程数据+业绩回升）；运营实习生（准确率、执行力、协作度）。未把未印出的具体阈值写成数字。",
            loc[8], sha[8],
        ),
        fact(
            "visual_pages[9] 节 2.1「0-2月零基础新人课程顾问」。核心标签：小白、无销售经验、无产品认知；"
            "核心目标：活下来→会开口→能接线索→完成基础转化；总周期：2个月（8周）分3阶段。"
            "2.1.1 破冰适应期（第1周）；2.1.2 基础技能期（第2-4周）；2.1.3 独立实战期（第2个月）。"
            "各阶段均有「带教内容 / 导师动作」双栏及通关口径。导师动作可见陪听、模拟演练、每周通关考核等动作名；"
            "未核验到可独立引用的具体人数或分数阈值，不写入数值。",
            loc[9], sha[9],
        ),
        fact(
            "visual_pages[10] 节 2.2「3-6月进阶新人课程顾问」：能接单但不稳定、转化偏低；目标为稳流程→提转化→升客单；"
            "总周期约3个月、分2阶段。2.2.1 流程优化期（第3-4月）；2.2.2 产能稳定期（第5-6月）。"
            "同页开启 2.3「产能预警员工」，总周期约1-2个月、分阶段纠偏。页脚第8页。",
            loc[10], sha[10],
        ),
        fact(
            "visual_pages[11] 续写 2.3 产能预警四阶段：2.3.1 精准诊断期（第1周，产出一对一改进计划）；"
            "2.3.2 强制纠偏期（第2–3周，回归标准流程）；2.3.3 产能提升期（第4–8周）；2.3.4 巩固跟踪期（长期，隔周轻复盘）。"
            "同页表 2.4「运营实习生」：非一线销售、辅助销售运营、执行岗；目标懂业务→会工具→能执行；总周期约2–3个月、分3阶段。",
            loc[11], sha[11],
        ),
        fact(
            "visual_pages[12] 2.4 运营实习生三阶段：2.4.1 业务认知期（第1-2周，销售逻辑/团队/基础工具含 SCRM 与打卡协作）；"
            "2.4.2 实操执行期（第3-4周，数据统计、物料、活动协助）；2.4.3 独立上岗期（第2-3月）。页脚第10页。",
            loc[12], sha[12],
        ),
        fact(
            "visual_pages[14] 节 3.1「如何辅导“客户开拓”：名单获取、电话/微信破冰话术打磨」。"
            "3.1.1 名单获取强调精准客群；3.1.2 破冰话术从推销感转向解决问题。可见高意向家长、被动咨询、老客户转介绍等客群标签。"
            "本页为辅导方法页，不把示例话术中的未核验数字当项目 KPI。",
            loc[14], sha[14],
        ),
        fact(
            "visual_pages[15] 续写微信破冰步骤，并进入 3.2「如何辅导“需求挖掘”：实战演练与反馈」。"
            "3.2.1 需求挖掘关键逻辑标明 SPIN：S Situation、P Problem、I Implication、N Need-Payoff。"
            "同页含对/错应答示例场景。页脚第13页。",
            loc[15], sha[15],
        ),
        fact(
            "visual_pages[16] 节 3.3「如何辅导“价值呈现与方案解决”：案例故事教学」。"
            "3.3.1 价值呈现核心逻辑标明 FABE：F Feature、A Advantage、B Benefit、E Evidence。"
            "3.3.2 要求准备案例故事类型（可见逆袭型、升学规划型等标签）。示例中的分数仅作故事证据句，不升格为项目 KPI。",
            loc[16], sha[16],
        ),
        fact(
            "visual_pages[17] 3.4「如何辅导“成交与客户经营”：成交/关单信号识别、转介绍引导」。"
            "3.4.1 关单信号识别列出家长隐性同意表现（比价/时间价格政策追问、主动问老师课表资料、反复跟进等）。页脚第15页。",
            loc[17], sha[17],
        ),
        fact(
            "visual_pages[18] 3.4.2 关键技巧从“逼单”转为“帮助决策”（澄清顾虑、限时激励、风险承诺）；"
            "3.4.3 客户经营强调缴费后即时反馈与转介绍话术。总结句：销售卖的是解决家长焦虑与帮助孩子成长，而非仅卖课程。",
            loc[18], sha[18],
        ),
        fact(
            "visual_pages[20] 节 4.1「常用工具清单以及使用指南」。4.1.1 客户管理类工具核心必备："
            "(1) SCRM 系统（线索分层、跟进提醒、统计与外部系统对接）；(2) 企业微信（客户标签、快捷回复、社群与朋友圈、与 SCRM 同步）。"
            "使用指南可见标签维度（年级/科目/来源/意向）及朋友圈内容节奏要求。页脚第18页。",
            loc[20], sha[20],
        ),
        fact(
            "visual_pages[21] 4.1.2 谈单转化类工具：可视化谈单工具包（本地/在线文档，含课程体系、师资、学员案例等）；"
            "4.1.3 线索获取与跟进辅助：智能外呼（SCRM 云外呼/云座席，模板、录音、复盘回写 SCRM）。页脚第19页。",
            loc[21], sha[21],
        ),
        fact(
            "visual_pages[22] 4.1.3 表单工具（金数据/问卷星）：讲座信息收集、试课预约、转介绍登记；"
            "导师要点强调从邀约到讲座到咨询的跟盯以便统计转化率。"
            "4.1.4 数据复盘与自我提升：Excel/金山表格（客户台账、业绩与转化分析）；录音复盘（企业微信录音/剪映）。页脚第20页。",
            loc[22], sha[22],
        ),
        fact(
            "visual_pages[23] 节 4.2「经典销售书籍/课程/文章/公众号推荐清单」，按新手入门→进阶提升→高阶突破分层。"
            "入门可见《销售就是玩转情商》《世界上最伟大的推销员》《销售就是会提问》；"
            "进阶可见《SPIN销售巨人》《优势谈判》及信任模型类书目。仅记录书名分层，不把推荐理由写成外部事实。",
            loc[23], sha[23],
        ),
        fact(
            "visual_pages[24] 4.2.2「优质销售课程推荐（实战型，可直接落地）」列出可落地课程/专栏类型，"
            "含销售六步、互联网铁军进阶、教培读单实战、科学销售/SCRM 等条目标题。",
            loc[24], sha[24],
        ),
        fact(
            "visual_pages[25] 4.2.3 优质文章推荐（教培读单、异议处理、朋友圈获客、新手出单关键动作等标题）；"
            "4.2.4 优质公众号推荐分销售技巧类与升学备考类。未把未看清的公众号 ID 写成事实。",
            loc[25], sha[25],
        ),
        fact(
            "visual_pages[26] 工具箱收束出现「汉校通小程序」推荐：以成绩输入模拟志愿、服务本地升学焦虑家长。"
            "导师辅导小贴士要求新人按 工具练习→话术背诵→模拟演练→实战复盘 的顺序，每周聚焦少量核心工具。",
            loc[26], sha[26],
        ),
        fact(
            "visual_pages[28] 节 5.1「导师如何进行时间精力的分配以及自我压力的调控」。"
            "5.1.3 极简执行表三行：每天 / 每周 / 每月（要事+批量处理+留白与运动；自我复盘+专业学习+休息；梳理学员并淘汰低效动作）。"
            "页脚第26页。",
            loc[28], sha[28],
        ),
        fact(
            "visual_pages[29] 节 5.2 收口检验。5.2.1 核心四件事：会不会、懂不懂原理、稳不稳、能不能传承。"
            "5.2.2 五步收口检验法：任务闭环（独立交付）、复盘复述（理解深度）、场景应变、反向教学、数据/结果检验。页脚第27页。",
            loc[29], sha[29],
        ),
        fact(
            "visual_pages[30] 5.2.3「导师收口评估表（极简版）」列评估维度与 1–5 分量表。"
            "可见总分口径：24–30 分可完全放手；18–23 分基本合格需补短板；12–17 分需重新带教+强化练习；12 分以下需调整带教方式或学习态度。"
            "5.2.4 收口沟通话术分成果/责任/成长；底部有模拟话术通关步骤，并写明不合格必须回炉重练。页脚第28页。",
            loc[30], sha[30],
        ),
        fact(
            "visual_pages[31] 续写全真模拟通关：导师扮演刁钻家长抛难题；检验标准含不慌不急不冷场、能挖痛点、能自然促单。"
            "独立作业检验用 XX 占位（外呼/有效沟通/邀约到店或试听/加微），未印死具体数量。"
            "同页进入 5.3「导师如何和店面更好配合去带教新人」，5.3.1 先定职责：销售导师主抓人+技能+成交。",
            loc[31], sha[31],
        ),
        fact(
            "visual_pages[32] 店面侧主抓现场、资源、纪律与结果。5.3.2 四个配合节点：Day1 对齐；日常闭环（导师晨间小练/现场陪练/晚复盘，店面给低难度客户并控场）；"
            "日闭环复盘；上岗后扶上马。关键规则：店面不得绕过导师直接把新客派给新人。页脚第30页。",
            loc[32], sha[32],
        ),
        fact(
            "visual_pages[33] 5.3.3 通用配合机制：新人「三级上战场」（一级只模仿不接真实客户；二级导师/店长旁协助；三级独立接待），"
            "能否升级由导师判定。数据口径统一四项：有效沟通量、加微/留资率、邀约试听/到店率、成交转化率；"
            "数据差则按「不会导师补、不干店面管」分工。同页进入 5.4 收集并运用徒弟反馈。页脚第31页。",
            loc[33], sha[33],
        ),
        fact(
            "visual_pages[34]–visual_pages[35] 覆盖 5.4 反馈闭环与 5.5 归属感/认同感："
            "多渠道收集（任务后即时、结构化周反馈、非正式沟通、数据结果）并分类迭代带教；"
            "5.5 从融入环境、情感关怀、价值认可、成长陪伴四块写导师动作。",
            loc[34], sha[34],
        ),
        fact(
            "visual_pages[36] 节 5.6「导师如何分阶段、分节奏培训新人」。引言写适应/成长/独立/差异化四阶段。"
            "5.6.1 入职1-7天适应期·筑基：低强度，以认知讲解+基础操作为主。"
            "5.6.2 入职8-30天成长期·实操：中强度，理论与实操结合、边练边纠。页脚第34页。",
            loc[36], sha[36],
        ),
        fact(
            "visual_pages[37] 5.6.3 入职2-3个月独立期·攻坚：高自主、导师抓结果复盘与关键卡点；"
            "5.6.4 入职3-6个月进阶期·深耕：定制化、项目实践+专项突破，目标独当一面。页脚第35页。",
            loc[37], sha[37],
        ),
        fact(
            "visual_pages[38] 节 5.7 团队氛围与激励。痛点三框：内向不融入、高压躺平、无业绩无成就感。"
            "解决：带入团队三步（破冰/任务融入/文化渗透）与四维激励（即时、成长、目标、情感）。含实战案例框，案例中的个人结果不升格为项目 KPI。",
            loc[38], sha[38],
        ),
        fact(
            "visual_pages[39] 节 5.8 建立信任。痛点含怕被骂藏问题、导师摆架子、承诺不兑现。"
            "核心解决「信任建立关键」：平等沟通、倾听优先、兑现承诺、表扬细节/情绪价值、给可落地解决办法。"
            "同页开启 5.9 调动主动提问。案例中的百分比不作为项目 KPI 写入。",
            loc[39], sha[39],
        ),
        fact(
            "visual_pages[40] 5.9.2 激活主动提问四策略：营造安全提问环境；教怎么提问并给模板；提问即正向反馈；倒逼思考不直接给答案。"
            "5.9.3 为实战案例页。页脚第38页。",
            loc[40], sha[40],
        ),
    ]
    tables = [
        {
            "claim_type": "explicit_fact",
            "content": "visual_pages[4] 对照表表头为 维度 | 超级销售 | 销售赋能者，四行目标/核心能力/时间分配/成就感来源。",
            "evidence_locators": [loc[4]],
            "image_sha256": sha[4],
            "not_in_structured_reader": True,
        },
        {
            "claim_type": "explicit_fact",
            "content": "visual_pages[30] 收口评估表为三列表：评估维度、得分情况（1–5）、得分范围与评价标准（含 24–30 / 18–23 / 12–17 / 12分以下 四档）。",
            "evidence_locators": [loc[30]],
            "image_sha256": sha[30],
            "not_in_structured_reader": True,
        },
        {
            "claim_type": "explicit_fact",
            "content": "visual_pages[28] 极简执行表两列（周期 | 动作），行是每天/每周/每月。",
            "evidence_locators": [loc[28]],
            "image_sha256": sha[28],
            "not_in_structured_reader": True,
        },
    ]
    observations = [
        {
            "claim_type": "explicit_fact",
            "content": "visual_pages[41] 为尾页：编委名单框（主编/组长/编写人）与英文口号 A BETTER YOU, A BIGGER WORLD。无表格或标准条文。",
            "evidence_locators": [loc[41]],
            "image_sha256": sha[41],
        }
    ]
    skipped = [
        {"why": "第一章/二/三/四/五分隔页仅重复目录已列章节名，不另造事实。", "evidence_locators": [loc[3], loc[7], loc[13], loc[19], loc[27]]},
        {"why": "封面与 visual_pages[5] 先前已 grounded，本轮不重写。", "evidence_locators": [loc[0], loc[5]]},
    ]
    ambiguities = [
        {
            "claim_type": "weak_inference",
            "content": "手册印刷页脚与 Reader visual_pages 序号相差封面/目录/前言页；事实一律绑定 visual_pages[N] 与 image sha256，不以页脚数字替代 locator。",
            "evidence_locators": [loc[4]],
        },
        {
            "claim_type": "weak_inference",
            "content": "案例故事与示例话术中的个人分数/百分比仅作教学方法示例，不作为项目级 KPI。",
            "evidence_locators": [loc[16], loc[30], loc[38]],
        },
    ]
    return {
        "visual_inputs": inputs,
        "grounded_visual_facts": facts,
        "table_observations": tables,
        "visual_observations": observations,
        "ambiguities": ambiguities,
        "already_grounded_skipped": skipped,
        "page_dispositions": dispositions,
    }


def build_487128(source: dict, reader: dict) -> dict:
    vp = reader["visual_pages"]
    sha = [p["sha256"] for p in vp]
    loc = [f"visual_pages[{i}]" for i in range(len(vp))]
    inputs = [_vp_input(reader, i) for i in range(len(vp))]
    blank = "9fd42c68af374f0b352ca11503b676a1ca52aac5e389429a520634419f42e0f5"
    lined_a = "d189bb8bde7bbd70"
    dispositions = [
        disp(loc[0], sha[0], "PRIOR_REVIEWED_GROUNDED", "封面，先前已核验。", 1),
        disp(loc[1], sha[1], "SKIP_BLANK", "空白页，与 visual_pages[16] 同 sha。", 2),
        disp(loc[2], sha[2], "REVIEWED_GROUNDED", "新东方企业文化五段。", 3),
        disp(loc[3], sha[3], "PRIOR_REVIEWED_GROUNDED", "运营新兵营成长地图，先前已核验。", 4),
        disp(loc[4], sha[4], "REVIEWED_GROUNDED", "课程概览三日表。", 5),
        disp(loc[5], sha[5], "SKIP_PHOTO_COLLAGE", "往期精彩集锦照片墙；活动名与课程表重复，照片本身非文字表。", 6),
        disp(loc[6], sha[6], "SKIP_REDUNDANT_NOTEBOOK", "横线笔记本模板+钟形装饰，无可读正文。", 7),
        disp(loc[7], sha[7], "SKIP_REDUNDANT_NOTEBOOK", "标题条「湖北省域FY25钟楚计划——运营岗职通培训」+空白横线+钟形装饰。", 8),
        disp(loc[8], sha[8], "SKIP_REDUNDANT_NOTEBOOK", "与 visual_pages[6] 同图，重复空白笔记页。", 9),
        disp(loc[9], sha[9], "SKIP_REDUNDANT_NOTEBOOK", "同类空白笔记页。", 10),
        disp(loc[10], sha[10], "SKIP_REDUNDANT_NOTEBOOK", "与 visual_pages[6] 同图。", 11),
        disp(loc[11], sha[11], "SKIP_REDUNDANT_NOTEBOOK", "同类空白笔记页。", 12),
        disp(loc[12], sha[12], "SKIP_REDUNDANT_NOTEBOOK", "与 visual_pages[6] 同图。", 13),
        disp(loc[13], sha[13], "SKIP_REDUNDANT_NOTEBOOK", "同类空白笔记页。", 14),
        disp(loc[14], sha[14], "SKIP_REDUNDANT_NOTEBOOK", "与 visual_pages[6] 同图。", 15),
        disp(loc[15], sha[15], "SKIP_REDUNDANT_NOTEBOOK", "同类空白笔记页。", 16),
        disp(loc[16], sha[16], "SKIP_BLANK", "空白页，与 visual_pages[1] 同 sha。", 17),
        disp(loc[17], sha[17], "SKIP_REDUNDANT_BRANDING", "尾页新东方/湖北省域人力资源部品牌底图，无表或标准。", 18),
    ]
    facts = [
        fact(
            "visual_pages[2] 标题「新东方企业文化」。五段可读：愿景（成为中国优秀的、令人尊敬的、有文化价值的教育、生活、文旅服务机构）；"
            "使命（提升学生终身竞争力，提高客户生活品质，提供旅行美好体验力）；"
            "核心价值观（诚信负责 真情关爱 好学精进 志高行远）；企业文化（坦诚 尊重 协作 创新）；"
            "新东方精神（追求卓越，挑战极限，从绝望中寻找希望，人生终将辉煌！）。"
            "右侧英文 slogan A BIGGER WORLD / A BETTER YOU。image-only PDF，该页不在 text_blocks。",
            loc[2], sha[2],
        ),
        fact(
            "visual_pages[4] 标题「课程概览」，页眉可见湖北省域人力资源部。"
            "三日课程表列 日期/形式/课程内容。DAY1 上午：暖场互动（培训项目介绍+小组自我介绍+破冰游戏）、"
            "主题授课（部门架构/员工福利/职业发展、公司介绍与企业文化、职业成长分享）；"
            "下午主题授课含青少部业务、资源有效跟进、初中学科产品、高中学科产品。"
            "DAY2 上午：面签及电访流程、新人分享；下午考核：无领导小组讨论、压力面试、公布结果。"
            "DAY3 全天：校区实践。形式列用主题授课/暖场互动/考核色标区分。",
            loc[4], sha[4],
        ),
    ]
    tables = [
        {
            "claim_type": "explicit_fact",
            "content": "visual_pages[4] 课程概览为三日合并单元格日程表，列=日期、形式、课程内容；DAY1 分上午/下午多行，DAY3 为全天校区实践。",
            "evidence_locators": [loc[4]],
            "image_sha256": sha[4],
            "not_in_structured_reader": True,
        }
    ]
    observations = [
        {
            "claim_type": "explicit_fact",
            "content": "visual_pages[5]「往期精彩集锦」为现场照片拼贴，图注活动名与课程概览模块同族（破冰、自我介绍、主题授课、产品知识、面试电访、笔试、无领导小组、压力面试、校区实践/入职考核）。照片像素本身不承载可核验数字表。",
            "evidence_locators": [loc[5]],
            "image_sha256": sha[5],
        }
    ]
    skipped = [
        {"why": "空白页 visual_pages[1] 与 [16] 同 sha，无像素内容。", "evidence_locators": [loc[1], loc[16]]},
        {"why": "visual_pages[6,8,10,12,14] 及相邻笔记页为横线模板+钟形装饰，标题条仅重复手册名，无项目级正文表。", "evidence_locators": [loc[6], loc[7], loc[8], loc[9], loc[10], loc[11], loc[12], loc[13], loc[14], loc[15]]},
        {"why": "尾页仅为新东方湖北省域人力资源部品宣。", "evidence_locators": [loc[17]]},
        {"why": "封面与成长地图先前已 grounded。", "evidence_locators": [loc[0], loc[3]]},
    ]
    ambiguities = [
        {
            "claim_type": "weak_inference",
            "content": "课程概览为三日表，与已核验的成长地图六日节点并置存在；二者均为本 Source 视觉页，不跨源解释差异。",
            "evidence_locators": [loc[4], loc[3]],
        }
    ]
    return {
        "visual_inputs": inputs,
        "grounded_visual_facts": facts,
        "table_observations": tables,
        "visual_observations": observations,
        "ambiguities": ambiguities,
        "already_grounded_skipped": skipped,
        "page_dispositions": dispositions,
    }


def build_487133(source: dict, reader: dict) -> dict:
    vp = reader["visual_pages"]
    sha = [p["sha256"] for p in vp]
    loc = [f"visual_pages[{i}]" for i in range(len(vp))]
    inputs = [_vp_input(reader, i) for i in range(len(vp))]
    dispositions = [
        disp(loc[0], sha[0], "PRIOR_REVIEWED_GROUNDED", "封面，先前已核验。", 1),
        disp(loc[1], sha[1], "SKIP_BLANK", "空白页。", 2),
        disp(loc[2], sha[2], "REVIEWED_GROUNDED", "新东方企业文化五段。", 3),
        disp(loc[3], sha[3], "PRIOR_REVIEWED_GROUNDED", "成长地图，先前已核验。", 4),
        disp(loc[4], sha[4], "REVIEWED_GROUNDED", "课程概览：线下课程+线上自主学习表。", 5),
        disp(loc[5], sha[5], "SKIP_PHOTO_COLLAGE", "往期精彩集锦照片墙，活动名与课程表重复。", 6),
        disp(loc[6], sha[6], "SKIP_REDUNDANT_NOTEBOOK", "Notebook 横线模板，无正文。", 7),
        disp(loc[7], sha[7], "SKIP_REDUNDANT_NOTEBOOK", "科楚/钟楚计划笔记页标题条+空白横线。", 8),
        disp(loc[8], sha[8], "SKIP_REDUNDANT_NOTEBOOK", "与 visual_pages[6] 同图。", 9),
        disp(loc[9], sha[9], "SKIP_REDUNDANT_NOTEBOOK", "同类空白笔记页。", 10),
        disp(loc[10], sha[10], "SKIP_REDUNDANT_NOTEBOOK", "与 visual_pages[6] 同图。", 11),
        disp(loc[11], sha[11], "SKIP_REDUNDANT_NOTEBOOK", "同类空白笔记页。", 12),
        disp(loc[12], sha[12], "SKIP_REDUNDANT_NOTEBOOK", "与 visual_pages[6] 同图。", 13),
        disp(loc[13], sha[13], "SKIP_REDUNDANT_NOTEBOOK", "同类空白笔记页。", 14),
        disp(loc[14], sha[14], "SKIP_BLANK", "空白页。", 15),
        disp(loc[15], sha[15], "SKIP_REDUNDANT_BRANDING", "尾页品牌底图。", 16),
    ]
    facts = [
        fact(
            "visual_pages[2] 标题「新东方企业文化」，结构与用语同族五段：愿景、使命、核心价值观、企业文化、新东方精神；"
            "右侧英文 A BIGGER WORLD / A BETTER YOU。本 Source 为 image-only PDF。",
            loc[2], sha[2],
        ),
        fact(
            "visual_pages[4] 标题「课程概览」，分「线下课程」与「线上课程」两表。"
            "线下可见上午主题授课（企业文化、员工福利与发展）；下午暖场互动（自我介绍+破冰游戏）、主题授课（员工分享）、"
            "互动问答（匿名答疑）、考核（出门测+满意度调研）。"
            "线上为自主学习：团队合作、问题解决、表达沟通、自我认知、职业素养，考核为学习心得提交。",
            loc[4], sha[4],
        ),
    ]
    tables = [
        {
            "claim_type": "explicit_fact",
            "content": "visual_pages[4] 课程概览含线下（上午/下午分节）与线上自主学习两张日程表，考核行分别为出门测+满意度调研、学习心得提交。",
            "evidence_locators": [loc[4]],
            "image_sha256": sha[4],
            "not_in_structured_reader": True,
        }
    ]
    observations = [
        {
            "claim_type": "explicit_fact",
            "content": "visual_pages[5]「往期精彩集锦」照片墙，图注含暖场互动、主题授课、互动问答、结课考核；照片不承载可核验数字表。",
            "evidence_locators": [loc[5]],
            "image_sha256": sha[5],
        }
    ]
    skipped = [
        {"why": "空白页 visual_pages[1][14]。", "evidence_locators": [loc[1], loc[14]]},
        {"why": "Notebook 横线模板页仅有计划名标题条与装饰钟/塔，无项目级正文表。", "evidence_locators": [loc[6], loc[7], loc[8], loc[9], loc[10], loc[11], loc[12], loc[13]]},
        {"why": "尾页品牌底图。", "evidence_locators": [loc[15]]},
        {"why": "封面与成长地图先前已 grounded。", "evidence_locators": [loc[0], loc[3]]},
    ]
    ambiguities = [
        {
            "claim_type": "weak_inference",
            "content": "本手册课程概览为线下一日+线上自学结构，与另一 Source 的三日表不同；不跨源合并解释。",
            "evidence_locators": [loc[4]],
        }
    ]
    return {
        "visual_inputs": inputs,
        "grounded_visual_facts": facts,
        "table_observations": tables,
        "visual_observations": observations,
        "ambiguities": ambiguities,
        "already_grounded_skipped": skipped,
        "page_dispositions": dispositions,
    }


def build_519110(source: dict, reader: dict) -> dict:
    emb = reader["embedded_objects"]
    sha = [o["sha256"] for o in emb]
    loc = [f"embedded_objects[{i}]" for i in range(len(emb))]
    inputs = [_emb_input(reader, i) for i in range(1, 8)]
    dispositions = [
        disp(loc[0], sha[0], "SKIP_EMPTY_OBJECT", "embedded_objects[0] 为空 media（空文件 sha）。"),
        disp(loc[1], sha[1], "PRIOR_REVIEWED_GROUNDED", "学情分析表，先前已核验。"),
        disp(loc[2], sha[2], "PRIOR_REVIEWED_GROUNDED", "服务流程图，先前已核验。"),
        disp(loc[3], sha[3], "REVIEWED_GROUNDED", "课堂反馈样例仪表盘（上机讲图/本讲练习/内容总结）。"),
        disp(loc[4], sha[4], "REVIEWED_GROUNDED", "课堂反馈样例续页（知识点掌握/课后任务/作答明细）。"),
        disp(loc[5], sha[5], "REVIEWED_GROUNDED", "试卷分析样例仪表盘。"),
        disp(loc[6], sha[6], "REVIEWED_GROUNDED", "学情阶段报告/专属阶段性规划报告样例。"),
        disp(loc[7], sha[7], "REVIEWED_GROUNDED", "老师介绍用「教师培训·旅程」四宫格资质图。"),
    ]
    facts = [
        fact(
            "embedded_objects[3] 为「课堂反馈」系统截图样例，页眉可见服务流程/课堂反馈样例。"
            "界面分栏含「上机讲图」（完成率、正确率进度条与题号色块）、「本讲课堂练习」（环形正确率）、「本讲内容总结」及手写讲义缩略图。"
            "该图是一对一 SOP 的工具样例：说明顾问应收集的课堂反馈模块，不是项目汇总 KPI。"
            "样例中的学员姓名与具体百分比不升格为项目级指标。",
            loc[3], sha[3],
        ),
        fact(
            "embedded_objects[4] 为课堂反馈样例续屏，模块可见：知识点掌握情况（星级）、本讲课后任务、下讲预告、课堂表现、教师寄语，"
            "以及上机操作/本讲练习作答明细（对错与分步解答）。用于展示周反馈模板结构。样例作答分数不作为项目 KPI。",
            loc[4], sha[4],
        ),
        fact(
            "embedded_objects[5] 为「服务流程 试卷分析样例」仪表盘。可见结构：学员档案卡、题型得分条、难度得分、知识点明细、"
            "分专题掌握建议。对应手册「试卷诊断：卷面得分情况，各个板块得分率会有详细的呈现」。"
            "样例卷面分数与知识点百分比不作为项目 KPI。",
            loc[5], sha[5],
        ),
        fact(
            "embedded_objects[6] 为「服务流程 专属阶段性规划报告」/学情阶段报告样例。"
            "可见汇总卡（累计作业题目、累计作业批改、平均正确率）、老师寄语、日常答题折线、分知识点正确率与掌握程度星级、学习建议。"
            "对应手册「汇总本月所有课程表现、知识点掌握情况、测试成绩」。样例中的个人累计值不作为项目 KPI。",
            loc[6], sha[6],
        ),
        fact(
            "embedded_objects[7] 为老师介绍用四宫格「教师培训 / 旅程」信息图，非课堂仪表盘。"
            "四块标题可读：教师培训（全年课时总量口径）、教师批磨课（次数口径）、教师刷题（题量口径）、名校行/海外行交流赋能"
            "（可见华师营、华科营、武大营及新加坡、英国学习等条目）。"
            "该图用于展示师资培训旅程，其中全年小时/次数为图上印刷口径；不把未看清的细项数字补写。",
            loc[7], sha[7],
        ),
    ]
    tables = [
        {
            "claim_type": "explicit_fact",
            "content": "embedded_objects[7] 四宫格把教师发展拆成培训课时、批磨课次数、刷题量、交流赋能四类；细项含统招通识、学科示范、业务技能、职业文化素养，以及 1v1 批磨课、教研专项、赛课阅兵、寒暑假抽课，功底测/套卷/讲义刷题。",
            "evidence_locators": [loc[7]],
            "image_sha256": sha[7],
            "not_in_structured_reader": True,
        }
    ]
    observations = []
    skipped = [
        {"why": "embedded_objects[0] 空文件，无图像。", "evidence_locators": [loc[0]]},
        {"why": "image1/image2 先前已 grounded（学情分析表、服务流程图）。", "evidence_locators": [loc[1], loc[2]]},
        {"why": "DOCX 53 张 native-libreoffice 页对应 505 text_blocks，正文已结构化；本轮只补嵌入仪表盘/师资图。", "evidence_locators": ["text_blocks"]},
    ]
    ambiguities = [
        {
            "claim_type": "weak_inference",
            "content": "课堂反馈/试卷分析/阶段报告截图含真实样式的学员样例数据；只采信界面模块名与用途，不把样例姓名与分数写入项目指标。",
            "evidence_locators": [loc[3], loc[4], loc[5], loc[6]],
        },
        {
            "claim_type": "weak_inference",
            "content": "教师培训四宫格上的全年小时/次数印刷清晰者已记结构；未逐格核验的加总关系不交叉验算。",
            "evidence_locators": [loc[7]],
        },
    ]
    return {
        "visual_inputs": inputs,
        "grounded_visual_facts": facts,
        "table_observations": tables,
        "visual_observations": observations,
        "ambiguities": ambiguities,
        "already_grounded_skipped": skipped,
        "page_dispositions": dispositions,
    }


BUILDERS = {
    "513687723938@1": build_513687,
    "487369767128@1": build_487128,
    "487369767133@1": build_487133,
    "519110126421@1": build_519110,
}


def _count_disp(rows: list[dict]) -> dict[str, int]:
    reviewed = skipped = unread = prior = 0
    for row in rows:
        st = row.get("status") or ""
        if st.startswith("PRIOR_"):
            prior += 1
        elif st.startswith("REVIEWED"):
            reviewed += 1
        elif st.startswith("UNREADABLE"):
            unread += 1
        elif st.startswith("SKIP"):
            skipped += 1
    return {
        "reviewed": reviewed,
        "skipped": skipped,
        "unreadable": unread,
        "prior": prior,
        "total": len(rows),
    }


def main() -> int:
    q = _queue()
    STORE.mkdir(parents=True, exist_ok=True)
    deep = {
        "schema": "paw-cursor-visual-deep-review-checkpoint-v1/1",
        "model": admission.DEFAULT_MODEL,
        "model_id": admission.DEFAULT_MODEL_ID,
        "started_at": admission.utc_now(),
        "status": "IN_PROGRESS",
        "sources": {},
        "formal_state_bypass": 0,
        "gpt_brain_mutated_by_cursor": "NO",
        "ui_freeze_touched": "NO",
    }
    persist_records = {}
    for svid, builder in BUILDERS.items():
        source = q[svid]
        reader = _reader(source)
        payload = builder(source, reader)
        record = persist_source_review(
            source=source,
            visual_inputs=payload["visual_inputs"],
            grounded_visual_facts=payload["grounded_visual_facts"],
            table_observations=payload["table_observations"],
            visual_observations=payload["visual_observations"],
            ambiguities=payload["ambiguities"],
            already_grounded_skipped=payload["already_grounded_skipped"],
            page_dispositions=payload["page_dispositions"],
            source_status="VISUAL_SUPPLEMENTED",
            merge_existing=True,
        )
        persist_records[svid] = record
        counts = _count_disp(payload["page_dispositions"])
        last = payload["visual_inputs"][-1]
        deep["sources"][svid] = {
            "status": "COMPLETE",
            "canonical_path": record.get("path"),
            "mirror": record.get("mirror"),
            "fact_count_after_merge": record.get("fact_count"),
            "skipped_repeat": record.get("skipped_repeat"),
            "page_image_counts": counts,
            "last_locator": last["locator"],
            "last_image": last["image_path"],
            "last_image_sha256": last["image_sha256"],
            "dispositions": payload["page_dispositions"],
        }
        print(svid, record.get("path"), "facts", record.get("fact_count"), counts)
    deep["status"] = "COMPLETED"
    deep["updated_at"] = admission.utc_now()
    admission.atomic_write_json(DEEP_CKPT, deep)
    state = load_checkpoint(STORE)
    state["deep_review"] = {
        "checkpoint": str(DEEP_CKPT),
        "status": "COMPLETED",
        "sources": list(BUILDERS),
        "updated_at": deep["updated_at"],
    }
    save_checkpoint(state, STORE)
    print("wrote", DEEP_CKPT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
