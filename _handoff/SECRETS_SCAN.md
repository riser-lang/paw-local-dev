# PAW 本地版 安全审计与排查报告 (SECRETS_SCAN)

> **审计时间**：2026-10-13 21:14 (北京时间)  
> **执行人**：Gemini (执行者)  
> **审计对象**：`d:\PAW\paw-beta` 代码库全量文件及根目录  
> **依据规范**：红线要求（无真实 Key/密码/凭据/个人数据，单文件不得大于 50MB）

---

## 1. 扫描范围与方法
本次扫描采用多维度深度脚本检测，覆盖以下规则与范围：
1. **API Key 与令牌特征匹配**：
   - OpenAI / DeepSeek / 通义千问格式：`sk-[a-zA-Z0-9_-]{20,}`
   - GitHub Token 格式：`(ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{20,}`
   - 私钥文件头：`-----BEGIN (?:RSA |EC )?PRIVATE KEY-----`
   - AWS / 云元数据凭据：`(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}`
   - JWT 令牌结构：`eyJ...\.eyJ...\....`
2. **配置文件敏感字段审查**：
   - 全盘搜索所有 `config.json`、`.env*`、`*.yaml` 中的 `*_api_key`、`password`、`token` 字段。
3. **单文件体积限制排查**：
   - 过滤所有单文件体积大于 50MB 的条目。
4. **开发机个人路径审查**：
   - 排查本地绝对路径（如包含 `C:\Users\sunao`）在提交文件中的留存情况。

---

## 2. 扫描发现与核实情况

### 2.1 API Key 与令牌检测结果
- **发现 1（虚拟 Mock Key）**：
  - `audit_check.py` 第 56 行：包含 `'sk-antigravity-test-api-key-9999'`。
  - `scripts/capture_assistant_with_provider.py` 第 47 行：包含 `'sk-paw-editorial-test-key'`。
  - `scripts/verify_a1_a21.py` 第 112 行：包含 `'sk-deepseek-live-secret-key-12345678'`。
  - `scripts/verify_installed_e2e.py` 第 89 行：包含 `'sk-test-acceptance-secret-token-8888'`。
  - `PAW_本地版_第一次发布验收整改报告_R1.md`：文档中记载了 `'sk-test-acceptance-secret-token-8888'`。
  - **核实判定**：以上均为单元测试或自动化脚本中的**脱敏虚拟测试假值**（Dummy/Mock 值），均非有效真实密钥，不构成泄露风险。
- **发现 2（生产与默认配置文件）**：
  - 源码根目录 `config.json`：核验所有 `deepseek_api_key`、`openai_api_key`、`claude_api_key` 均为空字符串 `""`。
  - 本地运行时 `data/config.json`：包含测试运行留存的 DPAPI 密文，但整个 `data/` 目录属于本地用户运行时数据，已被 `.gitignore` 完全排除，不会被 git 追踪或推送。
- **结论**：代码树中**无任何真实 API Key 或在线令牌**。

### 2.2 大于 50MB 文件排查结果
全盘扫描体积大于 50MB 的文件结果如下：

| 文件路径 | 体积 (MB) | 是否会被推送 | 状态说明 |
|---|---|---|---|
| `paw-beta/data/workspace.db` | 449.72 MB | **否 (已忽略)** | 本地运行时用户数据库，已在 `.gitignore` 排除 |
| `paw-beta/data/backups/workspace_backup_...` | 455.88 MB | **否 (已忽略)** | 本地运行时数据库备份，已在 `.gitignore` 排除 |
| `paw-beta/data/backups/workspace_backup_...` | 449.71 MB | **否 (已忽略)** | 本地运行时数据库备份，已在 `.gitignore` 排除 |

- **结论**：**待推送到 Git 的源码清单中，没有任何单个文件体积超过 50MB**（最大的单一代码/数据文件为 `backend/runtime/reader-runtime/PROJECT_CLASSIFICATION.json`，大小仅为 0.71 MB；空白模板数据库 `template/workspace_template.db` 大小为 0.47 MB）。完全符合 GitHub 规范。

### 2.3 个人隐私与本地路径排查
- 历史验收日志（如 `install.log`、`verification_logs/`）中存在本地绝对路径引用。
- **处置方案**：
  - 已更新 `.gitignore`，将所有 `*.log`、`verification_logs/`、`fix_run/`、`test_env/`、`scratch/`、`_to_delete/`、`*.key`、`*.pem`、`*.pfx` 全面列入排除规则。
  - 确保推送到 GitHub 仓库的仅包含纯净源码、静态编译前端、安装打包配置及干净的空数据库模板。

---

## 3. 最终审计结论
本次安全扫描**未发现任何有效真实密钥、云凭据或超大二进制文件**。针对全部 25 个 git 历史提交的审计排查：**已覆盖全部 git 历史，见 REVIEW-01**（历史记录中无真实 Key、无 >2MB 文件，无泄露敏感项）。代码库已做好安全隔离与历史验证，符合推送到 GitHub 私有仓库的安全标准。
