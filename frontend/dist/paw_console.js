// PAW Personal Work OS v2.2 - Interactive Desktop Control Console & Onboarding
// Compliant with Task Specification v2.2: Zero-CLI, DPAPI security, Universal Provider, Non-blocking Scanner, Human Governed Review
(function () {
  const STYLES = `
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

    .paw-event-item {
      padding: 8px 12px;
      background: #FAF7F1;
      border: 1px solid #E8E2D8;
      border-radius: 6px;
      margin-bottom: 6px;
      font-size: 12px;
      color: #302B26;
    }
`;

  const styleEl = document.createElement("style");
  styleEl.innerHTML = STYLES;
  document.head.appendChild(styleEl);

  const MODAL_HTML = `
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
`;

  const container = document.createElement("div");
  container.innerHTML = MODAL_HTML;
  document.body.appendChild(container);

  const overlay = document.getElementById("paw-modal-overlay");
  const tabContent = document.getElementById("paw-tab-content");
  let currentTab = "settings";
  let scannerTimer = null;

  document.getElementById("paw-fab-btn").addEventListener("click", () => {
    overlay.style.display = "flex";
    loadTab(currentTab);
  });

  document.getElementById("paw-close-btn").addEventListener("click", () => {
    overlay.style.display = "none";
    if (scannerTimer) { clearInterval(scannerTimer); scannerTimer = null; }
  });

  document.querySelectorAll(".paw-tab-btn").forEach((btn) => {
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
  });

  // Handle auto-opening via URL query parameter (e.g. ?open_paw_console=settings)
  try {
    const urlParams = new URLSearchParams(window.location.search);
    const autoTab = urlParams.get("open_paw_console");
    if (autoTab) {
      setTimeout(() => {
        const tabBtn = document.querySelector(`.paw-tab-btn[data-tab="${autoTab}"]`);
        if (tabBtn) {
          document.querySelectorAll(".paw-tab-btn").forEach((b) => b.classList.remove("active"));
          tabBtn.classList.add("active");
          currentTab = autoTab;
        }
        overlay.style.display = "flex";
        loadTab(currentTab);
      }, 350);
    }
  } catch (e) {}

  function formatSeconds(secs) {
    if (!secs || secs <= 0) return "0秒";
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    if (m > 0) return `${m}分${s}秒`;
    return `${s}秒`;
  }

  function getProviderPresets(presetKey) {
    const PRESETS = {
      deepseek: {
        base_url: "https://api.deepseek.com",
        model: "deepseek-chat",
      },
      qwen: {
        base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: "qwen-plus",
      },
      openai: {
        base_url: "https://api.openai.com/v1",
        model: "gpt-4o",
      },
      custom: {
        base_url: "https://your-api-gateway.com/v1",
        model: "claude-3-5-sonnet",
      },
      offline: {
        base_url: "",
        model: "rule-engine",
      },
    };
    return PRESETS[presetKey] || PRESETS.deepseek;
  }

  let _cruisePollTimer = null;

  async function loadTab(tab) {
    if (tab !== "status" && _cruisePollTimer) {
      clearInterval(_cruisePollTimer);
      _cruisePollTimer = null;
    }
    if (tab === "onboarding") {
      const res = await fetch("/v1/local/onboarding").then((r) => r.json()).catch(() => ({}));
      tabContent.innerHTML = `
        <div style="background: #FAF7F1; border: 1px solid #E5ECE3; padding: 14px 18px; border-radius: 8px; margin-bottom: 18px;">
          <h4 style="margin: 0 0 6px 0; color: #648166; font-size: 15px;"> 欢迎使用 PAW Personal Work OS (Windows 本地化版)</h4>
          <p style="margin: 0; color: #648166; font-size: 12.5px; line-height: 1.6;">
            专为普通员工打造的严肃 AI 工作系统。您无需学习 Prompt，无需向聊天窗口粘贴文档。<br/>
            只要在本地工作文件夹正常存取 Word/Excel/PPT/PDF，PAW 会自动解析高保真证据、识别项目行动与知识，经您一键核准后沉淀为持久资产。
          </p>
        </div>

        <div class="paw-field">
          <label>1. 本地工作文件夹目录 (平时保存日常办公文档的文件夹)</label>
          <div style="display: flex; gap: 8px;">
            <input type="text" id="ob-work-dir" value="${res.work_dir || "D:\\PAW\\paw-beta\\data\\sources"}" style="flex: 1;" />
            <button class="paw-btn-secondary" id="ob-btn-pick" style="white-space: nowrap;"> 选择文件夹</button>
            <button class="paw-btn-secondary" id="ob-btn-prescan" style="white-space: nowrap;"> 检查此目录</button>
          </div>
          <div id="ob-prescan-result" style="margin-top: 6px; font-size: 12px; color: #756E65;"></div>
        </div>

        <div class="paw-field">
          <label>2. 选择 AI 模型服务 (Universal OpenAI 协议兼容)</label>
          <select id="ob-provider-preset">
            <option value="deepseek" ${res.provider === "deepseek" ? "selected" : ""}>DeepSeek (推荐: 极高推理解析性价比)</option>
            <option value="qwen" ${res.provider === "qwen" ? "selected" : ""}>通义千问 Qwen (阿里云兼容端点)</option>
            <option value="openai" ${res.provider === "openai" ? "selected" : ""}>OpenAI (GPT-4o 官方标准)</option>
            <option value="custom" ${res.provider === "custom" ? "selected" : ""}>自定义 OpenAI 兼容接口 / Claude 兼容网关</option>
            <option value="offline" ${res.provider === "offline" ? "selected" : ""}>纯离线规则推理 (无需 API Key, 离线安全保底)</option>
          </select>
        </div>

        <div id="ob-model-config-panel">
          <div class="paw-field">
            <label>API Key (Windows DPAPI 强加密存储，日志与磁盘永不泄露明文)</label>
            <input type="password" id="ob-api-key" placeholder="${res.has_api_key ? `已设置: ${res.key_mask}` : "请输入 sk-..."}" />
          </div>
          <div class="paw-field">
            <label>Base URL (API 接口基地址)</label>
            <input type="text" id="ob-base-url" value="https://api.deepseek.com" />
          </div>
          <div class="paw-field">
            <label>Model Name (模型标识名称)</label>
            <input type="text" id="ob-model" value="deepseek-chat" />
          </div>
          <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 12px;">
            <button class="paw-btn-secondary" id="ob-btn-test-conn"> 测试模型连通性</button>
            <div id="ob-conn-result" style="font-size: 12px; font-weight: 500;"></div>
          </div>
        </div>

        <div class="paw-field">
          <label>3. 资源限流模式 (首发扫描保护您的 Word/Excel 不卡顿)</label>
          <select id="ob-speed-mode">
            <option value="quiet" ${res.speed_mode === "quiet" ? "selected" : ""}>安静模式 (单线程最低优先级, 办公完全无感)</option>
            <option value="standard" ${res.speed_mode === "standard" || !res.speed_mode ? "selected" : ""}>标准模式 (默认推荐, 平衡处理速度与办公体验)</option>
            <option value="full" ${res.speed_mode === "full" ? "selected" : ""}>全速模式 (利用多核心加速扫描处理)</option>
          </select>
        </div>

        <div style="margin-top: 24px; padding-top: 16px; border-top: 1px solid #E8E2D8; display: flex; justify-content: space-between; align-items: center;">
          <span style="font-size: 12px; color: #756E65;">设置已采用 Windows 原生 DPAPI 加密隔离保护</span>
          <button class="paw-btn-primary" id="ob-submit-btn" style="padding: 10px 22px; font-size: 14px;">完成引导，开启 PAW 守护 </button>
        </div>
      `;

      // Preset change handler
      const presetSelect = document.getElementById("ob-provider-preset");
      const urlInput = document.getElementById("ob-base-url");
      const modelInput = document.getElementById("ob-model");
      const configPanel = document.getElementById("ob-model-config-panel");

      presetSelect.addEventListener("change", () => {
        const val = presetSelect.value;
        if (val === "offline") {
          configPanel.style.display = "none";
        } else {
          configPanel.style.display = "block";
          const p = getProviderPresets(val);
          urlInput.value = p.base_url;
          modelInput.value = p.model;
        }
      });

      // Prescan handler
      document.getElementById("ob-btn-prescan").addEventListener("click", async () => {
        const resEl = document.getElementById("ob-prescan-result");
        resEl.innerHTML = `<span style="color:#B66F51;">正在预检目标目录中的文件...</span>`;
        const dir = document.getElementById("ob-work-dir").value.trim();
        try {
          const data = await fetch("/v1/local/scanner/prescan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ directory: dir }),
          }).then((r) => r.json());
          resEl.innerHTML = `<span style="color:#648166; font-weight: 500;">共发现 <b>${data.total_files}</b> 个可解析办公文档 (Office/PDF)，预计首次解析耗时约 <b>${data.estimated_minutes} 分钟</b>。</span>`;
        } catch (e) {
          resEl.innerHTML = `<span style="color:#A65F58;">预检失败: ${e.message}</span>`;
        }
      });

      // Test connection handler
      document.getElementById("ob-btn-test-conn").addEventListener("click", async () => {
        const resEl = document.getElementById("ob-conn-result");
        resEl.innerHTML = `<span style="color:#B66F51;">正在发起探测请求...</span>`;
        const payload = {
          base_url: urlInput.value.trim(),
          api_key: document.getElementById("ob-api-key").value.trim(),
          model: modelInput.value.trim(),
        };
        try {
          const resp = await fetch("/v1/local/provider/test_connection", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          }).then((r) => r.json());
          if (resp.success) {
            resEl.innerHTML = `<span style="color:#648166;">连接成功！响应延迟: ${resp.latency_ms} ms (模型: ${resp.model_used})</span>`;
          } else {
            resEl.innerHTML = `<span style="color:#A65F58;">未通过  连接失败 [${resp.error_code}]: ${resp.error_message}</span>`;
          }
        } catch (e) {
          resEl.innerHTML = `<span style="color:#A65F58;">网络探测异常: ${e.message}</span>`;
        }
      });

      document.getElementById("ob-btn-pick")?.addEventListener("click", async () => {
        try {
          const res = await fetch("/v1/local/pick_folder").then((r) => r.json());
          if (res.ok && res.path) {
            const inp = document.getElementById("ob-work-dir");
            const parts = inp.value.trim() ? inp.value.trim().split(";").map((s) => s.trim()).filter(Boolean) : [];
            if (!parts.includes(res.path)) parts.push(res.path);
            inp.value = parts.join(";");
          }
        } catch (e) {}
      });

      // Submit handler
      document.getElementById("ob-submit-btn").addEventListener("click", async () => {
        const pVal = presetSelect.value;
        const key = document.getElementById("ob-api-key").value.trim();
        const payload = {
          work_dir: document.getElementById("ob-work-dir").value.trim(),
          provider: pVal,
          speed_mode: document.getElementById("ob-speed-mode").value,
          cruise_mode: "realtime",
        };
        if (pVal === "deepseek") {
          if (key) payload.deepseek_api_key = key;
          payload.deepseek_base_url = urlInput.value.trim();
          payload.deepseek_model = modelInput.value.trim();
        } else if (pVal === "qwen") {
          if (key) payload.qwen_api_key = key;
          payload.qwen_base_url = urlInput.value.trim();
          payload.qwen_model = modelInput.value.trim();
        } else if (pVal === "openai") {
          if (key) payload.openai_api_key = key;
          payload.openai_base_url = urlInput.value.trim();
          payload.openai_model = modelInput.value.trim();
        } else if (pVal === "custom") {
          if (key) payload.custom_api_key = key;
          payload.custom_base_url = urlInput.value.trim();
          payload.custom_model = modelInput.value.trim();
        }

        await fetch("/v1/local/onboarding", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        alert(" 新手引导配置成功！PAW 现已就绪。正在为您打开扫描与进度面板。");
        // switch tab to scanner
        document.querySelectorAll(".paw-tab-btn").forEach((b) => {
          b.classList.toggle("active", b.dataset.tab === "scanner");
        });
        currentTab = "scanner";
        loadTab("scanner");
      });

    } else if (tab === "scanner") {
      tabContent.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
          <div>
            <h4 style="margin: 0; font-size: 15px;"> 本地文件夹深度扫描与状态</h4>
            <div style="font-size: 12px; color: #756E65; margin-top: 2px;">高保真解析 Word / Excel / PPT / PDF，非阻塞运行，支持随时暂停与断点续扫</div>
          </div>
          <div id="scan-status-badge">
            <span class="paw-badge paw-badge-gray">初始化中...</span>
          </div>
        </div>

        <div style="background: #FCFAF6; border: 1px solid #E8E2D8; border-radius: 8px; padding: 16px; margin-bottom: 18px;">
          <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
            <span style="font-weight: 600;" id="scan-progress-label">处理进度: 0 / 0</span>
            <span style="color: #B66F51; font-weight: 600;" id="scan-progress-percent">0%</span>
          </div>
          <div class="paw-progress-wrap">
            <div class="paw-progress-bar" id="scan-progress-bar"></div>
          </div>
          <div style="display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: #756E65;">
            <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 60%;" id="scan-curr-file">
              当前处理: 待开始
            </div>
            <div>
              <span>已用时间: <b id="scan-elapsed">0秒</b></span> |
              <span>预计剩余: <b id="scan-eta">0秒</b></span>
            </div>
          </div>
        </div>

        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 18px;">
          <div class="paw-metric-card">
            <div style="color: #756E65; font-size: 11.5px;">成功解析</div>
            <div style="font-size: 18px; font-weight: 700; color: #648166; margin-top: 2px;" id="metric-success">0</div>
          </div>
          <div class="paw-metric-card">
            <div style="color: #756E65; font-size: 11.5px;">异常/跳过</div>
            <div style="font-size: 18px; font-weight: 700; color: #A47A42; margin-top: 2px;" id="metric-failed">0</div>
          </div>
          <div class="paw-metric-card">
            <div style="color: #756E65; font-size: 11.5px;">生成候选条目</div>
            <div style="font-size: 18px; font-weight: 700; color: #B66F51; margin-top: 2px;" id="metric-candidates">0</div>
          </div>
          <div class="paw-metric-card">
            <div style="color: #756E65; font-size: 11.5px;">限流模式</div>
            <div style="font-size: 13.5px; font-weight: 600; color: #302B26; margin-top: 4px;" id="metric-speed">标准</div>
          </div>
        </div>

        <div id="scan-resumable-banner" style="display: none; background: #F2E8D7; border: 1px solid #E7D7BC; border-radius: 8px; padding: 10px 14px; margin-bottom: 14px; font-size: 12.5px; color: #A47A42;">
           检测到上次扫描的断点记录：已有 <b id="scan-resumable-count">0</b> 个文件完成解析。点击「从断点继续扫描」可跳过已完成文件，无需从头开始。
        </div>
        <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 20px; flex-wrap: wrap;">
          <button class="paw-btn-primary" id="btn-scan-start"> 开始全量扫描</button>
          <button class="paw-btn-secondary" id="btn-scan-pause" style="display:none;"> 暂停扫描</button>
          <button class="paw-btn-primary" id="btn-scan-resume" style="display:none;"> 继续扫描</button>
          <button class="paw-btn-danger" id="btn-scan-stop" style="display:none;"> 终止扫描</button>
          <div style="margin-left: auto; display: flex; align-items: center; gap: 6px;">
            <span style="font-size: 12px; color: #756E65;">性能调节:</span>
            <select id="sel-runtime-speed" style="padding: 5px 10px; font-size: 12px; border: 1px solid #DED8CC; border-radius: 6px;">
              <option value="quiet">安静模式</option>
              <option value="standard" selected>标准模式</option>
              <option value="full">全速模式</option>
            </select>
          </div>
        </div>

        <div id="scan-summary-card" style="display: none; background: #FAF7F1; border: 1px solid #D3DED1; border-radius: 8px; padding: 14px 18px; margin-bottom: 18px;">
          <div style="font-weight: 600; color: #648166; font-size: 14px;"> 文件夹扫描完成！</div>
          <p style="margin: 6px 0 10px 0; color: #648166; font-size: 13px;" id="scan-summary-text"></p>
          <button class="paw-btn-primary" id="btn-goto-batch-review" style="font-size: 12.5px; padding: 6px 14px;">前往「智能批量审核」确认 </button>
        </div>

        <div style="margin-top: 10px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <h5 style="margin: 0; font-size: 13px; color: #756E65;"> 跳过与解析异常清单 (<span id="failed-count-title">0</span>)</h5>
            <span style="font-size: 11.5px; color: #A1998E;">加密/占用/超限等文件非阻塞跳过，不影响正常扫描</span>
          </div>
          <div style="border: 1px solid #E8E2D8; border-radius: 8px; max-height: 180px; overflow-y: auto;">
            <table class="paw-table" id="table-failed-files">
              <thead>
                <tr>
                  <th style="width: 40%;">文件名称/路径</th>
                  <th style="width: 25%;">跳过原因</th>
                  <th style="width: 25%;">建议处理</th>
                  <th style="width: 10%; text-align: center;">操作</th>
                </tr>
              </thead>
              <tbody id="tbody-failed-files">
                <tr><td colspan="4" style="text-align: center; color: #A1998E; padding: 16px;">暂无异常跳过文件</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      `;

      const badgeEl = document.getElementById("scan-status-badge");
      const progLabel = document.getElementById("scan-progress-label");
      const progPercent = document.getElementById("scan-progress-percent");
      const progBar = document.getElementById("scan-progress-bar");
      const currFile = document.getElementById("scan-curr-file");
      const elapsedEl = document.getElementById("scan-elapsed");
      const etaEl = document.getElementById("scan-eta");
      const metricSuccess = document.getElementById("metric-success");
      const metricFailed = document.getElementById("metric-failed");
      const metricCand = document.getElementById("metric-candidates");
      const metricSpeed = document.getElementById("metric-speed");

      const btnStart = document.getElementById("btn-scan-start");
      const btnPause = document.getElementById("btn-scan-pause");
      const btnResume = document.getElementById("btn-scan-resume");
      const btnStop = document.getElementById("btn-scan-stop");
      const speedSelect = document.getElementById("sel-runtime-speed");
      const summaryCard = document.getElementById("scan-summary-card");
      const summaryText = document.getElementById("scan-summary-text");
      const tbodyFailed = document.getElementById("tbody-failed-files");
      const failedCountTitle = document.getElementById("failed-count-title");

      async function updateScannerUI() {
        try {
          const prog = await fetch("/v1/local/scanner/progress").then((r) => r.json());
          const total = prog.total_files || 0;
          const p1 = prog.phase1_processed || 0;
          const p2 = prog.phase2_processed || 0;
          const phase = prog.current_phase || prog.status;
          let processed = 0;
          let phaseLabel = "处理进度";
          if (phase === "phase2_analyzing") { processed = p2; phaseLabel = "第二阶段·AI 分析"; }
          else if (phase === "phase1_ingesting") { processed = p1; phaseLabel = "第一阶段·本地解析"; }
          else if (prog.status === "completed") { processed = total; phaseLabel = "全部完成"; }
          else { processed = p1; }
          const pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;

          progLabel.innerHTML = `${phaseLabel}: ${processed} / ${total}<span style="color:#756E65;font-weight:400;font-size:11.5px;">&nbsp;&nbsp;(阶段一 ${p1}/${total} · 阶段二 ${p2}/${total})</span>`;
          progPercent.innerText = `${pct}%`;
          progBar.style.width = `${pct}%`;
          currFile.innerText = prog.current_file ? `当前: ${prog.current_file.split('\\').pop()}` : "空闲待命";
          currFile.title = prog.current_file || "";
          elapsedEl.innerText = formatSeconds(prog.elapsed_seconds);
          etaEl.innerText = formatSeconds(prog.estimated_remaining_seconds);

          metricSuccess.innerText = prog.success_files || 0;
          metricFailed.innerText = prog.failed_files || 0;
          metricCand.innerText = prog.new_candidates_count || 0;
          const speedMap = { quiet: "安静", standard: "标准", full: "全速" };
          metricSpeed.innerText = speedMap[prog.speed_mode] || "标准";
          speedSelect.value = prog.speed_mode || "standard";

          // Status & Buttons
          const scanRunning = prog.status === "scanning" || prog.status === "phase1_ingesting" || prog.status === "phase2_analyzing";
          if (scanRunning) {
            badgeEl.innerHTML = `<span class="paw-badge paw-badge-info"> ${phase === "phase2_analyzing" ? "第二阶段：AI 智能分析中..." : "第一阶段：本地解析中..."}</span>`;
            btnStart.style.display = "none";
            btnPause.style.display = "inline-block";
            btnResume.style.display = "none";
            btnStop.style.display = "inline-block";
            summaryCard.style.display = "none";
          } else if (prog.status === "paused") {
            badgeEl.innerHTML = `<span class="paw-badge paw-badge-warn"> 扫描已暂停 (CPU已释放)</span>`;
            btnStart.style.display = "none";
            btnPause.style.display = "none";
            btnResume.style.display = "inline-block";
            btnStop.style.display = "inline-block";
          } else if (prog.status === "completed") {
            badgeEl.innerHTML = `<span class="paw-badge paw-badge-pass">扫描全部完成</span>`;
            btnStart.style.display = "inline-block";
            btnStart.innerText = " 重新全量扫描";
            btnPause.style.display = "none";
            btnResume.style.display = "none";
            btnStop.style.display = "none";
            summaryCard.style.display = "block";
            summaryText.innerText = `本次共解析 ${processed} 个文件，生成 ${prog.new_candidates_count} 条高价值候选（已按项目与置信度归类），点击下方按钮即可批量核准！`;
          } else {
            badgeEl.innerHTML = `<span class="paw-badge paw-badge-gray">待命就绪</span>`;
            btnStart.style.display = "inline-block";
            const resumableCount = prog.resumable_files || 0;
            btnStart.innerText = resumableCount > 0 ? " 从断点继续扫描" : " 开始全量扫描";
            const banner = document.getElementById("scan-resumable-banner");
            if (banner) {
              banner.style.display = resumableCount > 0 ? "block" : "none";
              const cnt = document.getElementById("scan-resumable-count");
              if (cnt) cnt.innerText = String(resumableCount);
            }
            btnPause.style.display = "none";
            btnResume.style.display = "none";
            btnStop.style.display = "none";
          }

          // Failed items table
          const failedItems = prog.failed_items || [];
          failedCountTitle.innerText = failedItems.length;
          if (failedItems.length > 0) {
            tbodyFailed.innerHTML = failedItems.map((f) => `
              <tr>
                <td style="font-family: monospace; font-size: 11.5px; word-break: break-all;" title="${f.file_path}">${f.file_path.split('\\').pop()}</td>
                <td style="color: #A65F58; font-size: 12px;">${f.reason}</td>
                <td style="color: #756E65; font-size: 12px;">${f.suggestion}</td>
                <td style="text-align: center;">
                  <button class="paw-btn-secondary btn-retry-one" data-path="${f.file_path}" style="padding: 3px 8px; font-size: 11px;">重试</button>
                </td>
              </tr>
            `).join("");

            tbodyFailed.querySelectorAll(".btn-retry-one").forEach((b) => {
              b.addEventListener("click", async (ev) => {
                const p = ev.target.dataset.path;
                ev.target.innerText = "重试中...";
                await fetch("/v1/local/scanner/retry", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ file_path: p }),
                });
                updateScannerUI();
              });
            });
          } else {
            tbodyFailed.innerHTML = `<tr><td colspan="4" style="text-align: center; color: #A1998E; padding: 16px;">暂无异常跳过文件</td></tr>`;
          }
        } catch (e) {
          console.error("Scanner progress fetch error", e);
        }
      }

      btnStart.addEventListener("click", async () => {
        btnStart.disabled = true;
        const resumable = parseInt(document.getElementById("scan-resumable-count")?.innerText || "0", 10);
        let force = false;
        if (resumable > 0) {
          force = !confirm("检测到上次扫描的断点记录（已完成 " + resumable + " 个文件的解析）。\n\n【确定】从断点继续扫描（推荐，已完成的文件自动跳过）\n【取消】放弃断点，从头开始全新扫描");
        }
        await fetch("/v1/local/scanner/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ force_restart: force }),
        });
        btnStart.disabled = false;
        updateScannerUI();
      });

      btnPause.addEventListener("click", async () => {
        await fetch("/v1/local/scanner/pause", { method: "POST" });
        updateScannerUI();
      });

      btnResume.addEventListener("click", async () => {
        await fetch("/v1/local/scanner/resume", { method: "POST" });
        updateScannerUI();
      });

      btnStop.addEventListener("click", async () => {
        if (confirm("确定要终止当前扫描任务吗？未完成的文件可在下次继续断点扫描。")) {
          await fetch("/v1/local/scanner/stop", { method: "POST" });
          updateScannerUI();
        }
      });

      speedSelect.addEventListener("change", async () => {
        await fetch("/v1/local/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ speed_mode: speedSelect.value }),
        });
        updateScannerUI();
      });

      document.getElementById("btn-goto-batch-review").addEventListener("click", () => {
        document.querySelectorAll(".paw-tab-btn").forEach((b) => {
          b.classList.toggle("active", b.dataset.tab === "batch_review");
        });
        currentTab = "batch_review";
        loadTab("batch_review");
      });

      updateScannerUI();
      scannerTimer = setInterval(updateScannerUI, 1500);

    } else if (tab === "batch_review") {
      tabContent.innerHTML = `<p style="color: #756E65;">正在加载待审核候选条目 (Grouped by Project & Confidence)...</p>`;
      const res = await fetch("/v1/web/brain/review/grouped").then((r) => r.json()).catch(() => ({}));
      const groups = res.groups || [];
      const totalPending = res.total_pending || 0;

      tabContent.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
          <div>
            <h4 style="margin: 0; font-size: 15px;">候选知识审核</h4>
            <div style="font-size: 12px; color: #756E65; margin-top: 2px;">
              所有提取的候选知识按项目归纳，严守人机治理原则，由您审阅后沉淀为持久资产
            </div>
          </div>
          <div>
            <span class="paw-badge paw-badge-info" style="font-size: 12px; padding: 4px 10px;">待审核总量: ${totalPending} 条</span>
            <span class="paw-badge paw-badge-gray" style="font-size: 12px; padding: 4px 10px; margin-left: 6px;">本次已加载: ${res.loaded_count || groups.reduce((s, g) => s + g.items.length, 0)} 条${totalPending > (res.loaded_count || 0) ? "（按时间倒序，核准后自动补充后续条目）" : ""}</span>
          </div>
        </div>

        <div style="background: #FCFAF6; border: 1px solid #E8E2D8; border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center;">
          <div style="display: flex; align-items: center; gap: 12px; white-space: nowrap !important; flex-shrink: 0 !important;">
            <label style="font-size: 13px; font-weight: 500; display: flex; align-items: center; gap: 8px; cursor: pointer; white-space: nowrap !important; flex-shrink: 0 !important;">
              <input type="checkbox" id="chk-select-all" style="width: 16px !important; height: 16px !important; min-width: 16px !important; max-width: 16px !important; flex-shrink: 0 !important; margin: 0 !important;" />
              <span style="white-space: nowrap !important; flex-shrink: 0 !important;">全选所有条目</span>
            </label>
            <span style="font-size: 12px; color: #756E65; white-space: nowrap !important; flex-shrink: 0 !important;">已勾选: <b id="selected-count" style="color: #B66F51;">0</b> 项</span>
          </div>
          <div style="display: flex; gap: 8px;">
            <button class="paw-btn-secondary" id="btn-batch-reject" style="font-size: 12px;">批量驳回</button>
            <button class="paw-btn-primary" id="btn-batch-accept" style="font-size: 12px; background: #B66F51;">批量核准入库</button>
          </div>
        </div>

        <div style="display: flex; flex-direction: column; gap: 14px; max-height: 520px; overflow-y: auto;" id="grouped-container">
          ${groups.length === 0 ? `
            <div style="text-align: center; padding: 40px; color: #A1998E;">
              
              <div style="font-size: 14px; font-weight: 500;">所有候选条目已全部审核完毕！</div>
              <div style="font-size: 12px; margin-top: 4px;">正式状态库已完全更新，bypass = 0</div>
            </div>
          ` : groups.map((g, gIdx) => `
            <div style="border: 1px solid #E8E2D8; border-radius: 8px; background: #fff; overflow: hidden;">
              <div style="padding: 10px 14px; background: #FCFAF6; border-bottom: 1px solid #E8E2D8; display: flex; justify-content: space-between; align-items: center;">
                <div class="paw-batch-group-header" style="display: flex; align-items: center; gap: 10px; white-space: nowrap !important; flex-shrink: 0;">
                  <input type="checkbox" class="chk-group" data-group="${gIdx}" style="width: 16px !important; height: 16px !important; min-width: 16px !important; flex-shrink: 0 !important; margin: 0 !important;" />
                  <span style="font-weight: 600; font-size: 13.5px; color: #302B26; white-space: nowrap !important; flex-shrink: 0 !important;">${g.group_name}</span>
                  <span class="paw-badge paw-badge-gray" style="white-space: nowrap !important; flex-shrink: 0 !important;">${g.count} 条候选</span>
                </div>
                <button class="paw-btn-secondary btn-select-high-conf" data-group="${gIdx}" style="padding: 3px 8px; font-size: 11.5px; white-space: nowrap !important; flex-shrink: 0 !important;">勾选高置信度</button>
              </div>
              <div style="padding: 0 12px 10px;">
                <table class="paw-table" style="width: 100%; border-collapse: collapse; font-size: 12.5px;">
                  <thead>
                    <tr style="background: #FCFAF6; color: #756E65; text-align: left;">
                      <th style="padding: 6px 8px; width: 32px;"></th>
                      <th style="padding: 6px 8px; width: 70px;">类型</th>
                      <th style="padding: 6px 8px;">标题</th>
                      <th style="padding: 6px 8px; width: 90px;">置信度</th>
                      <th style="padding: 6px 8px; width: 110px;">来源</th>
                    </tr>
                  </thead>
                  <tbody>
                  ${g.items.map((item) => {
                    const cVal = item.confidence;
                    let isHigh = true;
                    let confDisplay = "高";
                    if (typeof cVal === "number") {
                      isHigh = cVal >= 0.85;
                      confDisplay = (cVal * 100).toFixed(0) + "%";
                    } else if (typeof cVal === "string") {
                      if (cVal === "HIGH") { isHigh = true; confDisplay = "高"; }
                      else if (cVal === "MEDIUM") { isHigh = false; confDisplay = "中"; }
                      else if (cVal === "LOW") { isHigh = false; confDisplay = "低"; }
                      else if (!isNaN(parseFloat(cVal))) {
                        const num = parseFloat(cVal);
                        isHigh = num >= 0.85;
                        confDisplay = (num * 100).toFixed(0) + "%";
                      } else {
                        confDisplay = cVal || "高";
                      }
                    }
                    return `
                    <tr style="border-top: 1px solid #FAF7F1;">
                      <td style="padding: 6px 8px;"><input type="checkbox" class="chk-item chk-group-${gIdx}" style="width: 16px !important; height: 16px !important; min-width: 16px !important; flex-shrink: 0 !important;" data-id="${item.id}" data-conf="${isHigh ? '1' : '0'}" /></td>
                      <td style="padding: 6px 8px;"><span class="paw-badge ${item.candidate_type === 'knowledge' ? 'paw-badge-info' : item.candidate_type === 'todo' ? 'paw-badge-pass' : 'paw-badge-gray'}">${item.candidate_type}</span></td>
                      <td style="padding: 6px 8px; color: #302B26; font-weight: 500;" title="${item.summary || ""}">${item.title}<div style="font-size: 11px; color: #A1998E; font-weight: 400; margin-top: 2px;">${(item.summary || "").slice(0, 60)}</div></td>
                      <td style="padding: 6px 8px;"><span class="paw-badge ${isHigh ? 'paw-badge-pass' : 'paw-badge-warn'}">${confDisplay}</span></td>
                      <td style="padding: 6px 8px; font-size: 11px; color: #756E65;">${item.rule_fallback_notice ? "规则提炼" : "AI 分析"}</td>
                    </tr>
                    `;
                  }).join("")}
                  </tbody>
                </table>
              </div>
            </div>
          `).join("")}
        </div>
      `;

      function updateSelectedCounter() {
        const checked = document.querySelectorAll(".chk-item:checked");
        document.getElementById("selected-count").innerText = checked.length;
      }

      document.querySelectorAll(".chk-item").forEach((c) => {
        c.addEventListener("change", updateSelectedCounter);
      });

      document.getElementById("chk-select-all")?.addEventListener("change", (e) => {
        document.querySelectorAll(".chk-item").forEach((c) => { c.checked = e.target.checked; });
        document.querySelectorAll(".chk-group").forEach((c) => { c.checked = e.target.checked; });
        updateSelectedCounter();
      });

      document.querySelectorAll(".chk-group").forEach((grpChk) => {
        grpChk.addEventListener("change", (e) => {
          const gIdx = e.target.dataset.group;
          document.querySelectorAll(`.chk-group-${gIdx}`).forEach((c) => { c.checked = e.target.checked; });
          updateSelectedCounter();
        });
      });

      document.querySelectorAll(".btn-select-high-conf").forEach((b) => {
        b.addEventListener("click", (e) => {
          const gIdx = e.target.dataset.group;
          document.querySelectorAll(`.chk-group-${gIdx}`).forEach((c) => {
            if (parseFloat(c.dataset.conf) >= 0.85 || c.dataset.conf === "1" || c.dataset.conf === "HIGH") c.checked = true;
          });
          updateSelectedCounter();
        });
      });

      document.getElementById("btn-batch-accept")?.addEventListener("click", async () => {
        const checked = Array.from(document.querySelectorAll(".chk-item:checked")).map((c) => c.dataset.id);
        if (checked.length === 0) {
          alert("请先勾选需要通过的候选条目！");
          return;
        }
        if (confirm(`确认批量通过选中的 ${checked.length} 条候选？\n系统将自动生成审计记录并更新正式状态底座（保持 bypass=0）。`)) {
          const resp = await fetch("/v1/web/brain/review/batch_accept", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ candidate_ids: checked, reviewer: "LocalUser" }),
          }).then((r) => r.json());
          const after = await fetch("/v1/web/brain/review/grouped").then((r) => r.json()).catch(() => ({}));
          if (resp.failed_count > 0) {
            alert(`批量审核完成：
成功入库: ${resp.accepted_count} 条
入库失败: ${resp.failed_count} 条（首条错误原因: ${resp.failed_details && resp.failed_details[0] ? resp.failed_details[0].error : "校验未通过"}）
当前剩余待审: ${after.total_pending != null ? after.total_pending : "-"} 条`);
          } else {
            alert(`批量通过成功！
本次入库: ${resp.accepted_count} 条（生成 ${resp.commit_records_created} 条提交审计）
当前剩余待审: ${after.total_pending != null ? after.total_pending : "-"} 条${(after.total_pending || 0) > 0 ? "（含扫描新产生的候选）" : ""}`);
          }
          loadTab("batch_review");
          triggerPawRefresh();
        }
      });

      document.getElementById("btn-batch-reject")?.addEventListener("click", async () => {
        const checked = Array.from(document.querySelectorAll(".chk-item:checked")).map((c) => c.dataset.id);
        if (checked.length === 0) {
          alert("请先勾选需要驳回的候选条目！");
          return;
        }
        if (confirm(`确认批量驳回选中的 ${checked.length} 条候选？`)) {
          const resp = await fetch("/v1/web/brain/review/batch_reject", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ candidate_ids: checked, reviewer: "LocalUser", notes: "用户批量驳回" }),
          }).then((r) => r.json());
          alert(`已批量驳回 ${resp.rejected_count} 条候选。`);
          loadTab("batch_review");
          triggerPawRefresh();
        }
      });

    } else if (tab === "settings") {
      const set = await fetch("/v1/local/settings").then((r) => r.json()).catch(() => ({}));
      tabContent.innerHTML = `
        <h4 style="margin: 0 0 14px 0; font-size: 15px;"> 本地环境与模型全面配置</h4>

        <div class="paw-field">
          <label>监控工作目录 (支持添加多个文件夹)</label>
          <div id="set-work-dirs-box" style="border: 1px solid #E8E2D8; border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; max-height: 150px; overflow-y: auto;"></div>
          <button class="paw-btn-secondary" id="set-pick-dir" style="white-space: nowrap;"> 添加监控文件夹</button>
          <input type="hidden" id="set-work-dir" value="${set.work_dir || ""}" />
          <div style="margin-top: 6px; font-size: 11.5px; color: #756E65;">可多次点击「添加监控文件夹」追加目录；每行右侧 &times; 可移除该目录。</div>
        </div>

        <div class="paw-field">
          <label>数据持久化目录 (数据库与配置存储位置)</label>
          <div style="display: flex; gap: 8px; align-items: center;">
            <input type="text" id="set-data-dir" value="${set.data_dir || ""}" style="flex: 1;" readonly />
            <button class="paw-btn-secondary" id="set-pick-data-dir" style="white-space: nowrap;"> 选择数据目录</button>
          </div>
          <div style="margin-top: 6px; font-size: 11.5px; color: #756E65;">默认使用系统用户数据目录；修改后现有数据会自动复制到新目录，重启程序后生效。</div>
        </div>

        <div class="paw-field">
          <label>推理大脑 Provider 预设模板</label>
          <select id="set-provider-preset">
            <option value="deepseek" ${set.provider === "deepseek" ? "selected" : ""}>DeepSeek (推荐: 高性价比推理)</option>
            <option value="qwen" ${set.provider === "qwen" ? "selected" : ""}>通义千问 Qwen (阿里云兼容服务)</option>
            <option value="openai" ${set.provider === "openai" ? "selected" : ""}>OpenAI (标准端点)</option>
            <option value="custom" ${set.provider === "custom" ? "selected" : ""}>自定义 OpenAI 兼容接口 / 网关</option>
            <option value="offline" ${set.provider === "offline" ? "selected" : ""}>纯离线规则推理 (无需 API Key)</option>
          </select>
        </div>

        <div id="set-provider-fields">
          <div class="paw-field">
            <label>API Key (DPAPI 加密存储)</label>
            <input type="password" id="set-api-key" placeholder="${set.has_api_key ? `已加密存储: ${set.key_mask} (如需更改请输入新 Key)` : "sk-..."}" />
          </div>
          <div class="paw-field">
            <label>Base URL</label>
            <input type="text" id="set-base-url" value="${set.deepseek_base_url || "https://api.deepseek.com"}" />
          </div>
          <div class="paw-field">
            <label>Model Name</label>
            <input type="text" id="set-model" value="${set.deepseek_model || "deepseek-chat"}" />
          </div>
          <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 14px;">
            <button class="paw-btn-secondary" id="set-btn-test-conn"> 测试当前模型连通性</button>
            <div id="set-conn-result" style="font-size: 12px; font-weight: 500;"></div>
          </div>
        </div>

        <div class="paw-field" id="set-websearch-group" style="padding: 12px 14px; background: #FCFAF6; border: 1px solid #E8E2D8; border-radius: 8px; margin-bottom: 14px;">
          <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; margin-bottom: 4px;">
            <input type="checkbox" id="set-websearch-enabled" ${set.web_search_enabled !== false ? "checked" : ""} style="width: auto;" />
            <span style="font-weight: 600; color: #302B26;"> 开启模型原生联网检索能力（默认开启）</span>
          </label>
          <div id="set-websearch-desc" style="font-size: 11.5px; color: #756E65; line-height: 1.5; margin-left: 22px;">
            ${(set.provider === "qwen" || set.provider === "deepseek" || set.provider === "custom") ? 
              "当前模型支持联网能力。开启后，助手在需要时会调用服务商原生联网搜索，并在界面明确标明「来自网络搜索，非工作台依据」。网络内容不计入正式知识依据。" : 
              " 如实说明：当前选定的模型端点不支持原生联网搜索，助手将严格基于本地工作台文档与事实回答，绝不伪造虚假联网效果。"}
          </div>
        </div>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
          <div class="paw-field">
            <label>巡航频率自选 (R1 第7条)</label>
            <select id="set-cruise-mode">
              <option value="realtime" ${set.cruise_mode === "realtime" ? "selected" : ""}>实时（文件变化即触发，默认推荐）</option>
              <option value="every_1h" ${set.cruise_mode === "every_1h" || set.cruise_mode === "hourly" ? "selected" : ""}>每小时巡航一次</option>
              <option value="every_2h" ${set.cruise_mode === "every_2h" ? "selected" : ""}>每 2 小时巡航一次</option>
              <option value="every_6h" ${set.cruise_mode === "every_6h" ? "selected" : ""}>每 6 小时巡航一次</option>
              <option value="daily" ${set.cruise_mode === "daily" ? "selected" : ""}>每天巡航一次</option>
              <option value="manual" ${set.cruise_mode === "manual" ? "selected" : ""}>手动（仅手动点击时分析）</option>
            </select>
          </div>
          <div class="paw-field">
            <label>资源限流模式</label>
            <select id="set-speed-mode">
              <option value="quiet" ${set.speed_mode === "quiet" ? "selected" : ""}>安静模式 (低优先级, 不抢占 CPU)</option>
              <option value="standard" ${set.speed_mode === "standard" || !set.speed_mode ? "selected" : ""}>标准模式 (默认推荐)</option>
              <option value="full" ${set.speed_mode === "full" ? "selected" : ""}>全速模式 (满载解析)</option>
            </select>
          </div>
        </div>

        <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px;">
          <div class="paw-field">
            <label>单文件解析上限 (MB)</label>
            <input type="number" id="set-max-size" value="${set.max_file_size_mb || 100}" />
          </div>
          <div class="paw-field">
            <label>每日模型调用上限 (次/天, 0 表示无上限)</label>
            <input type="number" id="set-daily-limit" min="0" value="${set.daily_call_limit !== undefined ? set.daily_call_limit : 0}" />
          </div>
          <div class="paw-field">
            <label>数据持久化目录 (Local AppData)</label>
            <input type="text" value="${set.data_dir || ""}" disabled style="background:#FAF7F1; color:#756E65;" />
          </div>
        </div>

        <!-- R7: Automated Backup & Recovery Panel -->
        <div style="margin-top: 18px; padding: 14px 16px; background: #FCFAF6; border: 1px solid #E8E2D8; border-radius: 8px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <h5 style="margin: 0; font-size: 13.5px; color: #302B26;"> 数据库自动备份与灾难恢复 (R7)</h5>
            <button class="paw-btn-secondary" id="btn-create-backup-now" style="font-size: 12px; padding: 4px 10px;">+ 立即创建安全快照</button>
          </div>
          <div style="font-size: 12px; color: #756E65; margin-bottom: 10px;">
            系统每日自动备份 workspace.db（保留最近 7 份），并在每次批量核准前自动创建快照。备份文件不包含任何敏感 API 密钥明文。
          </div>
          <div id="set-backup-list" style="max-height: 150px; overflow-y: auto; font-size: 12px;">正在加载备份清单...</div>
        </div>

        <div style="margin-top: 20px; display: flex; justify-content: flex-end;">
          <button class="paw-btn-primary" id="set-save-btn" style="padding: 9px 20px;"> 保存所有设置</button>
        </div>
      `;

      // Load backups
      async function updateBackupList() {
        const bEl = document.getElementById("set-backup-list");
        if (!bEl) return;
        try {
          const bData = await fetch("/v1/local/backup/list").then((r) => r.json());
          const list = bData.backups || [];
          if (list.length === 0) {
            bEl.innerHTML = `<span style="color: #A1998E;">暂无可用的历史备份</span>`;
          } else {
            bEl.innerHTML = `
              <table style="width: 100%; border-collapse: collapse; font-size: 11.5px;">
                <thead>
                  <tr style="border-bottom: 1px solid #DED8CC; text-align: left; color: #756E65;">
                    <th style="padding: 4px;">备份文件名</th>
                    <th style="padding: 4px;">时间</th>
                    <th style="padding: 4px;">大小</th>
                    <th style="padding: 4px; text-align: center;">操作</th>
                  </tr>
                </thead>
                <tbody>
                  ${list.map((b) => `
                    <tr style="border-bottom: 1px solid #FAF7F1;">
                      <td style="padding: 4px; font-family: monospace;">${b.filename}</td>
                      <td style="padding: 4px; color: #756E65;">${b.formatted_time}</td>
                      <td style="padding: 4px; color: #756E65;">${b.size_mb} MB</td>
                      <td style="padding: 4px; text-align: center;">
                        <button class="paw-btn-secondary btn-restore-b" data-fn="${b.filename}" style="padding: 2px 6px; font-size: 11px;">恢复</button>
                      </td>
                    </tr>
                  `).join("")}
                </tbody>
              </table>
            `;

            bEl.querySelectorAll(".btn-restore-b").forEach((btn) => {
              btn.addEventListener("click", async () => {
                const fn = btn.dataset.fn;
                if (confirm(`【警告】确定要从备份文件恢复数据库吗？\n当前数据库将被该备份完整还原。\n恢复文件: ${fn}`)) {
                  btn.innerText = "恢复中...";
                  const res = await fetch("/v1/local/backup/restore", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ backup_filename: fn }),
                  }).then((r) => r.json());
                  alert(res.message || "恢复成功！");
                  updateBackupList();
                }
              });
            });
          }
        } catch (e) {
          bEl.innerHTML = `<span style="color: #A65F58;">加载备份失败: ${e.message}</span>`;
        }
      }

      updateBackupList();

      document.getElementById("btn-create-backup-now")?.addEventListener("click", async () => {
        const btn = document.getElementById("btn-create-backup-now");
        btn.disabled = true;
        btn.innerText = "正在备份...";
        await fetch("/v1/local/backup/create", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tag: "manual_ui" }),
        });
        btn.disabled = false;
        btn.innerText = "+ 立即创建安全快照";
        updateBackupList();
      });


      const pSelect = document.getElementById("set-provider-preset");
      const urlInput = document.getElementById("set-base-url");
      const modelInput = document.getElementById("set-model");
      const pFields = document.getElementById("set-provider-fields");

      pSelect.addEventListener("change", () => {
        const val = pSelect.value;
        const wsDesc = document.getElementById("set-websearch-desc");
        const wsBox = document.getElementById("set-websearch-enabled");
        if (wsDesc) {
          if (val === "qwen" || val === "deepseek" || val === "custom") {
            wsDesc.innerText = "当前模型支持联网能力。开启后，助手在需要时会调用服务商原生联网搜索，并在界面明确标明「来自网络搜索，非工作台依据」。网络内容不计入正式知识依据。";
            if (wsBox) wsBox.disabled = false;
          } else {
            wsDesc.innerText = " 如实说明：当前选定的模型端点不支持原生联网搜索，助手将严格基于本地工作台文档与事实回答，绝不伪造虚假联网效果。";
          }
        }
        if (val === "offline") {
          pFields.style.display = "none";
        } else {
          pFields.style.display = "block";
          const p = getProviderPresets(val);
          urlInput.value = p.base_url;
          modelInput.value = p.model;
        }
      });

      document.getElementById("set-btn-test-conn").addEventListener("click", async () => {
        const resEl = document.getElementById("set-conn-result");
        resEl.innerHTML = `<span style="color:#B66F51;">正在发送连通性探测...</span>`;
        const payload = {
          base_url: urlInput.value.trim(),
          api_key: document.getElementById("set-api-key").value.trim(),
          model: modelInput.value.trim(),
        };
        try {
          const resp = await fetch("/v1/local/provider/test_connection", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          }).then((r) => r.json());
          if (resp.success) {
            resEl.innerHTML = `<span style="color:#648166;">测试通过！延时: ${resp.latency_ms} ms (${resp.model_used})</span>`;
          } else {
            resEl.innerHTML = `<span style="color:#A65F58;">未通过  失败 [${resp.error_code}]: ${resp.error_message}</span>`;
          }
        } catch (e) {
          resEl.innerHTML = `<span style="color:#A65F58;">请求异常: ${e.message}</span>`;
        }
      });

      const renderWorkDirs = () => {
        const inp = document.getElementById("set-work-dir");
        const box = document.getElementById("set-work-dirs-box");
        if (!inp || !box) return;
        const parts = inp.value.trim() ? inp.value.trim().split(";").map((s) => s.trim()).filter(Boolean) : [];
        box.innerHTML = parts.length === 0
          ? '<div style="color:#A1998E;font-size:12px;">尚未添加监控文件夹，点击下方按钮添加</div>'
          : parts.map((p, i) => '<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12.5px;"><span style="flex:1;word-break:break-all;"> ' + p + '</span><button type="button" data-wd-idx="' + i + '" style="border:none;background:none;color:#A65F58;cursor:pointer;font-size:13px;">&times;</button></div>').join("");
        box.querySelectorAll("[data-wd-idx]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const idx = parseInt(btn.getAttribute("data-wd-idx"), 10);
            const cur = inp.value.trim().split(";").map((s) => s.trim()).filter(Boolean);
            cur.splice(idx, 1);
            inp.value = cur.join(";");
            renderWorkDirs();
          });
        });
      };
      renderWorkDirs();
      document.getElementById("set-pick-dir")?.addEventListener("click", async () => {
        try {
          const res = await fetch("/v1/local/pick_folder").then((r) => r.json());
          if (res.ok && res.path) {
            const inp = document.getElementById("set-work-dir");
            const parts = inp.value.trim() ? inp.value.trim().split(";").map((s) => s.trim()).filter(Boolean) : [];
            if (!parts.includes(res.path)) parts.push(res.path);
            inp.value = parts.join(";");
            renderWorkDirs();
          }
        } catch (e) {}
      });
      document.getElementById("set-pick-data-dir")?.addEventListener("click", async () => {
        try {
          const res = await fetch("/v1/local/pick_folder").then((r) => r.json());
          if (res.ok && res.path) {
            if (!confirm("将数据持久化目录切换为:\n" + res.path + "\n\n现有数据会复制到新目录，重启程序后生效。是否继续？")) return;
            const resp = await fetch("/v1/local/data_dir", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ data_dir: res.path }),
            }).then((r) => r.json());
            if (resp.detail) { alert("失败: " + resp.detail); return; }
            alert(resp.message || "已设置");
            if (resp.ok) document.getElementById("set-data-dir").value = res.path;
          }
        } catch (e) {}
      });

      document.getElementById("set-save-btn").addEventListener("click", async () => {
        const pVal = pSelect.value;
        const key = document.getElementById("set-api-key").value.trim();
        const payload = {
          work_dir: document.getElementById("set-work-dir").value.trim(),
          provider: pVal,
          cruise_mode: document.getElementById("set-cruise-mode").value,
          speed_mode: document.getElementById("set-speed-mode").value,
          max_file_size_mb: parseInt(document.getElementById("set-max-size").value) || 100,
          daily_call_limit: Math.max(0, parseInt(document.getElementById("set-daily-limit")?.value || "0", 10)),
          web_search_enabled: document.getElementById("set-websearch-enabled") ? document.getElementById("set-websearch-enabled").checked : true,
        };

        if (pVal === "deepseek") {
          if (key) payload.deepseek_api_key = key;
          payload.deepseek_base_url = urlInput.value.trim();
          payload.deepseek_model = modelInput.value.trim();
        } else if (pVal === "qwen") {
          if (key) payload.qwen_api_key = key;
          payload.qwen_base_url = urlInput.value.trim();
          payload.qwen_model = modelInput.value.trim();
        } else if (pVal === "openai") {
          if (key) payload.openai_api_key = key;
          payload.openai_base_url = urlInput.value.trim();
          payload.openai_model = modelInput.value.trim();
        } else if (pVal === "custom") {
          if (key) payload.custom_api_key = key;
          payload.custom_base_url = urlInput.value.trim();
          payload.custom_model = modelInput.value.trim();
        }

        let saveOk = false;
        let saveErr = "";
        try {
          const sess = await fetch("/v1/session").then((r) => r.json()).catch(() => ({}));
          const resp = await fetch("/v1/local/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json", "x-paw-local-token": sess.local_token || "" },
            body: JSON.stringify(payload),
          });
          const result = await resp.json().catch(() => ({}));
          if (resp.ok && result.ok !== false) {
            saveOk = true;
          } else {
            saveErr = result.detail || result.message || ("HTTP " + resp.status);
          }
        } catch (e) {
          saveErr = e.message;
        }
        if (!saveOk) {
          alert("设置保存失败: " + saveErr + "，请重试。");
          return;
        }
        // Verify the saved state actually took effect before claiming success
        try {
          const check = await fetch("/v1/local/settings").then((r) => r.json()).catch(() => ({}));
          const mismatches = [];
          if (payload.speed_mode && check.speed_mode && payload.speed_mode !== check.speed_mode) {
            mismatches.push("性能模式(当前生效: " + check.speed_mode + ")");
          }
          if (payload.provider && check.provider && payload.provider !== check.provider) {
            mismatches.push("Provider(当前生效: " + check.provider + ")");
          }
          if (mismatches.length > 0) {
            alert("设置已提交，但以下项目尚未生效: " + mismatches.join("、") + "。请重试保存或重启程序。");
            return;
          }
        } catch (e) {}
        alert("设置已成功保存并验证生效！配置已加密并即时应用于全局（含文件夹扫描的性能调节）。");
      });

    } else if (tab === "status") {
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
            <button class="paw-btn-secondary" id="btn-converge-projects" style="font-size: 12.5px; padding: 6px 14px;">
              检查项目收敛
            </button>
            <button class="paw-btn-secondary" id="btn-trigger-deep-cruise" style="font-size: 12.5px; padding: 6px 14px;">
              深度巡航 (AI提炼项目)
            </button>
            <button class="paw-btn-primary" id="btn-trigger-cruise" style="font-size: 12.5px; padding: 6px 14px;">
              立即执行一次巡航
            </button>
          </div>
        </div>

        ${sched.status === "running" ? `
        <div id="cruise-running-notice" style="background: #FFF7ED; border: 1px solid #FED7AA; border-radius: 8px; padding: 14px 18px; margin-bottom: 16px; color: #C2410C; font-size: 13px;">
          <div style="font-weight: 600; margin-bottom: 4px;">⏳ 后台正在巡航分析本地工作文档并调用内置 AI 深度提炼项目进展...</div>
          <div style="color: #9A3412; font-size: 12px;">系统正在处理素材、提取证据链并生成项目 30 秒说明，结果将在完成后自动刷新呈现。</div>
        </div>
        ` : report ? `
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
            <div id="cruise-live-status" style="font-weight: 600; font-size: 15px; margin-top: 4px; color: ${sched.status === "running" ? "#C2410C" : "#648166"}">
              ${sched.status === "running" ? "⏳ 正在巡航分析中..." : "空闲待命 (Idle)"}
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

      function startCruisePolling() {
        if (_cruisePollTimer) {
          clearInterval(_cruisePollTimer);
          _cruisePollTimer = null;
        }
        const btn1 = document.getElementById("btn-trigger-cruise");
        const btn2 = document.getElementById("btn-trigger-deep-cruise");
        if (btn1) { btn1.disabled = true; btn1.innerText = "巡航中..."; }
        if (btn2) { btn2.disabled = true; btn2.innerText = "AI分析中..."; }
        _cruisePollTimer = setInterval(async () => {
          try {
            const check = await fetch("/v1/local/runtime/status").then(r => r.json());
            if (check?.scheduler?.status === "idle") {
              clearInterval(_cruisePollTimer);
              _cruisePollTimer = null;
              triggerPawRefresh();
              loadTab("status");
            }
          } catch (e) {
            clearInterval(_cruisePollTimer);
            _cruisePollTimer = null;
          }
        }, 2000);
      }

      if (sched.status === "running") {
        startCruisePolling();
      }

      async function triggerCruiseAction(force) {
        const btn1 = document.getElementById("btn-trigger-cruise");
        const btn2 = document.getElementById("btn-trigger-deep-cruise");
        if (btn1) { btn1.disabled = true; btn1.innerText = "巡航启动中..."; }
        if (btn2) { btn2.disabled = true; btn2.innerText = "AI分析启动中..."; }
        const statusElem = document.getElementById("cruise-live-status");
        if (statusElem) {
          statusElem.innerText = force ? "正在全量深度巡航并由 AI 提炼项目..." : "正在巡航分析工作文档与提炼项目...";
          statusElem.style.color = "#C2410C";
        }

        try {
          const resp = await fetch("/v1/local/runtime/cruise/trigger", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ force_rescan: force, analyze_projects: true, async_mode: true })
          }).then(r => r.json());

          const resStatus = resp?.result?.status;
          if (resStatus === "started" || resStatus === "already_running") {
            startCruisePolling();
          } else {
            loadTab("status");
          }
        } catch (err) {
          alert("巡航触发异常: " + err);
          loadTab("status");
        }
      }

      document.getElementById("btn-trigger-cruise").addEventListener("click", () => triggerCruiseAction(false));
      document.getElementById("btn-trigger-deep-cruise").addEventListener("click", () => triggerCruiseAction(true));

      document.getElementById("btn-converge-projects").addEventListener("click", async () => {
        const btn = document.getElementById("btn-converge-projects");
        const statusElem = document.getElementById("cruise-live-status");
        btn.disabled = true;
        btn.innerText = "正在检查项目收敛...";
        if (statusElem) {
          statusElem.innerText = "AI 正在检查哪些项目属于同一个长期业务...";
          statusElem.style.color = "#C2410C";
        }
        try {
          const res = await fetch("/v1/local/projects/converge", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
          }).then(r => r.json());
          alert("项目收敛检查完成：" + (res.summary || "已完成"));
          triggerPawRefresh();
        } catch (err) {
          alert("项目收敛检查失败：" + err);
        } finally {
          loadTab("status");
        }
      });

    } else if (tab === "evolution") {
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

    } else if (tab === "doctor") {
      tabContent.innerHTML = `<p style="color: #756E65;">正在执行全面环境与数据诊断，请稍候...</p>`;
      const doc = await fetch("/v1/local/doctor").then((r) => r.json()).catch(() => ({}));
      const checks = doc.checks || [];

      tabContent.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
          <div>
            <h4 style="margin: 0; font-size: 15px; font-weight: 600; color: #302B26;">系统运行诊断与环境状态</h4>
            <div style="font-size: 12px; color: #6F6A61; margin-top: 2px;">检查本地数据库、解析引擎、调度器与模型端点就绪情况</div>
          </div>
          <span style="color: ${doc.overall === 'HEALTHY' ? '#B66F51' : '#A47A42'}; font-size: 12.5px; font-weight: 500;">
            ${doc.overall === 'HEALTHY' ? '运行状态正常' : '需要检查'}
          </span>
        </div>
        <div style="display: flex; flex-direction: column; gap: 8px;">
          ${checks.map((c) => `
            <div style="border: 1px solid #E8E2D8; border-radius: 6px; padding: 10px 14px; background: #FAF8F2;">
              <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="font-weight: 550; font-size: 13.5px; color: #302B26;">${c.name}</span>
                <span style="font-size: 12px; font-weight: 500; color: ${c.status === 'PASS' ? '#B66F51' : c.status === 'OFFLINE_FALLBACK' ? '#8A7659' : '#A65F58'};">
                  ${c.status === 'PASS' ? '正常' : c.status === 'OFFLINE_FALLBACK' ? '纯本地检索' : c.status}
                </span>
              </div>
              <div style="color: #6F6A61; font-size: 12px; margin-top: 3px;">${c.detail}</div>
            </div>
          `).join("")}
        </div>
        <div style="margin-top: 20px; display: flex; justify-content: flex-end;">
          <button class="paw-btn-primary" id="btn-refresh-doctor" style="font-size: 12.5px;">重新诊断</button>
        </div>
      `;

      document.getElementById("btn-refresh-doctor").addEventListener("click", () => loadTab("doctor"));
    }
  }

  const urlParams = new URLSearchParams(window.location.search);
  const requestedTab = urlParams.get("open_tab");
  if (requestedTab) {
    overlay.style.display = "flex";
    loadTab(requestedTab);
  }

  // Auto show onboarding on first launch if not completed
  fetch("/v1/local/onboarding")
    .then((r) => r.json())
    .then((data) => {
      if (!data.completed) {
        overlay.style.display = "flex";
        loadTab("onboarding");
      }
    })
    .catch(() => {});

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
        if (url.includes("/v1/web/brain/review/candidates") && /([?&])limit=50(?=&|$)/.test(url)) {
          url = url.replace(/([?&])limit=50(?=&|$)/, "$1limit=500");
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

  // On-demand refresh triggers (no polling loops or focus spam)

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
