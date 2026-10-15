# PAW 本地版 首次推送文件清单 (PUSH_MANIFEST)

> **统计基准**：`git ls-files` 实际跟踪结果（依据 REVIEW-01 与 TASK-G-00 执行清理后）  
> **准备推送分支**：`main`（首次初始化推送，等待审核通过）  
> **文件总数**：386 个文件  
> **总传输体积**：约 **7.11 MB** (7,458,600 字节)  
> **超限文件排查**：无大于 50MB 的文件（最大文件为 725.3 KB）  
> **敏感信息排查**：已排除所有本地测试日志、运行时数据、用户个人目录痕迹  

---

## 1. 目录结构与体积概览（按实际跟踪文件汇总）

| 模块目录 | 跟踪文件数 | 磁盘占用 | 包含核心内容 |
|---|---|---|---|
| **backend/** | 283 | 5.40 MB | Python 后端服务、文档解析器、治理服务、巡航调度器、免 Key 搜索模块 |
| **frontend/dist/** | 4 | 0.40 MB | 静态编译的 React 18 控制台前端页面资源 (`index.html`, `index.js`, `index.css`) |
| **template/** | 1 | 0.47 MB | 干净初始化的 SQLite 数据库模板 (`workspace_template.db`，35 张空表) |
| **installer/** | 2 | 0.01 MB | Inno Setup 6 安装程序配置脚本 (`paw_setup.iss`, `LICENSE.txt`) |
| **scripts/** | 54 | 0.45 MB | 构建打包脚本 (`build_windows_dist.py`)、Mock 服务、自动化校验用例 |
| **tests/** | 13 | 0.19 MB | 脱机单元测试集与网络搜索 HTML 结构测试夹具 (`fixtures/`) |
| **docs/** | 10 | 0.09 MB | 技术说明文档、需求追踪矩阵、使用说明 |
| **_handoff/** | 6 | 0.02 MB | 交接管理与审核材料 (`PROJECT_MAP`, `SECRETS_SCAN`, `STATUS`, `REVIEW-01`, `TASK-G-00`) |
| **根目录配置** | 13 | 0.07 MB | `run.py`, `paw_desktop.spec`, `requirements.txt`, `config.json`, `.gitignore` 等 |
| **总计** | **386** | **7.11 MB** | **轻量纯净代码库，全集完整，无测试遗留日志** |

---

## 2. 核心大文件排查（前 10 大文件明细）
经核验，待推送清单中体积最大的前 10 个文件均处于正常文本/结构化资源范围（远低于 50MB 阈值）：

1. `backend/runtime/reader-runtime/PROJECT_CLASSIFICATION.json` - 725.3 KB
2. `template/workspace_template.db` - 484.0 KB
3. `frontend/dist/assets/index-qIwUsXqw.js` - 264.0 KB
4. `backend/runtime/legacy/paw_incremental_decision_pipeline.py` - 154.3 KB
5. `tests/fixtures/search_html/bing_sample.html` - 128.7 KB
6. `backend/runtime/core/paw_gpt_review_bundle.py` - 113.1 KB
7. `backend/runtime/App.tsx` - 107.6 KB
8. `frontend/dist/paw_console.js` - 91.5 KB
9. `backend/runtime/legacy/paw_quick_capture_write_bridge.py` - 77.3 KB
10. `backend/runtime/legacy/paw_wps_0600_incremental_mirror.py` - 71.9 KB

---

## 3. 严格排除清单（已验证不再被 git 跟踪）
以下内容已在本次清理中成功取消跟踪或被 `.gitignore` 严格拦截，绝不进入推送：
- 历史测试日志：`install.log`、`fix_run/` (9个文件)、`verification_logs/` (7个日志) —— **已执行 `git rm --cached`，本地文件完好保留**
- 本地运行数据库：`data/workspace.db` (449.72 MB)
- 历史备份文件：`data/backups/*.db` (合计超 900 MB)
- 本地打包编译中间体：`dist/` (134 MB)、`build/` (40 MB)
- 本地测试环境与缓存：`test_env/` (32 MB)、`__pycache__/`、`*.pyc`
- 屏幕截图与图片：`*.png`
- 本地配置与密钥：`data/config.json`（含本地 DPAPI 密文）
- 临时文件与待清理目录：`scratch/`、`_to_delete/`
