# -*- coding: utf-8 -*-
"""Patch paw_console.js with all UI bug fixes and cruise enhancements:
1. Fix squashed checkboxes and labels in Batch Review (Issue 1)
2. Fix tab button currentTarget dataset extraction and Evolution safe rendering (Issue 3)
3. Enhance Cruise UI with progress feedback, Latest Cruise Report card, and Deep Cruise button (Issue 4)
"""
import re

TARGET_FILE = r"d:\PAW\paw-beta\frontend\dist\paw_console.js"

with open(TARGET_FILE, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Update STYLES to enforce checkbox sizing and prevent text squashing
CSS_RESET = """
    #paw-modal-box input[type="checkbox"] {
      width: 16px !important;
      height: 16px !important;
      min-width: 16px !important;
      max-width: 16px !important;
      flex-shrink: 0 !important;
      margin: 0 !important;
      cursor: pointer;
      accent-color: #B66F51;
    }
    .paw-batch-group-header {
      display: flex;
      align-items: center;
      gap: 10px;
      white-space: nowrap !important;
      overflow: hidden;
    }
    .paw-batch-group-header span,
    .paw-batch-group-header label,
    .paw-batch-group-header .paw-badge {
      white-space: nowrap !important;
      flex-shrink: 0 !important;
    }
    .paw-cruise-report {
      background: #FAF7F1;
      border: 1px solid #E8E2D8;
      border-radius: 8px;
      padding: 14px 18px;
      margin-bottom: 18px;
    }
"""

if "#paw-modal-box input[type=\"checkbox\"]" not in code:
    code = code.replace(
        "    .paw-event-item {",
        CSS_RESET + "\n    .paw-event-item {"
    )

# 2. Fix tab button click listener to use e.currentTarget safely
OLD_TAB_CLICK = """  document.querySelectorAll(".paw-tab-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      document.querySelectorAll(".paw-tab-btn").forEach((b) => b.classList.remove("active"));
      e.target.classList.add("active");
      currentTab = e.target.dataset.tab;
      if (scannerTimer) { clearInterval(scannerTimer); scannerTimer = null; }
      loadTab(currentTab);
    });
  });"""

NEW_TAB_CLICK = """  document.querySelectorAll(".paw-tab-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const targetBtn = e.currentTarget || btn;
      const tabName = targetBtn.dataset.tab;
      if (!tabName) return;
      document.querySelectorAll(".paw-tab-btn").forEach((b) => b.classList.remove("active"));
      targetBtn.classList.add("active");
      currentTab = tabName;
      if (scannerTimer) { clearInterval(scannerTimer); scannerTimer = null; }
      loadTab(currentTab);
    });
  });"""

if OLD_TAB_CLICK in code:
    code = code.replace(OLD_TAB_CLICK, NEW_TAB_CLICK)

# 3. Fix batch review group header in HTML
OLD_GROUP_HEADER = """              <div style="padding: 10px 14px; background: #FCFAF6; border-bottom: 1px solid #E8E2D8; display: flex; justify-content: space-between; align-items: center;">
                <div style="display: flex; align-items: center; gap: 8px;">
                  <input type="checkbox" class="chk-group" data-group="${gIdx}" />
                  <span style="font-weight: 600; font-size: 13.5px; color: #302B26;">${g.group_name}</span>
                  <span class="paw-badge paw-badge-gray">${g.count} 条候选</span>
                </div>
                <button class="paw-btn-secondary btn-select-high-conf" data-group="${gIdx}" style="padding: 3px 8px; font-size: 11.5px;">勾选高置信度</button>
              </div>"""

NEW_GROUP_HEADER = """              <div style="padding: 10px 14px; background: #FCFAF6; border-bottom: 1px solid #E8E2D8; display: flex; justify-content: space-between; align-items: center;">
                <div class="paw-batch-group-header" style="display: flex; align-items: center; gap: 10px; white-space: nowrap !important; flex-shrink: 0;">
                  <input type="checkbox" class="chk-group" data-group="${gIdx}" style="width: 16px !important; height: 16px !important; min-width: 16px !important; flex-shrink: 0 !important; margin: 0 !important;" />
                  <span style="font-weight: 600; font-size: 13.5px; color: #302B26; white-space: nowrap !important; flex-shrink: 0 !important;">${g.group_name}</span>
                  <span class="paw-badge paw-badge-gray" style="white-space: nowrap !important; flex-shrink: 0 !important;">${g.count} 条候选</span>
                </div>
                <button class="paw-btn-secondary btn-select-high-conf" data-group="${gIdx}" style="padding: 3px 8px; font-size: 11.5px; white-space: nowrap !important; flex-shrink: 0 !important;">勾选高置信度</button>
              </div>"""

if OLD_GROUP_HEADER in code:
    code = code.replace(OLD_GROUP_HEADER, NEW_GROUP_HEADER)

# Also ensure table checkboxes have explicit sizing
code = code.replace(
    '<input type="checkbox" class="chk-item chk-group-${gIdx}"',
    '<input type="checkbox" class="chk-item chk-group-${gIdx}" style="width: 16px !important; height: 16px !important; min-width: 16px !important; flex-shrink: 0 !important;"'
)

# Fix chk-select-all toolbar container
code = code.replace(
    '<input type="checkbox" id="chk-select-all" /> 全选所有条目',
    '<input type="checkbox" id="chk-select-all" style="width: 16px !important; height: 16px !important; min-width: 16px !important; max-width: 16px !important; flex-shrink: 0 !important; margin: 0 !important;" /> <span style="white-space: nowrap !important; flex-shrink: 0 !important;">全选所有条目</span>'
)
code = code.replace(
    '<div style="display: flex; align-items: center; gap: 12px;">',
    '<div style="display: flex; align-items: center; gap: 12px; white-space: nowrap !important; flex-shrink: 0 !important;">'
)

# 4. Enhance Status (巡航与快照) tab with full Cruise Summary Report and actions
STATUS_TAB_REGEX = r'\} else if \(tab === "status"\) \{.*?(?=\} else if \(tab === "evolution"\))'

NEW_STATUS_TAB = """} else if (tab === "status") {
      const data = await fetch("/v1/local/runtime/status").then((r) => r.json()).catch(() => ({}));
      const sched = data.scheduler || {};
      const events = data.recent_events || [];
      const report = sched.latest_cruise_result;

      tabContent.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
          <div>
            <h4 style="margin: 0; font-size: 15px; font-weight: 600; color: #302B26;">巡航与 Runtime 运行状态</h4>
            <div style="font-size: 12px; color: #756E65; margin-top: 2px;">
              后台自动巡航监控工作文档，内置 AI 深度分析项目进展并提炼 30 秒说明
            </div>
          </div>
          <div style="display: flex; gap: 8px;">
            <button class="paw-btn-secondary" id="btn-trigger-deep-cruise" style="font-size: 12.5px; padding: 6px 14px;">
              深度巡航 (AI提炼项目)
            </button>
            <button class="paw-btn-primary" id="btn-trigger-cruise" style="font-size: 12.5px; padding: 6px 14px;">
              立即执行一次巡航
            </button>
          </div>
        </div>

        ${report ? `
        <div class="paw-cruise-report" style="background: #FAF7F1; border: 1px solid #E8E2D8; border-radius: 8px; padding: 14px 18px; margin-bottom: 16px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <span style="font-weight: 600; font-size: 13.5px; color: #B66F51;">
              最新巡航成果简报 (${report.completed_at ? new Date(report.completed_at).toLocaleTimeString() : "刚刚"})
            </span>
            <span class="paw-badge paw-badge-pass">耗时 ${report.elapsed_seconds || 0} 秒</span>
          </div>
          <p style="margin: 0 0 10px 0; font-size: 12.5px; color: #302B26; line-height: 1.5;">
            ${report.summary || "巡航完成，各模块状态正常。"}
          </p>
          <div style="display: flex; gap: 16px; font-size: 12px; color: #756E65;">
            <span>工作文件: <b style="color:#302B26;">${report.files_total || 0}</b> 个</span>
            <span>新解析: <b style="color:#648166;">${report.files_analyzed || 0}</b> 个</span>
            <span>保持最新: <b style="color:#756E65;">${report.files_unchanged || 0}</b> 个</span>
            <span>AI提炼项目: <b style="color:#B66F51;">${report.projects_analyzed || 0}</b> 个</span>
            <span>待审候选: <b style="color:#A47A42;">${report.candidates_produced || 0}</b> 条</span>
          </div>
        </div>
        ` : `
        <div id="cruise-running-notice" style="display: none; background: #FAF7F1; border: 1px solid #E8E2D8; border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; color: #B66F51; font-size: 13px;">
          正在巡航分析本地文档并调用内置 AI 深度提炼项目进展，请稍候...
        </div>
        `}

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 20px;">
          <div class="paw-metric-card">
            <div style="color: #756E65; font-size: 12px;">当前调度模式</div>
            <div style="font-weight: 600; font-size: 15px; margin-top: 4px;">
              ${sched.mode === "realtime" ? "实时监听 (Real-time)" : sched.mode === "hourly" ? "每小时巡航 (Hourly)" : sched.mode}
            </div>
          </div>
          <div class="paw-metric-card">
            <div style="color: #756E65; font-size: 12px;">巡航运行状态</div>
            <div id="cruise-live-status" style="font-weight: 600; font-size: 15px; margin-top: 4px; color: ${sched.status === "running" ? "#B66F51" : "#648166"}">
              ${sched.status === "running" ? "正在巡航分析中..." : "空闲待命 (Idle)"}
            </div>
          </div>
          <div class="paw-metric-card">
            <div style="color: #756E65; font-size: 12px;">累计巡航次数 / 分析文件</div>
            <div style="font-weight: 600; font-size: 15px; margin-top: 4px;">
              ${sched.total_cruises || 0} 次 / ${sched.total_files_analyzed || 0} 个
            </div>
          </div>
          <div class="paw-metric-card">
            <div style="color: #756E65; font-size: 12px;">工作目录现有文件</div>
            <div style="font-weight: 600; font-size: 15px; margin-top: 4px;">
              ${sched.files_in_directory || 0} 个文件
            </div>
          </div>
        </div>

        <h5 style="margin: 0 0 10px 0; font-size: 13.5px; color: #756E65;">Event Bus 实时事件流 (最近活动):</h5>
        <div style="max-height: 220px; overflow-y: auto;">
          ${
            events.length === 0
              ? "<p style='color:#A1998E; font-size:12px;'>暂无事件记录</p>"
              : events.map((e) => `
                <div class="paw-event-item">
                  <span class="paw-badge ${e.event_type.includes("CRUISE") ? "paw-badge-pass" : "paw-badge-warn"}">${e.event_type}</span>
                  <span style="color: #756E65; margin-left: 8px;">[${new Date(e.timestamp * 1000).toLocaleTimeString()}]</span>
                  <span style="margin-left: 8px; font-weight: 500;">${e.payload?.summary || e.payload?.file || e.payload?.trigger || JSON.stringify(e.payload).slice(0, 70)}</span>
                </div>
              `).join("")
          }
        </div>
      `;

      async function triggerCruiseAction(force) {
        const btn1 = document.getElementById("btn-trigger-cruise");
        const btn2 = document.getElementById("btn-trigger-deep-cruise");
        if (btn1) { btn1.disabled = true; btn1.innerText = "巡航中..."; }
        if (btn2) { btn2.disabled = true; btn2.innerText = "AI分析中..."; }
        const statusElem = document.getElementById("cruise-live-status");
        if (statusElem) {
          statusElem.innerText = force ? "正在全量深度巡航并由 AI 提炼项目..." : "正在巡航分析工作文档与提炼项目...";
          statusElem.style.color = "#B66F51";
        }

        try {
          const res = await fetch("/v1/local/runtime/cruise/trigger", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ force_rescan: force, analyze_projects: true })
          }).then(r => r.json());

          triggerPawRefresh();
          loadTab("status");
        } catch (err) {
          alert("巡航触发失败: " + err);
          loadTab("status");
        }
      }

      document.getElementById("btn-trigger-cruise").addEventListener("click", () => triggerCruiseAction(false));
      document.getElementById("btn-trigger-deep-cruise").addEventListener("click", () => triggerCruiseAction(true));

    """

code = re.sub(STATUS_TAB_REGEX, NEW_STATUS_TAB, code, flags=re.DOTALL)

# 5. Fix Evolution Tab to guard suggestions safely and avoid any possible rendering crash
EVO_TAB_REGEX = r'\} else if \(tab === "evolution"\) \{.*?(?=\} else if \(tab === "doctor"\))'

NEW_EVO_TAB = """} else if (tab === "evolution") {
      tabContent.innerHTML = `<p style="color: #756E65;">正在加载知识资产演进与健康图谱...</p>`;
      try {
        const healthData = await fetch("/v1/local/evolution/health").then((r) => r.json()).catch(() => ({}));
        const graphData = await fetch("/v1/local/evolution/graph").then((r) => r.json()).catch(() => ({}));
        const pages = healthData.pages || [];
        const score = healthData.overall_score || 0;
        const grade = healthData.overall_grade || "正常";

        tabContent.innerHTML = `
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
            <div>
              <h4 style="margin: 0; font-size: 15px; font-weight: 600; color: #302B26;">知识健康度与演进建议</h4>
              <div style="font-size: 12px; color: #756E65; margin-top: 2px;">让个人工作过程持续转化为可信、可维护、可演进的工作资产</div>
            </div>
            <button class="paw-btn-primary" id="btn-refresh-evo" style="font-size: 12.5px; padding: 6px 14px;">重新评估健康度</button>
          </div>

          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 18px;">
            <div class="paw-metric-card">
              <div style="color: #756E65; font-size: 12px;">知识资产整体健康度</div>
              <div style="display: flex; align-items: baseline; gap: 8px; margin-top: 4px;">
                <span style="font-size: 26px; font-weight: 700; color: ${score >= 80 ? '#648166' : score >= 60 ? '#B66F51' : '#A47A42'}">${score}</span>
                <span style="color: #756E65; font-size: 13px;">/ 100 分</span>
                <span class="paw-badge ${score >= 70 ? 'paw-badge-pass' : 'paw-badge-warn'}" style="margin-left: 6px;">${grade}</span>
              </div>
              <div style="font-size: 11.5px; color: #756E65; margin-top: 6px;">
                已评估知识页: ${healthData.total_pages_evaluated || 0} 条 (优秀: ${healthData.distribution?.["优秀"] || 0}, 需维护: ${healthData.distribution?.["需维护"] || 0})
              </div>
            </div>

            <div class="paw-metric-card">
              <div style="color: #756E65; font-size: 12px;">实体上下文图谱 (Context Graph)</div>
              <div style="display: flex; align-items: baseline; gap: 8px; margin-top: 4px;">
                <span style="font-size: 26px; font-weight: 700; color: #B66F51;">${graphData.total_nodes || 0}</span>
                <span style="color: #756E65; font-size: 13px;">实体节点</span>
                <span style="font-size: 20px; font-weight: 600; color: #756E65; margin-left: 8px;">${graphData.total_edges || 0}</span>
                <span style="color: #756E65; font-size: 13px;">跨域关联</span>
              </div>
              <div style="font-size: 11.5px; color: #756E65; margin-top: 6px;">
                覆盖项目、知识、待办、关键决策与量化指标
              </div>
            </div>
          </div>

          <h5 style="margin: 0 0 10px 0; font-size: 13px; color: #302B26;">知识条目健康与演进建议 (按优先级排序):</h5>
          <div style="max-height: 220px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px;">
            ${pages.slice(0, 10).map((p) => {
              const suggs = p.suggestions || [];
              return `
              <div style="border: 1px solid #E8E2D8; border-radius: 6px; padding: 10px; background: #fff;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                  <span style="font-weight: 600; font-size: 13.5px; color: #302B26;">${p.title}</span>
                  <span class="paw-badge ${p.grade === '优秀' ? 'paw-badge-pass' : 'paw-badge-warn'}">${p.score}分 · ${p.grade}</span>
                </div>
                <div style="color: #756E65; font-size: 12px; margin-top: 4px;">
                  类别: <code>${p.knowledge_type}</code> · 距今未修改: ${p.age_days} 天 · 关联项目: ${p.links_count} 个
                </div>
                ${suggs.length > 0 ? `
                  <div style="margin-top: 6px; font-size: 12px; color: #A86348; background: #FAF7F1; padding: 4px 8px; border-radius: 4px;">
                    建议: ${suggs.join("；")}
                  </div>
                ` : ''}
              </div>
            `}).join("")}
          </div>
        `;

        document.getElementById("btn-refresh-evo").addEventListener("click", async () => {
          document.getElementById("btn-refresh-evo").innerText = "正在分析评估...";
          await fetch("/v1/local/evolution/diagnose", { method: "POST" });
          loadTab("evolution");
        });
      } catch (err) {
        tabContent.innerHTML = `<div style="color: #A65F58; padding: 20px;">知识演进图谱加载异常: ${err}</div>`;
      }

    """

code = re.sub(EVO_TAB_REGEX, NEW_EVO_TAB, code, flags=re.DOTALL)

with open(TARGET_FILE, "w", encoding="utf-8") as f:
    f.write(code)

print("paw_console.js successfully patched!")
