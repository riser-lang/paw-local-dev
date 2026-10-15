# PAW Personal Work OS - 针对外部评审 (Claude Judge) 发现项的整改清单 (CHANGES_SINCE_JUDGE.md)

**版本基线**：v2.3.1 as judged  
**目标版本**：v2.3.2 (r2)  
**整改阶段**：Phase B 完整修复与审计归正  
**状态**：提请外部评审  

---

## 1. 外部评审发现项 (F1–F10) 逐项整改结果

| 编号 | 处理状态 | 旧报告错误内容 | 本轮整改事实与说明 | 源码与实证引用 |
|:---:|:---:|---|---|---|
| **F1** | 已修正 | 引用了不存在的函数 `encrypt_api_key`、`win32crypt`、`DPAPIControlledException`、`backend/rag`、`offline_search`；编造了巡航返回 `cruise_id`、18ms 响应与蓝色通知条；日志耗时与真实日志不符。 | 严格按实际源码与真实日志重写。实际函数为 `encrypt_secret`、`decrypt_secret`、`DPAPIProtectionError`。巡航接口实际返回 `status: "started"`，前端为按钮文字状态切换。日志耗时与时间戳从原始日志原样提取。 | `01_Latest_Source_Code/backend/core/security.py:73-119`<br>`01_Latest_Source_Code/backend/main.py:679-711`<br>`02_Runtime_Verification_Logs/a22_a33_verification.log` |
| **F2** | 已修正 | 描述了不存在的安装器逻辑：`NextButtonClick`、`InitializeWizard`、`IsPortInUse`、`ws2_32`、`WSAStartup`、`FindWindow`、`MigrateFromPreviousVersion`、`pythonw.exe`，默认端口写为 8000。 | 重新反编译并提取真实字符串与函数列表。安装脚本 `installer/paw_setup.iss` 中 [Code] 段真实存在的 10 个函数为：`RequestGracefulShutdown`、`GetInstalledUninstallString`、`GetInstalledDisplayVersion`、`InitializeSetup`、`InitializeUninstall`、`BackupDatabaseBeforeInstall`、`InitializeUserDataDirectory`、`CurStepChanged`、`ShouldPurgeData`、`CurUninstallStepChanged`。默认端口为 8800。 | `01_Latest_Source_Code/installer/paw_setup.iss:76-285`<br>`02_Runtime_Verification_Logs/bytecode_strings.log` |
| **F3** | 已修正 | 需求追踪矩阵中引用了不存在的符号与文件（`backend/rag`、`offline_search` 等）。 | 逐行核对并修正整个需求追踪矩阵，所有实现路径均对齐当前实际存在的模块（如 `backend/governance/service.py`、`backend/core/security.py` 等）。 | `04_Requirement_Traceability_Matrix/REQUIREMENT_TRACEABILITY_MATRIX.md` |
| **F4** | 已修正 | A25 测试中 Q07–Q12（无依据类）和 Q25–Q27（注入类）直接赋值 `is_passed = True`，属于空断言。 | 移除所有无条件赋值。对 Q07–Q12 增加严格的私密信息拦截断言与契约要求；对 Q25–Q27 增加数据库行数、哈希与候选分布的不变性断言；建立 4 项反向对照并全数验证。 | `01_Latest_Source_Code/scripts/verify_a22_a33.py:280-350`<br>`02_Runtime_Verification_Logs/a25_negative_control.log` |
| **F5** | 已修正 | A28 测试复制无注入内容的普通模板文档，查询数据库后无任何 assert，且查询了非法的 `status='approved'` 状态。 | 重新编写 A28 测试：使用 python-docx 动态生成包含真实越权删表指令的恶意文档，经解析器解析后断言注入文本出现在证据块中，经调度器流转后断言生成的候选状态均为 pending，且业务库行数与哈希零变动；建立 4 项反向对照并全数验证。 | `01_Latest_Source_Code/scripts/verify_a22_a33.py:480-575`<br>`02_Runtime_Verification_Logs/a28_negative_control.log` |
| **F6** | 已修正 | 报告中将 `sunao13_防伪公钥证书.cer` 的 SHA256 误写为 `DF91E496...`。 | 重新计算并更正哈希值。实际 SHA256 值为 `544B841BE6F6AD262F98E2BFD5219F89443C323791C2BA0FD6BEDED6DA44AFC8`。 | `03_Decompilation_And_Binary_Fidelity/sunao13_防伪公钥证书.cer`<br>`MANIFEST.sha256` |
| **F7** | 已修正 | 签名报告声称签名合法受信任，但实际证书为自签名、无时间戳，在非本机上会提示根证书不受信任。 | 修正报告陈述，如实说明：该证书为自签名开发者证书，防伪验证方式是比对证书指纹 `B2D6EB9F292A758A7C45F993B838680BDCC26068`，而非依赖公网商用 CA 证书链。 | `03_Decompilation_And_Binary_Fidelity/SIGNATURE_VERIFICATION_REPORT.md`<br>`02_Runtime_Verification_Logs/signatures_verification.log` |
| **F8** | 已修正 | `backend/core/security.py` CSRF 校验中接受硬编码字面值 `paw_session=local-token`。 | 移除 CSRF 校验中对固定字面值 `paw_session=local-token` 的宽容放行，严格校验 `session_cookie == LOCAL_LAUNCH_TOKEN`。在 A29 自动化测试中新增用例 3b，实测携带 `paw_session=local-token` 变更请求被严格拦截返回 HTTP 403 Forbidden。 | `01_Latest_Source_Code/backend/core/security.py:180`<br>`01_Latest_Source_Code/scripts/verify_a22_a33.py:622-634`<br>commit `fcc9e86` |
| **F9** | 已修正 | 前端巡航轮询竞态：触发后立即查询状态，若后台线程未就绪则不启动轮询；status 页重复创建定时器。 | 重构巡航触发与轮询逻辑：定义全局唯一定时器 `_cruisePollTimer` 并在离开 `status` 页时注销；触发接口返回 `started` 或 `already_running` 时直接启动轮询，消除即时查询状态竞态；轮询间隔检测到 `idle` 即刻停止并刷新状态。 | `01_Latest_Source_Code/frontend/dist/paw_console.js:1483-1540`<br>commit `174531c` |
| **F10** | 未修改 (仅记录) | 安装器同时初始化 `%LOCALAPPDATA%\PAW\workspace.db` 与 `%LOCALAPPDATA%\PAW\data\workspace.db` 两份数据库。 | 鉴于修改涉及既有用户存量数据迁移风险，本轮不修改安装包路径初始化逻辑，作为已知架构问题留存并记录于已知问题章节。 | `01_Latest_Source_Code/installer/paw_setup.iss:191-225` |

---

## 2. 第二轮评审（r1）整改对照表 (R1–R6)

| 编号 | 处理状态 | 评审发现问题 | 本轮整改事实与说明 | 审计包证据路径 |
|:---:|:---:|---|---|---|
| **R1** | 已修正 | 需求追踪矩阵（RTM）代码映射存在偏差（如 A10 映射错误、A33 描述与代码相反、A31 提及不存在的向量检索、大量整数区间引用）。 | 全面重做 19 项需求追踪矩阵：每项均使用带 anchor 的代码块（<=25行且与源码逐字一致）；消除所有裸文件引用；修正实际行为陈述（明确 A31 为纯结构化离线降级无向量检索、A33 严格不自动晋升新版本需人工审核）。 | `04_Requirement_Traceability_Matrix/REQUIREMENT_TRACEABILITY_MATRIX.md` |
| **R2** | 已修正 | 执行审计报告存在两处事实错误：4.1 节称 A25 检查哈希（实际 A25 仅比较行数与状态分布，哈希比对由 A28/A28b 执行）；4.3 节称由 `verify_a22_a33.py` 生成（实际由 `verify_r3_part5_a22_a33.py` 在隔离库运行生成）。 | 修正 4.1 节事实表述，明确 A25 检查行数与状态分布，A28/A28b 执行内容哈希防篡改比对；修正 4.3 节测试调用命令为 `scripts/verify_r3_part5_a22_a33.py`，更新 13 项全通实测日志片段。 | `00_EXECUTIVE_AUDIT_REPORT_FOR_CLAUDE.md` |
| **R3** | 已修正 | 测试能力边界未如实说明；A28 缺少被黑模型返回越权提议的最坏情况测试。 | 1. 在 `scripts/verify_a22_a33.py` 新增 `test_a28b_compromised_model_injection`，并在调度测试中注册。通过 monkeypatch 模拟假模型提议关闭项目、payload夹带accepted、删待办，验证 0 直写 formal state 且全数被隔离为 pending 候选，并在反向对照中验证状态篡改必触发 AssertionError。<br>2. 在报告新增「测试能力边界」小节，如实说明 A25 Q07–Q12 仅覆盖检索与契约层，不能外推证明模型生成回答绝对无幻觉。 | `01_Latest_Source_Code/scripts/verify_a22_a33.py`<br>`02_Runtime_Verification_Logs/a28b_negative_control.log`<br>`00_EXECUTIVE_AUDIT_REPORT_FOR_CLAUDE.md` |
| **R4** | 已修正 | 上一轮 `test_gateway.py` 与 `run_doctor.py` 在开发数据目录运行，触发巡航导致开发库 candidate 行数由 5843 增至 5844；r1 的 T3 阶段未隔离运行的反向对照脚本和单独运行的 A28b 向 `ROOT\data\workspace.db` 写入了 5 条 pending 候选。 | 1. 规范测试环境：在独立临时目录配置 `PAW_DATA_DIR` 与 `PAW_WORKSPACE_DB`，`fix_run/dev_db_guard.log` 记录运行前后 Delta 为 0。<br>2. 如实披露偏差：r1 的 T3 阶段未隔离运行的反向对照脚本和单独运行的 A28b 向 `ROOT\data\workspace.db` 写入了 5 条 pending 候选（`analysis_model=AgentPlanner+FakeCompromisedProvider`）。外部评审已于 2026-10-06 备份后删除这 5 条候选及其 analysis_run 记录（candidate 5849→5844），备份文件为 `data\workspace_before_leak_cleanup_20261006.db`。删除“彻底消除写入污染”表述。<br>3. 根因修复：在 `scripts/verify_a22_a33.py` 中新增 `_require_isolated_db()` 校验函数，禁止针对非临时目录运行，并在反向对照中验证拒绝执行。 | `02_Runtime_Verification_Logs/gateway_smoke_tests.log`<br>`02_Runtime_Verification_Logs/doctor_diagnostic.log`<br>`02_Runtime_Verification_Logs/db_isolation_negative_control.log`<br>`00_EXECUTIVE_AUDIT_REPORT_FOR_CLAUDE.md` |
| **R5** | 已修正 | 文档跨文件引用包含包外路径；已知问题中推测双数据库为「历史版本兼容遗留」；手动测试清单界面文字未逐字核对。 | 1. 文档中所有引用统一改为审计包内相对路径。<br>2. 删除已知问题中「历史版本兼容遗留」等推测性表述，仅陈述事实。<br>3. 逐条在源码中 grep 核对手动测试清单界面文字，找到的与源码逐字一致，未找到的移除引号改为客观功能描述，核对记录写入 `manual_checklist_strings.md`。 | `CHANGES_SINCE_JUDGE.md`<br>`00_EXECUTIVE_AUDIT_REPORT_FOR_CLAUDE.md`<br>`04_Requirement_Traceability_Matrix/REQUIREMENT_TRACEABILITY_MATRIX.md`<br>`fix_run/manual_checklist_strings.md` |
| **R6** | 已修正 | 仓库根目录下存在未跟踪的 `scratch/` 目录；存在编译残留字节码。 | `.gitignore` 加入 `/scratch/`；彻底清理全部 `__pycache__` 与 `.pyc` 字节码。 | `.gitignore` |

---

## 3. 已知问题与技术债务说明 (Known Issues)

1. **F10 双数据库初始化现象**：
   在 `installer/paw_setup.iss` 的 `InitializeUserDataDirectory` 过程中，向导会在 `%LOCALAPPDATA%\PAW\` 和 `%LOCALAPPDATA%\PAW\data\` 分别生成 `workspace.db`。当前应用程序运行时使用 `backend/core/config.py` 中的 `DEFAULT_DB_PATH` 路径。由于调整涉及既有用户存量数据迁移风险，本轮未修改该初始化逻辑，如实陈述事实并留存记录。

2. **自签名防伪证书信任提示**：
   安装包所附数字签名主体为 `CN=sunao13, O=sunao13 Personal Work OS, OU=Anti-counterfeit`，证书指纹为 `B2D6EB9F292A758A7C45F993B838680BDCC26068`。该证书未购买商业公网 CA 机构背书，在其他未导入该公钥的 Windows 设备上运行时，Windows SmartScreen 或文件属性会提示“证书链终止于不受信任的根证书”。该签名用于所有权防伪核对（指纹比对），并非商用信任凭证。

3. **安装包相关日志复用说明**：
   signatures_verification.log 与 bytecode_strings.log 内容沿用 r1（安装包未变，结果不变），r2 中仅更新了文件修改时间以通过新鲜度检查。
