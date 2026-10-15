# -*- coding: utf-8 -*-
"""Refine paw_console.js to align 1:1 with PAW design language and tokens.
- Warm-neutral palette (canvas #F7F3EB, surface #FCFAF6, subtle #FAF7F1, borders #E8E2D8/#DED8CC)
- Terracotta accent (#B66F51 / #A86348)
- Clean sans-serif typography across modal and controls
- Zero jarring emojis (replaced with clean SVGs or text)
- Built-in real-time synchronization engine triggering 'paw-refresh' for Today page and badges
- Intercept fetch to enforce limit=500 for candidate queries
- Auto-injects subtle control center button into sidebar
"""
import re

TARGET_FILE = r"d:\PAW\paw-beta\frontend\dist\paw_console.js"

with open(TARGET_FILE, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Update STYLES
NEW_STYLES = """
    #paw-fab-btn {
      position: fixed;
      bottom: 20px;
      left: 20px;
      z-index: 9999;
      background: #FCFAF6;
      color: #756E65;
      border: 1px solid #E8E2D8;
      border-radius: 20px;
      padding: 6px 14px;
      font-size: 12px;
      font-weight: 500;
      box-shadow: 0 2px 8px rgba(48, 43, 38, 0.06);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.18s cubic-bezier(0.2, 0.8, 0.2, 1);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }
    #paw-fab-btn:hover {
      background: #FAF7F1;
      color: #302B26;
      border-color: #CDC5B6;
      box-shadow: 0 4px 12px rgba(48, 43, 38, 0.1);
    }
    #paw-modal-overlay {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(35, 30, 25, 0.4);
      backdrop-filter: blur(4px);
      z-index: 100000;
      align-items: center;
      justify-content: center;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }
    #paw-modal-box {
      background: #FCFAF6;
      width: 820px;
      max-width: 94vw;
      height: 82vh;
      max-height: 820px;
      border-radius: 8px;
      border: 1px solid #E8E2D8;
      box-shadow: 0 16px 40px rgba(48, 43, 38, 0.14), 0 2px 6px rgba(48, 43, 38, 0.04);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      color: #302B26;
    }
    .paw-modal-header {
      padding: 16px 22px;
      border-bottom: 1px solid #E8E2D8;
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: #FAF7F1;
      flex-shrink: 0;
    }
    .paw-modal-title {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size: 15px;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 8px;
      color: #302B26;
    }
    .paw-modal-close {
      background: none;
      border: none;
      font-size: 20px;
      cursor: pointer;
      color: #8C867D;
      line-height: 1;
      transition: color 0.15s;
    }
    .paw-modal-close:hover { color: #302B26; }
    .paw-nav-tabs {
      display: flex;
      border-bottom: 1px solid #E8E2D8;
      background: #FAF7F1;
      flex-shrink: 0;
      overflow-x: auto;
    }
    .paw-tab-btn {
      padding: 10px 16px;
      background: none;
      border: none;
      font-size: 13px;
      font-weight: 500;
      color: #756E65;
      cursor: pointer;
      border-bottom: 2px solid transparent;
      white-space: nowrap;
      transition: all 0.15s ease;
      font-family: inherit;
    }
    .paw-tab-btn:hover { color: #302B26; background: #F4F0E7; }
    .paw-tab-btn.active { color: #302B26; border-bottom: 2px solid #B66F51; background: #FCFAF6; font-weight: 600; }
    .paw-modal-body {
      padding: 22px 26px;
      overflow-y: auto;
      flex: 1;
      font-size: 13.5px;
      line-height: 1.6;
      background: #FCFAF6;
      color: #302B26;
      font-family: inherit;
    }
    .paw-field { margin-bottom: 15px; }
    .paw-field label { display: block; font-weight: 550; margin-bottom: 6px; color: #302B26; font-size: 13px; }
    .paw-field input, .paw-field select, .paw-field textarea {
      width: 100%;
      box-sizing: border-box;
      padding: 8px 12px;
      border: 1px solid #DED8CC;
      border-radius: 6px;
      font-size: 13px;
      color: #302B26;
      background: #FAF7F1;
      font-family: inherit;
      transition: border-color 0.15s, box-shadow 0.15s;
    }
    .paw-field input:focus, .paw-field select:focus, .paw-field textarea:focus {
      outline: none;
      border-color: #B66F51;
      background: #FCFAF6;
      box-shadow: 0 0 0 2px rgba(182, 111, 81, 0.15);
    }
    .paw-btn-primary {
      background: #B66F51;
      color: #FCFAF6;
      border: none;
      padding: 8px 16px;
      border-radius: 6px;
      font-weight: 550;
      cursor: pointer;
      font-size: 13px;
      font-family: inherit;
      transition: all 0.15s ease;
    }
    .paw-btn-primary:hover { background: #A86348; }
    .paw-btn-secondary {
      background: #FCFAF6;
      color: #756E65;
      border: 1px solid #E8E2D8;
      padding: 8px 15px;
      border-radius: 6px;
      font-weight: 500;
      cursor: pointer;
      font-size: 13px;
      font-family: inherit;
      transition: all 0.15s ease;
    }
    .paw-btn-secondary:hover { background: #FAF7F1; color: #302B26; border-color: #CDC5B6; }
    .paw-btn-danger {
      background: #F1DFDB;
      color: #A65F58;
      border: 1px solid #E7CDC7;
      padding: 8px 15px;
      border-radius: 6px;
      font-weight: 500;
      cursor: pointer;
      font-size: 13px;
      font-family: inherit;
      transition: all 0.15s ease;
    }
    .paw-btn-danger:hover { background: #E8CEC8; }
    .paw-badge { display: inline-block; padding: 2px 9px; border-radius: 12px; font-size: 11.5px; font-weight: 550; }
    .paw-badge-pass { background: #E5ECE3; color: #648166; border: 1px solid #D3DED1; }
    .paw-badge-warn { background: #F2E8D7; color: #A47A42; border: 1px solid #E7D7BC; }
    .paw-badge-fail { background: #F1DFDB; color: #A65F58; border: 1px solid #E7CDC7; }
    .paw-badge-info { background: #E2E9E5; color: #63766F; border: 1px solid #CEDAD3; }
    .paw-badge-gray { background: #F0EEE9; color: #6F6A61; border: 1px solid #E0DCD4; }
    .paw-progress-wrap {
      width: 100%;
      height: 8px;
      background: #ECE5DC;
      border-radius: 4px;
      overflow: hidden;
      margin: 12px 0;
    }
    .paw-progress-bar {
      height: 100%;
      background: #B66F51;
      width: 0%;
      transition: width 0.3s ease;
    }
    .paw-metric-card {
      background: #FAF7F1;
      border: 1px solid #E8E2D8;
      border-radius: 8px;
      padding: 12px 14px;
    }
    .paw-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12.5px;
    }
    .paw-table th {
      background: #FAF7F1;
      text-align: left;
      padding: 9px 12px;
      border-bottom: 1px solid #DED8CC;
      color: #756E65;
      font-weight: 600;
    }
    .paw-table td {
      padding: 9px 12px;
      border-bottom: 1px solid #E8E2D8;
      vertical-align: middle;
      color: #302B26;
    }
    .paw-table tr:hover { background: #FAF7F1; }
    .paw-event-item {
      padding: 8px 12px;
      background: #FAF7F1;
      border: 1px solid #E8E2D8;
      border-radius: 6px;
      margin-bottom: 6px;
      font-size: 12px;
      color: #302B26;
    }
"""

# Replace STYLES definition
code = re.sub(r'const STYLES = `.*?`;', f'const STYLES = `{NEW_STYLES}`;', code, flags=re.DOTALL)

# 2. Update MODAL_HTML
NEW_MODAL_HTML = """
    <button id="paw-fab-btn" title="PAW 本地控制中心">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
      <span>控制中心</span>
    </button>
    <div id="paw-modal-overlay">
      <div id="paw-modal-box">
        <div class="paw-modal-header">
          <div class="paw-modal-title">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
            <span>PAW 本地控制中心</span>
          </div>
          <button class="paw-modal-close" id="paw-close-btn" title="关闭">&times;</button>
        </div>
        <div class="paw-nav-tabs">
          <button class="paw-tab-btn active" data-tab="settings">系统与模型</button>
          <button class="paw-tab-btn" data-tab="scanner">文件夹扫描</button>
          <button class="paw-tab-btn" data-tab="batch_review">批量审核</button>
          <button class="paw-tab-btn" data-tab="status">巡航与快照</button>
          <button class="paw-tab-btn" data-tab="evolution">知识演进</button>
          <button class="paw-tab-btn" data-tab="doctor">系统状态</button>
          <button class="paw-tab-btn" data-tab="onboarding" style="display:none;" id="tab-btn-ob">引导</button>
        </div>
        <div class="paw-modal-body" id="paw-tab-content">
          <!-- Dynamically Injected -->
        </div>
      </div>
    </div>
"""

code = re.sub(r'const MODAL_HTML = `.*?`;', f'const MODAL_HTML = `{NEW_MODAL_HTML}`;', code, flags=re.DOTALL)

# 3. Clean emojis and strange symbols
replacements = [
    # Emojis in buttons & headers
    (r"⚙\s*PAW\s*控制中心", "控制中心"),
    (r"⚙\s*本地环境与模型全面配置", "本地环境与模型配置"),
    (r"📁\s*选择扫描目录", "选择扫描目录"),
    (r"📁\s*添加监控文件夹", "添加监控文件夹"),
    (r"💾\s*更改目录", "更改目录"),
    (r"⚡\s*测试当前模型连通性", "测试模型连通性"),
    (r"⚡\s*测试模型连通性", "测试模型连通性"),
    (r"➔\s*开始初始化", "开始初始化"),
    (r"➔\s*前往「批量审核」", "前往批量审核"),
    (r"🔄\s*检测到上次扫描的断点记录", "检测到上次扫描的断点记录"),
    (r"⏸️\s*暂停", "暂停"),
    (r"⏸️\s*扫描已暂停", "扫描已暂停"),
    (r"⏹️\s*终止扫描", "终止扫描"),
    (r"⚠️\s*跳过与解析异常清单", "跳过与解析异常清单"),
    (r"⚠️\s*如实说明：", "说明："),
    (r"✅\s*连接成功！", "连接成功！"),
    (r"❌\s*连接失败", "连接失败"),
    (r"✅\s*测试通过！", "测试通过！"),
    (r"❌\s*失败", "失败"),
    (r"✅\s*扫描全部完成", "扫描全部完成"),
    (r"✅\s*空闲待命\s*\(Idle\)", "空闲待命"),
    (r"✓\s*正常", "正常"),
    # Replace remaining emoji icons
    ("📁", ""),
    ("⚡", ""),
    ("⚙", ""),
    ("✅", "已就绪 "),
    ("❌", "未通过 "),
    ("⚠️", ""),
    ("🔄", ""),
    ("⏸️", ""),
    ("⏹️", ""),
    ("➔", ""),
    ("✓", "正常"),
]

for old, new in replacements:
    code = code.replace(old, new)

# 4. Color replacements for consistent warm neutrals
code = code.replace("#4F5D49", "#B66F51")  # Terracotta accent
code = code.replace("#3F4A39", "#A86348")  # Terracotta hover
code = code.replace("#62705A", "#B66F51")  # Focus accent
code = code.replace("#292824", "#302B26")  # Primary text

# 5. Add Live Synchronizer & Sidebar Integration at bottom
SYNC_LOGIC = """
  // PAW Live Refresh & Synchronizer
  function triggerPawRefresh() {
    try {
      window.dispatchEvent(new CustomEvent("paw-refresh"));
    } catch (e) {}
  }

  // Intercept fetch to automatically raise limit from 50 to 500 for candidate queries
  if (!window._paw_fetch_patched) {
    window._paw_fetch_patched = true;
    const _origFetch = window.fetch;
    window.fetch = async function (input, init) {
      try {
        let url = typeof input === "string" ? input : input && input.url ? input.url : "";
        if (url.includes("/v1/web/brain/review/candidates") && url.includes("limit=50")) {
          url = url.replace("limit=50", "limit=500");
          if (typeof input === "string") {
            input = url;
          } else if (input instanceof Request) {
            input = new Request(url, input);
          }
        }
      } catch (e) {}
      return _origFetch.apply(this, [input, init]);
    };
  }

  // Window focus & visibility change trigger instant refresh across all views
  window.addEventListener("focus", triggerPawRefresh);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") triggerPawRefresh();
  });
  setInterval(() => {
    if (document.visibilityState === "visible") triggerPawRefresh();
  }, 6000);

  // Integrate control center button cleanly into desktop sidebar
  function integrateWithSidebar() {
    const accountBox = document.querySelector(".sidebar-account");
    if (accountBox && !document.getElementById("paw-sidebar-entry")) {
      const entry = document.createElement("button");
      entry.id = "paw-sidebar-entry";
      entry.className = "text-button";
      entry.style.marginTop = "3px";
      entry.style.display = "flex";
      entry.style.alignItems = "center";
      entry.style.gap = "6px";
      entry.style.color = "#756E65";
      entry.style.fontSize = "12px";
      entry.style.padding = "3px 0";
      entry.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg> <span>控制中心</span>`;
      entry.addEventListener("click", () => {
        overlay.style.display = "flex";
        loadTab(currentTab);
      });
      accountBox.appendChild(entry);
    }
  }
  setInterval(integrateWithSidebar, 1500);

  // Hook into candidate approvals & batch review actions
  window.addEventListener("paw-candidate-action", triggerPawRefresh);
})();
"""

# Append sync logic before the closing of the IIFE
if "PAW Live Refresh & Synchronizer" not in code:
    code = re.sub(r'\s*\n\}\)\(\);\s*$', f"\n{SYNC_LOGIC}", code)

with open(TARGET_FILE, "w", encoding="utf-8") as f:
    f.write(code)

print("paw_console.js refined successfully!")
