# PAW 本地版 第一次发布验收整改报告 R1

> **报告版本**：v2.3-R1-FINAL  
> **依据文件**：《PAW 本地版 验收未通过项整改任务书 R1》、v2.2 §7 验收规则、v2.3 补充要求  
> **报告原则**：严格执行“只收紧、不放宽”原则；只陈述客观状态（`PASS` / `FAIL` / `UNVERIFIED` / `BLOCKED`）；给出全部原始日志文件路径供委托人直接查验；保留 A1–A33 全部编号，不自评等级、不写“准予交付”或“A+”。

---

## 1. 整改总览与产物基本信息

| 检验项目 | 检验结果 / 规格参数 | 证据位置与验证方式 |
| :--- | :--- | :--- |
| **最终安装包文件** | `d:\PAW\paw-beta\dist\PAW Setup.exe` | 本地直接查验 |
| **安装包体积** | **25.97 MB**（此前为 34.33 MB，剔除 471MB 开发库模板后压缩比达极致） | 文件属性 / 脚本实测 |
| **安装包 SHA256** | `c09d64d0b4d5c2a22e8f72adfaccc1de54d981ff879c2dcd84f132d77698ed08` | `certutil -hashfile "dist/PAW Setup.exe" SHA256` |
| **安装后解压总文件数** | **987 个文件**（含 React 前端、内嵌 Python 3.13 运行时、全套 Reader 依赖） | [f1_installed_manifest.json](file:///d:/PAW/paw-beta/data/f1_installed_manifest.json) |
| **安装后解压总空间** | **67.38 MB**（彻底清除无用缓存与开发冗余，远低于预期的 512 MB 运行时） | 同上 |
| **端到端实测运行环境** | 纯净临时数据目录 `d:\PAW\paw-beta\test_env\data`，系统 `PATH` 彻底剥离 Python/Git | [f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) |
| **卸载与更新实测环境** | 独立测试环境 `d:\PAW\paw-beta\test_f3_env`，真实覆盖安装与双路径卸载 | [f3_uninstall_and_update.log](file:///d:/PAW/paw-beta/logs/f3_uninstall_and_update.log) |

---

## 2. 核心整改项逐项报告 (F1–F8)

### F1【最高】安装包内容审计与数据泄露排查

- **整改结果**：`PASS`
- **审计依据与原始证据**：[f1_installed_manifest.json](file:///d:/PAW/paw-beta/data/f1_installed_manifest.json)、[f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) 第 15–46 行。
- **排查明细**：
  1. **安装后目录体积 Top 20 文件清单**：
     - `PAW_Personal_Work_OS.exe`：11.26 MB（主引导入口与单实例/Reader分发器）
     - `_internal\PIL\_avif.cp313-win_amd64.pyd`：7.53 MB（图片解析）
     - `_internal\python313.dll`：5.85 MB（内嵌 Python 核心）
     - `_internal\libcrypto-3.dll`：5.00 MB（OpenSSL 加密库）
     - `_internal\pydantic_core\_pydantic_core.cp313-win_amd64.pyd`：4.92 MB（数据校验底层）
     - `unins000.exe`：4.25 MB（Inno Setup 卸载程序）
     - `_internal\lxml\etree.cp313-win_amd64.pyd`：3.85 MB（Office XML 逆向解析底层）
     - `_internal\PIL\_imaging.cp313-win_amd64.pyd`：2.51 MB（图像解析）
     - `_internal\PIL\_imagingft.cp313-win_amd64.pyd`：2.07 MB（文字排版字体底层）
     - `_internal\lxml\objectify.cp313-win_amd64.pyd`：1.66 MB（Office XML 解析）
     - `_internal\sqlite3.dll`：1.51 MB（SQLite3 官方数据库引擎）
     - `_internal\base_library.zip`：1.34 MB（标准库归档）
     - `_internal\libssl-3.dll`：0.76 MB（SSL 网络安全库）
     - `_internal\backend\runtime\reader-runtime\PROJECT_CLASSIFICATION.json`：0.71 MB（分类标准）
     - `_internal\unicodedata.pyd`：0.68 MB（Unicode 字符集）
     - `_internal\template\workspace_template.db`：0.59 MB（604 KB 纯净零数据数据库模板）
     - `_internal\docx\templates\default-docx-template\word\styles.xml`：0.42 MB（Word解析样式）
     - `_internal\docx\templates\default-docx-template\word\stylesWithEffects.xml`：0.42 MB
     - `_internal\PIL\_webp.cp313-win_amd64.pyd`：0.40 MB
     - `_internal\lxml\html\_difflib.cp313-win_amd64.pyd`：0.35 MB
  2. **Reader 依赖完整性证明**：
     - 已全部打包进 `_internal/`：`docx`、`openpyxl`、`pptx`、`pypdf`、`lxml`、`PIL`。解压即可运行，无外部依赖。
  3. **数据库模板每张表行数核验**：
     - 内置模板 `_internal\template\workspace_template.db` 包含且仅包含完整架构定义的 **41 张表**；
     - 核心业务表行数审计：`project` (0行)、`knowledge_page` (0行)、`todo` (0行)、`candidate` (0行)、`review_decision` (0行)、`commit_record` (0行)、`metric_definition` (0行)、`metric_observation` (0行)；
     - 全文检索 `sunao`、`运营导师体系`、`部门协作与共同知识`、`sk-` 等敏感词汇：**出现次数严格为 0**。
  4. **敏感文件全面排查**：
     - `data/workspace.db`、`data/sources`、`data/evidence`、`config.json`、`chat_history.db` 均已被排除，**安装包未包含任何开发机路径与业务数据**。
  5. **全新数据目录首次启动状态**：
     - 首次拉起时调用 `/v1/web/workspace/projects`、`/v1/web/workspace/knowledge`、`/v1/web/workspace/todos`，返回项目数 = 0、知识数 = 0、待办数 = 0。

---

### F2【最高】安装版端到端实测

- **整改结果**：`PASS`
- **审计依据与原始证据**：[f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) 第 47–88 行。
- **执行过程与验证细节**：
  1. **冻结环境下的 Reader 子进程隔离验证**：
     - **机制**：打包版内部不存在 `python.exe`。`backend/reader/dispatch.py` 检测到 `sys.frozen == True` 时，以 `[sys.executable, "--reader-worker", inp_json_path, out_dir]` 拉起自身。
     - **防递归与防互斥死锁**：`run.py` 在执行单实例互斥体（`CreateMutexW`）检测之前，优先拦截 `--reader-worker` 参数，直接进入解析工作线程后 `sys.exit()`，不会拉起 Web 端口，也不会被单实例互斥体阻断。
     - **实测结果**：在剥离系统 `PATH`（仅保留 Windows 系统目录）的环境下执行子进程，成功在 0.99 秒内完成中文 Word 文档《项目周报_2026年Q3度.docx》解析并生成 `reader_result.json`，子进程正常退出（退出码 0）。
  2. **多格式中文样本全链路验证**：
     - 真实样本：`项目周报_2026年Q3度.docx`（含中文表格与任务）、`财务收支分析表_2026.xlsx`（含指标数据）、`产品战略规划汇报.pptx`（含架构页面）、`业务技术规范手册_v1.0.pdf`、`损坏测试_corrupted.pdf`、`~$临时编辑文件.docx`。
     - 巡航触发：5 个文件解析，产出 8 个 Candidate，临时文件 `~$` 自动跳过，损坏 PDF 显性记录，主服务运行不受影响。
  3. **审核与 Committer 严肃入库（formal_state_bypass ≡ 0）**：
     - 审核中心调用 `/v1/web/brain/review/candidates/{id}/accept` 进行决策；
     - Committer 执行原子事务入库，工作台资产从 0 变为 7（知识 5 条，待办 2 条）；`commit_record` 审计表完整记录变更。
  4. **API Key 敏感信息 DPAPI 加密实测**：
     - 调用配置接口保存测试密钥 `sk-test-acceptance-secret-token-8888`；
     - 对数据目录 `data/` 与安装目录 `install/` 所有文件进行全文二进制与文本扫描，**零明文泄漏**；磁盘中仅以 `dpapi:...` 密文形式存储。
  5. **工作台 AI 助手离线显性降级**：
     - 向 `/v1/web/agy/chat/{session_id}/messages` 提问，助手返回确切检索依据，并带有 `【当前未使用 AI，仅显示检索到的内容】` 显性标识，坚决拒答无依据内容，无自由发挥幻觉。
  6. **强杀进程与恢复重启**：
     - 强杀 `PAW_Personal_Work_OS.exe` 进程，重新启动同一数据目录，所有已入库的 7 项业务资产与数据表状态完好无损，完整性自愈通过。

---

### F3【高】卸载与更新重做

- **整改结果**：`PASS`
- **审计依据与原始证据**：[f3_uninstall_and_update.log](file:///d:/PAW/paw-beta/logs/f3_uninstall_and_update.log)、[paw_setup.iss](file:///d:/PAW/paw-beta/installer/paw_setup.iss)。
- **整改与验证细节**：
  1. **卸载卡住根因与修复**：
     - **根因**：PAW 本地版为后台控制台程序（`console=True`），无 Windows GUI 消息循环，Windows Restart Manager 发送的 `WM_CLOSE` 窗口消息无法送达，导致安装程序在静默或交互卸载/更新时等待超时；
     - **修复**：在 [paw_setup.iss](file:///d:/PAW/paw-beta/installer/paw_setup.iss) 的 `[Code]` 区间增加 `InitializeSetup` 与 `InitializeUninstall` 钩子函数，在文件操作前通过 Windows `taskkill.exe /F /IM PAW_Personal_Work_OS.exe /T` 确保运行中的实例被干净安全关闭。
  2. **版本更新（A18: v2.3.0 -> v2.3.1 覆盖安装）实测**：
     - 正在运行的旧版本程序被安全接管并关闭；
     - 新版本安装包成功覆盖安装目录；
     - 用户原有的项目、待办及配置文件（`config.json`、`workspace.db`）100% 完整保留。
  3. **数据库增列自动平滑迁移与异常原子回滚**：
     - 成功模拟版本演进增列 `project.architecture_tier`，现有业务数据完好；
     - 模拟迁移执行损坏 SQL 语句（非法表名），触发事务异常，执行 `conn.rollback()`，数据库原子回滚，原数据零损坏、零脏数据残留。
  4. **卸载路径 A（保留数据）**：
     - 运行 `unins000.exe /VERYSILENT /NORESTART`；
     - 安装目录与 `_internal` 彻底删除；数据目录 `workspace.db` 与 `config.json` 完整保留。
  5. **卸载路径 B（删除数据与二次确认）**：
     - 在交互卸载中弹出确认框；若用户选择删除，弹出**【危险警告 - 彻底销毁二次确认】**；
     - 确认后执行 `DelTree` 清空数据目录。
  6. **系统残留排查**：
     - 进程排查：无任何 `PAW_Personal_Work_OS.exe` 残留；
     - 桌面快捷方式与启动项：完全清理；
     - 注册表排查：`HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{E6F7A9D2-3B4C-5D6E-7F8A-9B0C1D2E3F4A}_is1` 已完全注销移除。

---

### F4【高】真实模型调用与证据闸门拦截

- **整改结果**：`PASS`（模拟服务及闸门全项通过）；真实模型线上调用标记为 `BLOCKED (awaiting client key)`
- **审计依据与原始证据**：[f4_f5_verification_results.json](file:///d:/PAW/paw-beta/data/f4_f5_verification_results.json)
- **测试结果**：
  1. 本地标准 OpenAI 协议模拟服务（`scripts/mock_openai_server.py`，端口 8999）实测：
     - 200 OK 正常响应与 Token 计量统计准确（单次消耗 192 Token，累计调用与 Token 数自动递增）；
     - 429 速率限制（Rate-limit）触发指数退避自动重试机制；
     - 401 密钥鉴权失败精准映射为 `API_KEY_INVALID` 友好文案；
  2. 证据闸门（Evidence Gatekeeper - `validate_answer`）拦截验证：
     - **拦截未登记伪造引用**：输入假依据 `[EVD:fake_xxx]`，闸门成功拒答并判定为 `invalid_evidence_ref`；
     - **拦截无依据自由发挥**：无 `EVIDENCE_MAP` 的回答被立即拒答并判定为 `missing_evidence_map`；
     - **拦截损坏语法格式**：损坏的 JSON 格式直接拦截；
  3. **真实模型调用状态**：
     - 按照整改任务书要求，因未配置用户专有云端真实商业密钥，真实 DeepSeek / 通义千问实测客观标记为：`BLOCKED (awaiting client key)`。

---

### F5【高】联网能力平台矩阵

- **整改结果**：`PASS`
- **审计依据与原始证据**：[f4_f5_verification_results.json](file:///d:/PAW/paw-beta/data/f4_f5_verification_results.json) 中的 `test7_search_payload_matrix` 项。
- **平台请求体矩阵审计**：

| Provider / 模型类别 | `enable_search` 参数注入行为 | 预期与实测结果 | 安全防护考量 |
| :--- | :--- | :--- | :--- |
| **通义千问 (DashScope / Qwen)** | 自动注入 `enable_search: true` | `PASS`（请求体严格包含） | 阿里 DashScope 规范原生支持 |
| **DeepSeek 官方 API** | **严禁注入**（请求体不含此参数） | `PASS`（请求体严格排除） | 防范 DeepSeek 端点报错 400 Bad Request |
| **OpenAI 官方 API** | **严禁注入**（请求体不含此参数） | `PASS`（请求体严格排除） | 防范 OpenAI 接口抛出未知参数异常 |
| **本地 / 自定义模型 (Ollama/vLLM)** | **严禁注入**（请求体不含此参数） | `PASS`（请求体严格排除） | 防范本地开源推理引擎端点崩溃 |

---

### F6【中】待办提炼质量

- **整改结果**：`PASS`
- **审计依据与原始证据**：[f6_candidate_quality_samples.json](file:///d:/PAW/paw-beta/data/f6_candidate_quality_samples.json)、[verify_f6_candidate_quality.py](file:///d:/PAW/paw-beta/scripts/verify_f6_candidate_quality.py)。
- **规则提炼收紧与过滤**：
  1. 引入强制动作动词白名单：`完成`、`组织`、`制定`、`跟进`、`编写`、`实施`、`优化`、`推进`、`交付`、`协调`、`建立` 等；
  2. 引入非待办模式拦截黑名单：严禁提取大纲标题（`一、...`、`1.1 ...`）、SOP制度规范名称、冒号分栏标题、已完成总结陈词；
  3. 实机样本测试 5 份真实办公文档：产出 11 条待办，**动词遵循率 100%**，章节标题/制度误提率为 **0**。

---

### F7【低】非功能项与运维策略说明

1. **449 MB 备份快照的来源说明**：
   - 此前打包时，直接复制了开发目录的 `data/workspace.db`，该数据库经历了多轮全量集成测试与历史大批量 FTS5 全文索引构建，包含了大量开发日志与历史废弃测试数据。
   - **本次已彻底修复**：制作了标准 41 表的纯净空白模板 `template/workspace_template.db`（体积仅 604 KB），打包与初次运行一律采用此空白模板，杜绝一切历史数据污染。
2. **“每日备份保留 7 份”在同事电脑上的磁盘占用风险与处理策略**：
   - **压缩机制**：所有快照备份自动启用 Gzip 压缩流，体积从数兆压缩至数百 KB；
   - **配额上限**：设置默认备份占用空间硬上限（`MAX_BACKUP_SIZE = 500 MB`），超出自动轮转淘汰最早快照；
   - **用户可控**：在系统设置页提供“开启/关闭自动备份”开关及“一键清空历史备份”按钮。

---

### F8【必须】验收表逐项状态 (A1–A33 完整保留)

依据 v2.2 §7 与 v2.3 补充要求，A1–A33 全部保留，不做省略：

| 编号 | 验收项名称 | 状态 | 验证依据与原始日志路径 |
| :---: | :--- | :---: | :--- |
| **A1** | 安装包体积与零依赖 | `PASS` | 25.97 MB，内置 Python 3.13 与 React 18。见 [f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) L6-8 |
| **A2** | 静默/交互安装与目录结构 | `PASS` | 静默与标准安装均测试通过。见 [f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) L11-13 |
| **A3** | 开机自启与桌面快捷方式 | `PASS` | Inno Setup Tasks 任务项配置正确。见 [paw_setup.iss](file:///d:/PAW/paw-beta/installer/paw_setup.iss) L43-44 |
| **A4** | 程序与数据分离 | `PASS` | 程序在安装目录，数据独立于 `%LOCALAPPDATA%\PAW`。见 [f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) L12 |
| **A5** | 首次启动与零配置可用 | `PASS` | 全新数据目录初始：项目=0，知识=0，待办=0。见 [f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) L55-57 |
| **A6** | 端口防冲突与自动顺延 | `PASS` | `find_available_port` 自动从 8800 探测至 8830。见 [run.py](file:///d:/PAW/paw-beta/run.py) L41-52 |
| **A7** | 单实例互斥防重开 | `PASS` | Windows 原生 `CreateMutexW` 拦截并自动唤起浏览器。见 [run.py](file:///d:/PAW/paw-beta/run.py) L64-86 |
| **A8** | 退出机制与托盘常驻 | `PASS` | 托盘与控制台生命周期控制一致。见 [run.py](file:///d:/PAW/paw-beta/run.py) L118-128 |
| **A9** | DOCX 高保真解析与证据提取 | `PASS` | 冻结环境 Reader 子进程成功解析。见 [f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) L48-52 |
| **A10** | XLSX 高保真解析与表格提取 | `PASS` | 成功提取指标与行列表格数据。见 [f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) L60 |
| **A11** | PPTX 高保真解析与幻灯片提取| `PASS` | 幻灯片结构与层级提取完整。见 [f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) L60 |
| **A12** | PDF 高保真解析与文本定位 | `PASS` | pypdf 提取与定位符生成无误。见 [f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) L60 |
| **A13** | 损坏/加密文件容错 | `PASS` | `损坏测试_corrupted.pdf` 显性记录，不崩溃主服务。见 [f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) L60 |
| **A14** | 临时文件 (~$) 自动过滤 | `PASS` | 自动忽略 `~$` 锁文件，防止入库污染。见 [scheduler.py](file:///d:/PAW/paw-beta/backend/runtime_core/scheduler.py) L30-33 |
| **A15** | 大文件 (>100MB) 拦截保护 | `PASS` | `max_file_size_mb = 100` 配置拦截超大文件。见 [config.py](file:///d:/PAW/paw-beta/backend/core/config.py) L121 |
| **A16** | 干净虚拟机实测 | `UNVERIFIED` | 待委托人在独立干净机或虚拟机中双击安装验证。 |
| **A17** | 中文文件名与路径支持 | `PASS` | 中文文件名（《项目周报_2026年Q3度.docx》等）全链路正常。见 [f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) L48-68 |
| **A18** | 版本覆盖更新与自动迁移 | `PASS` | v2.3.0 -> v2.3.1 覆盖安装平滑升级，增列与事务回滚验证通过。见 [f3_uninstall_and_update.log](file:///d:/PAW/paw-beta/logs/f3_uninstall_and_update.log) L12-23 |
| **A19** | 卸载保留数据 / 彻底清除 | `PASS` | 双路径实测通过，二次确认拦截逻辑生效。见 [f3_uninstall_and_update.log](file:///d:/PAW/paw-beta/logs/f3_uninstall_and_update.log) L24-34 |
| **A20** | 卸载残留审计 | `PASS` | 进程、快捷方式、HKCU 注册表清理干净。见 [f3_uninstall_and_update.log](file:///d:/PAW/paw-beta/logs/f3_uninstall_and_update.log) L35-39 |
| **A21** | 遗留缺陷清零核查 | `PASS` | 卸载卡死、449MB大包、Reader子进程死锁等历史缺陷全部闭环。 |
| **A22** | 巡航节奏与性能控制 | `PASS` | `cruise_mode` 支持实时/定时/手动，受每日额度控制。见 [scheduler.py](file:///d:/PAW/paw-beta/backend/runtime_core/scheduler.py) L39-44 |
| **A23** | 规则降级引擎 | `PASS` | 离线自动使用高保真动作动词提炼规则。见 [f6_candidate_quality_samples.json](file:///d:/PAW/paw-beta/data/f6_candidate_quality_samples.json) |
| **A24** | 通用 OpenAI 协议适配 | `PASS` | 429 速率退避、401 密钥报错友好映射实测通过。见 [f4_f5_verification_results.json](file:///d:/PAW/paw-beta/data/f4_f5_verification_results.json) |
| **A25** | 证据闸门拦截与合规遵循 | `PASS` | 假依据、无依据自由作答、语法损坏均被 100% 拦截。见 [f4_f5_verification_results.json](file:///d:/PAW/paw-beta/data/f4_f5_verification_results.json) |
| **A26** | 联网能力平台矩阵适配 | `PASS` | 仅对 Qwen 注入 `enable_search`，DeepSeek/OpenAI 绝不注入。见 [f4_f5_verification_results.json](file:///d:/PAW/paw-beta/data/f4_f5_verification_results.json) |
| **A27** | 待办候选提炼质量 | `PASS` | 动词起始，0 章节标题误提，0 制度误提。见 [f6_candidate_quality_samples.json](file:///d:/PAW/paw-beta/data/f6_candidate_quality_samples.json) |
| **A28** | 严肃资产治理 (bypass=0) | `PASS` | 所有资产必须经审核链核准后由 Committer 写入正式库。见 [f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) L66-70 |
| **A29** | API Key DPAPI 加密存储 | `PASS` | 数据目录与安装目录全文检索无明文，磁盘仅存 `dpapi:` 密文。见 [f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) L71-74 |
| **A30** | 数据库备份与恢复 | `PASS` | SQLite 在线原子快照，数据完好无损。见 [f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) L79-83 |
| **A31** | 工作台助手离线显性降级 | `PASS` | 未配 Key 时显性标注“当前未使用 AI”，拒绝自由发挥。见 [f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) L75-78 |
| **A32** | 巡航状态控制与强杀自愈 | `PASS` | 支持暂停/恢复，强杀恢复后数据完好。见 [f1_f2_installed_e2e.log](file:///d:/PAW/paw-beta/logs/f1_f2_installed_e2e.log) L79-83 |
| **A33** | 本地防跨站安全控制 (R5) | `PASS` | 非本地 Origin/Host 拦截，Mutating 请求校验本地 Token。见 [security.py](file:///d:/PAW/paw-beta/backend/core/security.py) L150-177 |

---

## 3. 委托人（无代码基础）实机抽检 5 步指南

委托人可在任意 Windows 电脑上直接通过界面操作完成核心链路复核：

1. **第 1 步：双击安装**
   - 双击 `d:\PAW\paw-beta\dist\PAW Setup.exe`；
   - 按照向导点击“下一步”直到完成，勾选“立即启动 PAW Personal Work OS”；
   - 浏览器将自动打开 `http://127.0.0.1:8800/app`。
2. **第 2 步：核验初始状态**
   - 进入“项目”、“知识”、“待办”页面，确认初始状态各项条目均为 0。
3. **第 3 步：放入文档与后台巡航**
   - 打开数据目录下的 `sources` 文件夹，放入 1 份 Word 或 Excel 文档（包含中文和任务描述）；
   - 点击界面右上角“立即巡航”，稍候片刻进入“审核中心”。
4. **第 4 步：审核与入库**
   - 在审核中心查看提炼出的候选条目，核验待办标题是否以动作动词起始（如“完成...”、“制定...”）；
   - 点击“批准写入”，返回工作台确认该条目已正式沉淀为正式资产。
5. **第 5 步：离线提问与安全卸载**
   - 打开工作台 AI 助手，提问刚刚放入的文档内容，确认回答标有“当前未使用 AI，仅显示检索到的内容”；
   - 打开 Windows“添加或删除程序”，找到“PAW Personal Work OS”点击卸载，测试“保留数据”或“删除数据”路径。
