# DONE-G-05：全量重跑与使用说明初稿交付报告

## A. 全量重跑（Part A）

### 1. 运行基本信息
- **分支**: `gemini/G-03`
- **目标 PR**: [#4 (gemini/G-03 -> claude/p0-project-update)](https://github.com/riser-lang/paw-local/pull/4)
- **本地 HEAD 提交**: `56690d1` (合入 `origin/claude/p0-project-update` 提交 `0260041`)
- **云端运行测试提交**: `436d2bcead8ffdbd7f9eefc8bd5074a9cda5cafd` (GitHub Actions pull_request merge commit)
- **运行链接**: [https://github.com/riser-lang/paw-local/actions/runs/38557745377](https://github.com/riser-lang/paw-local/actions/runs/38557745377)
- **判定结果**: **VERDICT: GREEN**

### 2. 代码与规则完整性核验
- `python tests/paw_lint.py`：输出 `lint clean`（0 violations）。
- `python scripts/scan_secrets_leak.py`：输出 `VERDICT: PASS`（0 leaks）。
- 审核人掌握规则文件（`tests/paw_case.py`, `tests/paw_verdict.py`, `tests/paw_lint.py`, `tests/required_cases.json`）均无任何改动。
- 产品修复由审核人提交 `0260041` 提供，未修改任何其他测试或产品代码。

### 3. 云端判定结果 `summary.md` 全文
```markdown
# Pipeline verdict for 436d2bcead8ffdbd7f9eefc8bd5074a9cda5cafd

Required cases: 28 | PASS 28 | FAIL 0 | NOT RUN 0

| Block | Case | Status | Failed check | Model |
|---|---|---|---|---|
| B1 | B1-01 | PASS |  | mock-llm |
| B1 | B1-02 | PASS |  | mock-llm |
| B1 | B1-03 | PASS |  | mock-llm |
| B2 | B2-01 | PASS |  | mock-llm |
| B2 | B2-02 | PASS |  | mock-llm |
| B2 | B2-03 | PASS |  | mock-llm |
| B3 | B3-01 | PASS |  | mock-llm |
| B3 | B3-02 | PASS |  | mock-llm |
| B3 | B3-03 | PASS |  | mock-llm |
| B3 | B3-04 | PASS |  | mock-llm |
| B4 | B4-01 | PASS |  | mock-llm |
| B4 | B4-02 | PASS |  | mock-llm |
| B4 | B4-03 | PASS |  | mock-llm |
| B4 | B4-04 | PASS |  | mock-llm |
| B4 | B4-05 | PASS |  | mock-llm |
| B4 | B4-06 | PASS |  | mock-llm |
| B4 | B4-07 | PASS |  | mock-llm |
| B5 | B5-01 | PASS |  | mock-llm |
| B5 | B5-02 | PASS |  | mock-llm |
| B5 | B5-03 | PASS |  | mock-llm |
| B6 | B6-01 | PASS |  | mock-llm |
| B6 | B6-02 | PASS |  | mock-llm |
| B7 | B7-01 | PASS |  | mock-llm |
| B7 | B7-02 | PASS |  | mock-llm |
| B7 | B7-03 | PASS |  | mock-llm |
| B8 | B8-01 | PASS |  | mock-llm |
| B8 | B8-02 | PASS |  | mock-llm |
| B8 | B8-03 | PASS |  | mock-llm |
| NEGATIVE-CONTROL | NC-01 | FAIL | project_update_committed | mock-llm |
| NEGATIVE-CONTROL | NC-02 | FAIL | explainer_fields_filled | mock-llm |
| NEGATIVE-CONTROL | NC-03 | FAIL | environment_clean | mock-llm |

## No problems

VERDICT: GREEN
```

---

## B. 使用说明初稿（Part B）

### 1. 文件信息
- **文件路径**: `docs/使用说明.txt`
- **编码格式**: UTF-8 带 BOM（`utf-8-sig`，Windows 记事本打开绝不乱码，已通过 Python 校验 `BOM: True`）
- **篇幅体量**: 95 行，约 7 KB，A4 打印 2 页以内。
- **目标受众**: 完全不懂电脑的非技术人员，语言平实通俗。
- **安全检查**: 包含敏感词扫描，绝无任何真实 API Key、个人账号信息或外部服务器地址。
- **章节完整性**:
  1. 一、软件安装（包含 Windows SmartScreen 蓝色“Windows 已保护你的电脑”提示下点“更多信息”→“仍要运行”，桌面及开始菜单图标说明）
  2. 二、第一次打开与界面介绍（浏览器自动打开工作台 `127.0.0.1:8800/app`，系统托盘右键菜单说明，左侧导航栏与右下角控制中心圆钮说明）
  3. 三、填写您的 API Key（控制中心入口、支持的服务商列表、DPAPI 强加密存储说明）
  4. 四、五分钟新手极速体验路线（选择监控文件夹 → 放入 Word 周报 → 审核页核准建议 → 项目页查看 30 秒快速了解 → 问 AI 助手一个问题）
  5. 五、常见问题解答 FAQ（端口自动避让 8800/8801、杀毒软件白名单、`%LOCALAPPDATA%\PAW` 存储位置、卸载保留用户数据、离线数据安全原则）

---

### 2. 界面词出处对照表（UI String Citations）

所有在《使用说明》中出现的按钮名、菜单名、选项名和功能文字，均严格对照并照抄自 `frontend/dist/` 打包文件及原生托盘代码，绝无凭空虚构：

| # | 说明书中的界面词 | 出处文件 | 原文片段与上下文 |
|---|----------------|---------|-----------------|
| 1 | `PAW Personal Work OS` | `installer/paw_setup.iss:12` | `#define MyAppName "PAW Personal Work OS"` |
| 2 | `PAW_Personal_Work_OS_Setup_v2.3.8.exe` | `installer/paw_setup.iss:13,29` | `#define MyAppVersion "2.3.8"`, `OutputBaseFilename={#MyAppName}_Setup_v{#MyAppVersion}` |
| 3 | `打开工作台 (&O)` | `backend/tray/tray_app.py:95` | `pystray.MenuItem("打开工作台 (&O)", self.on_open_browser, default=True)` |
| 4 | `暂停 / 恢复巡航 (&C)` | `backend/tray/tray_app.py:96` | `pystray.MenuItem("暂停 / 恢复巡航 (&C)", self.on_toggle_cruise)` |
| 5 | `系统健康自检 (&D)` | `backend/tray/tray_app.py:97` | `pystray.MenuItem("系统健康自检 (&D)", self.on_health_check)` |
| 6 | `安全退出 (&X)` | `backend/tray/tray_app.py:100` | `pystray.MenuItem("安全退出 (&X)", self.on_exit)` |
| 7 | `今天` | `frontend/dist/assets/index-qIwUsXqw.js` | `{key:"today",label:"今天",icon:c}` |
| 8 | `项目` | `frontend/dist/assets/index-qIwUsXqw.js` | `{key:"projects",label:"项目",icon:d}` |
| 9 | `知识` | `frontend/dist/assets/index-qIwUsXqw.js` | `{key:"knowledge",label:"知识",icon:m}` |
| 10 | `资料` | `frontend/dist/assets/index-qIwUsXqw.js` | `{key:"resources",label:"资料",icon:h}` |
| 11 | `待办` | `frontend/dist/assets/index-qIwUsXqw.js` | `{key:"todos",label:"待办",icon:u}` |
| 12 | `日历` | `frontend/dist/assets/index-qIwUsXqw.js` | `{key:"calendar",label:"日历",icon:p}` |
| 13 | `待我审核` | `frontend/dist/assets/index-qIwUsXqw.js` | `{key:"reviews",label:"待我审核",icon:x}` |
| 14 | `AI助手` | `frontend/dist/assets/index-qIwUsXqw.js` | `{key:"assistant",label:"AI助手",icon:g}` |
| 15 | `PAW 本地控制中心` | `frontend/dist/paw_console.js` | `title="PAW 本地控制中心"`, `<h3>PAW 本地控制中心</h3>` |
| 16 | `系统与模型` | `frontend/dist/paw_console.js` | `<button class="paw-tab-btn active" data-tab="system">系统与模型</button>` |
| 17 | `文件夹扫描` | `frontend/dist/paw_console.js` | `<button class="paw-tab-btn" data-tab="scanner">文件夹扫描</button>` |
| 18 | `批量审核` | `frontend/dist/paw_console.js` | `<button class="paw-tab-btn" data-tab="reviews">批量审核</button>` |
| 19 | `巡航与快照` | `frontend/dist/paw_console.js` | `<button class="paw-tab-btn" data-tab="cruise">巡航与快照</button>` |
| 20 | `DeepSeek (推荐: 极高推理解析性价比)` | `frontend/dist/paw_console.js` | `<option value="deepseek">DeepSeek (推荐: 极高推理解析性价比)</option>` |
| 21 | `通义千问 Qwen (阿里云兼容端点)` | `frontend/dist/paw_console.js` | `<option value="qwen">通义千问 Qwen (阿里云兼容端点)</option>` |
| 22 | `OpenAI (GPT-4o 官方标准)` | `frontend/dist/paw_console.js` | `<option value="openai">OpenAI (GPT-4o 官方标准)</option>` |
| 23 | `自定义 OpenAI 兼容接口 / 网关` | `frontend/dist/paw_console.js` | `<option value="custom">自定义 OpenAI 兼容接口 / 网关</option>` |
| 24 | `纯离线规则推理 (无需 API Key, 离线安全保底)` | `frontend/dist/paw_console.js` | `<option value="offline">纯离线规则推理 (无需 API Key, 离线安全保底)</option>` |
| 25 | `测试当前模型连通性` | `frontend/dist/paw_console.js` | `<button class="paw-btn" id="paw-cfg-test">测试当前模型连通性</button>` |
| 26 | `保存所有设置` | `frontend/dist/paw_console.js` | `<button class="paw-btn paw-btn-primary" id="paw-cfg-save">保存所有设置</button>` |
| 27 | `添加监控文件夹` | `frontend/dist/paw_console.js` | `<button class="paw-btn" id="paw-watch-add">添加监控文件夹</button>` |
| 28 | `开始全量扫描` | `frontend/dist/paw_console.js` | `<button class="paw-btn paw-btn-primary" id="paw-scan-now">开始全量扫描</button>` |
| 29 | `核准并写入` | `frontend/dist/assets/index-qIwUsXqw.js` | `children:"核准并写入"`, `onClick:()=>u(n.id)` |
| 30 | `批量核准入库` | `frontend/dist/paw_console.js` | `<button class="paw-btn paw-btn-primary" id="paw-batch-approve">批量核准入库</button>` |
| 31 | `30秒读懂这个项目` | `frontend/dist/assets/index-qIwUsXqw.js` | `children:"30秒读懂这个项目"` |
| 32 | `一句话读懂` | `frontend/dist/assets/index-qIwUsXqw.js` | `label:"一句话读懂"` |
| 33 | `当前做到哪里` | `frontend/dist/assets/index-qIwUsXqw.js` | `label:"当前做到哪里"` |
| 34 | `当前最大问题` | `frontend/dist/assets/index-qIwUsXqw.js` | `label:"当前最大问题"` |
| 35 | `下一步` | `frontend/dist/assets/index-qIwUsXqw.js` | `label:"下一步"` |
| 36 | `发送` | `frontend/dist/assets/index-qIwUsXqw.js` | `children:"发送"` |
