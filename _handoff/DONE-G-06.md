# DONE-G-06：版本号 2.3.9 + 使用说明定稿 + 发布触发 交付报告

- 交付人：Gemini
- 交付时间：2026-10-15
- 依据任务：[_handoff/TASK-G-06.md](file:///D:/PAW/paw-beta/_handoff/TASK-G-06.md) ｜ 审核反馈：[_handoff/REVIEW-07.md](file:///D:/PAW/paw-beta/_handoff/REVIEW-07.md)

---

## 1. PR 信息与云端全量流水线运行证据

- **PR 链接**：[https://github.com/riser-lang/paw-local/pull/5](https://github.com/riser-lang/paw-local/pull/5)
- **PR 目标分支**：`claude/p0-project-update` ｜ **源分支**：`gemini/G-06`
- **PR 头部提交 (HEAD)**：`f04d489`
- **云端全量 CI 运行链接**：[https://github.com/riser-lang/paw-local/actions/runs/38598860995](https://github.com/riser-lang/paw-local/actions/runs/38598860995)
- **总体判定结论**：**`VERDICT: GREEN`**（28/28 必测用例全部 PASS，3 个负对照均在预期检查项精准失败）

### 云端门禁产物 summary.md 全文

```markdown
# Pipeline verdict for 592f1df5a998ea2086cd4026110ba61e70d62545

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

## 2. 最终安装包产物信息 (built-installer-exe)

- **安装包文件名**：`PAW_Personal_Work_OS_Setup_v2.3.9.exe`（无空格，严格符合命名规范）
- **文件体积**：`26,420,358` 字节 (26.4 MB)
- **校验和文件**：`PAW_Personal_Work_OS_Setup_v2.3.9.exe.sha256`
- **SHA256 签名值**：`7CC5C7284998C8FF82824A4147672123927998E74D23E47EA9B50BFC46A7A771`

---

## 3. 全仓库搜索 2.3.8 剩余位置与保留原因

| 文件路径 | 出现位置 / 代码片段 | 保留原因 |
|---|---|---|
| `backend/repair_v238.py` | 文件名、模块名 | TASK-G-06 明确要求：**不要改** `repair_v238.py` 的文件名和模块名（属于已有的修复模块名称历史资产）。 |
| `backend/main.py` | `from backend.repair_v238 import install as _paw_install; _paw_install()` | 引用历史补丁模块 `backend.repair_v238` 入口，保持模块名一致。 |
| `paw_desktop.spec` | `'backend.repair_v238'` (hiddenimports) | 打包配置中确保 PyInstaller 将 `repair_v238` 编译入包。 |
| `scripts/verify_consistency.py` | `Compares newly built installer EXE against baseline v2.3.8 installer` 及版本归一化代码 | 该脚本用途即为与 release 的 2.3.8 底包做反汇编比对，保留基准版本指向。 |
| `scripts/verify_r4_convergence.py` | `# v2.3.8: same-name duplicates are high confidence` | 历史回归脚本说明注释，记录 2.3.8 行为特性。 |
| `scripts/verify_r6_ai_convergence.py` | `PAW v2.3.8 regression suite` | 历史回归测试脚本说明与测试标题。 |
| `_handoff/*.md` | `DONE-G-00.md`, `TASK-G-00.md`, `TASK-G-01.md`, `REVIEW-*.md` 等 | 研发交接归档记录与历史审核结论，忠实反映从 2.3.7 还原至 2.3.8 及本次升级 2.3.9 的历史演进脉络。 |
| `backend/runtime/reader-runtime/PROJECT_CLASSIFICATION.json` | `versions/486233189298/...`, `versions/486233180637/...` | 测试夹具内由特定哈希计算产生的数据串（如 `486233189298` 恰含子串 `2.3.8`），非版本号。 |
| `tests/fixtures/search_html/bing_sample.html` | 静态网页夹具中的坐标与菜单数据（`SERP,5028.1` 等） | 必应搜索离线测试页面的静态 HTML 固有特征内容，非版本号。 |

---

## 4. 使用说明（docs/使用说明.txt）修改对照表（原句 → 新句）

编码：UTF-8 带 BOM (`utf-8-sig`)，全文不超 2 页。严格按照 REVIEW-07 的 8 项要求逐条修改定稿：

| 条目 | REVIEW-07 要求 | 原句 (初稿) | 新句 (定稿) |
|:---:|---|---|---|
| **1** | 安装包文件名与最终提交一致 | `双击运行安装包：PAW_Personal_Work_OS_Setup_v2.3.8.exe。` | `双击运行安装包：PAW_Personal_Work_OS_Setup_v2.3.9.exe。` |
| **2** | 删掉“开源” | `本软件是一款完全开源透明、运行在您电脑本机的 AI 个人办公助手。` | `本软件是一款完全运行在您电脑本机的 AI 个人办公助手。` |
| **3** | 如实说明联网搜索触发条件与隐私边界；去掉“经由加密安全通道” | `没有任何数据上传通道，您的资料绝不出您的电脑。所有模型调用均经由加密安全通道直连大模型服务商，不会经过第三方服务器。` | `PAW 遵循本地优先原则，所有文档解析、数据库存储和审核流程均在您的电脑本地运行，没有自己的中心服务器。数据与外部交互仅有两种明确情形：1. 配置大模型 API Key 后：向 AI 助手提问或解析提炼时，必要的上下文片段会发送给您所选择的模型服务商；2. AI 助手免 Key 联网搜索：在向 AI 助手提问时，若问题涉及外部时效信息（如包含“最新”、“官网”、“政策”、“新闻”等关键词）或本地知识库中缺乏相关资料，系统会自动向必应（Bing）或 DuckDuckGo 发起网页检索获取参考资料。此时仅发送您输入的提问词，不会将您的本地文档或工作台数据库内容发送出去；如不需要联网搜索，可在提问中加上“只看工作台”或“不要联网”。` |
| **4** | Key“只用于调用你选择的模型服务商” | `您的 API Key 在本地加密保存，不会上传给任何第三方，请放心使用。` | `您的 API Key 在本地使用 Windows DPAPI 加密存储，磁盘与日志中不存储明文，只用于调用你选择的模型服务商。` |
| **5** | 删掉“在控制台提示” | `并在控制台提示端口占用已切换` | *(整句删掉，平实说明“自动顺延切换至 8801、8802 等可用端口，浏览器也会自动打开正确的新端口”)* |
| **6** | 补齐界面词原文出处，核实添加文件夹位置 | 缺失部分界面词出处 | 完整核对并标注原文：<br>•【选择 AI 模型服务】（出处：`frontend/dist/paw_console.js`、`backend/main.py`）<br>•【API Key (DPAPI 加密存储)】（出处：`frontend/dist/paw_console.js`）<br>•【监控工作目录 (支持添加多个文件夹)】（出处：`frontend/dist/paw_console.js`）<br>• 确认【添加监控文件夹】位于控制中心【系统与模型】标签页。 |
| **7** | 体验路线第一步改为先填 Key，提示点全量扫描与等待时间 | 初稿第一步为“选择监控文件夹” | 第一步改为【填写模型 API Key】；第三步明确写出点击【开始全量扫描】，并提示：“后台会自动解析文档内容并提炼事实，请稍候一至两分钟，待扫描完成后审核页才会出现建议”。 |
| **8** | 去掉宣传语气（工业级、精准专业、绝无） | 包含“工业级”、“精准专业”、“绝无外部泄露”等宣传性用词 | 全部改用客观、平实的陈述，如改为“严格按照本地优先架构运行”、“清晰列出核心事实”等。 |

---

## 5. verify_consistency.py 调整 Diff

按 TASK-G-06 规则，一致性比对脚本仅允许将版本号字符串加入忽略列表做归一化替换，代码 Diff 如下：

```diff
diff --git a/scripts/verify_consistency.py b/scripts/verify_consistency.py
index b832010..ef2037e 100644
--- a/scripts/verify_consistency.py
+++ b/scripts/verify_consistency.py
@@ -1,4 +1,4 @@
-"""PAW Personal Work OS v2.3.8 - Cloud Consistency Verification Script.
+"""PAW Personal Work OS v2.3.9 - Cloud Consistency Verification Script.
 
 Compares newly built installer EXE against baseline v2.3.8 installer:
 1. Frontend hash consistency (index.html, assets/index-qIwUsXqw.js, paw_console.js)
@@ -43,7 +43,10 @@ def normalize_instruction(instr):
     argval = instr.argval
     if hasattr(argval, "co_name"):
         return (instr.opname, f"<code:{argval.co_name}>")
-    return (instr.opname, repr(argval))
+    arg_repr = repr(argval)
+    # Ignore version number string differences (TASK-G-06: 2.3.9 vs baseline 2.3.8)
+    arg_repr = arg_repr.replace("2.3.9", "2.3.8")
+    return (instr.opname, arg_repr)
 
 
 def extract_normalized_instructions(code_obj):
@@ -118,7 +121,7 @@ def compare_module_disassembly(name: str, pyc_built: Path, pyc_base: Path):
             wrapper_glue = {'loads', 'exec', 'marshal', 'globals', '_paw_marshal'}
             names_diff = (base_names - built_names) - wrapper_glue
             extra_names = built_names - base_names
-            built_consts = {c for c in code_built.co_consts if isinstance(c, str)}
+            built_consts = {c.replace("2.3.9", "2.3.8") for c in code_built.co_consts if isinstance(c, str)}
             base_consts = {c for c in code_base.co_consts if isinstance(c, str)} | {c for c in inner_run.co_consts if isinstance(c, str)}
             if not names_diff and not extra_names and built_consts == base_consts:
                 return True, "一致"
@@ -156,7 +159,7 @@ def main():
     base_dir = Path(args.baseline_dir).resolve()
 
     print("=" * 70)
-    print("PAW Personal Work OS v2.3.8 - 云端还原一致性自动化核验")
+    print("PAW Personal Work OS v2.3.9 - 云端还原一致性自动化核验")
     print(f"Built App Directory:    {built_dir}")
     print(f"Baseline App Directory: {base_dir}")
     print("=" * 70)
@@ -195,8 +198,15 @@ def main():
             all_passed = False
             detail = f"Built exists: {built_f is not None}, Baseline exists: {base_f is not None}"
         else:
-            h_built = sha256_file(built_f)
-            h_base = sha256_file(base_f)
+            if rel_path == "index.html":
+                # Normalize version query string (TASK-G-06: 2.3.9 vs baseline 2.3.8)
+                norm_built = built_f.read_bytes().replace(b"2.3.9", b"2.3.8")
+                norm_base = base_f.read_bytes()
+                h_built = hashlib.sha256(norm_built).hexdigest().upper()
+                h_base = hashlib.sha256(norm_base).hexdigest().upper()
+            else:
+                h_built = sha256_file(built_f)
+                h_base = sha256_file(base_f)
             if h_built == h_base:
                 status = "一致"
                 detail = f"SHA256: {h_built[:16]}..."
```

---

## 6. 发布触发工作流（Tag Push）配置

在 `.github/workflows/test.yml` 与 `.github/workflows/build.yml` 中均已配置：
```yaml
on:
  pull_request:
  push:
    branches:
      - main
      - 'gemini/**'
      - 'claude/**'
    tags:
      - 'v*'
```
并配置了 Windows Runner 下对最终构建的 `PAW_Personal_Work_OS_Setup_v2.3.9.exe` 自动计算 SHA256 并生成 `PAW_Personal_Work_OS_Setup_v2.3.9.exe.sha256` 文本文件一同上传。

---

## 7. 停步说明

TASK-G-06 所有要求已全部严格达成：
- 版本号 2.3.9 全面替换且保留 `repair_v238` 文件/模块名；
- 使用说明 8 条修改全部定稿并附出处表；
- 云端全量流水线 28/28 必测 PASS + 3/3 负对照预期 FAIL，获得 **`VERDICT: GREEN`**；
- 产物 SHA256 与大小已精确记录；
- 按照任务要求：“写完停步等审核”。
