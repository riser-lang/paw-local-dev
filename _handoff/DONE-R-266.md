# DONE-R-266：截图重复排查

## 结论

任务未完成，未改测试脚本、未推送、未开 PR。调查确认有两次 PAW Verification Pipeline 的 RED 尝试报告截图 SHA 重复；但 GitHub 在整条流水线重跑后替换了首次尝试的 artifact，且旧 artifact ID 已返回 404。当前仍可读取的失败摘要只保留哈希与重复次数，不含每张截图对应的用例、文件名或页面内容。因此无法按要求列出重复用例，也不能可靠选择应加页面就绪条件的运行脚本。

## 查到的流水线运行

| 分支 | 运行编号 / 尝试 | 事件 | 结果 | 截图复用问题 |
|---|---:|---|---|---|
| codex/R-265 | 39261230153 / attempt 1 | push | RED | `376177330086` 重复 2 次；`a5151f3bfdac` 重复 2 次 |
| codex/R-265 | 39261230153 / attempt 2 | 整条 push 重跑 | GREEN | 重跑摘要为 91/91 PASS，无问题 |
| codex/R-265 | 39261236463 / attempt 1 | pull_request | GREEN | 无复用告警 |
| codex/R-265 | 39265378511 / attempt 1 | push | GREEN | 无复用告警 |
| codex/R-265 | 39265387785 / attempt 1 | pull_request | GREEN | 无复用告警 |
| codex/R-261 | 39269110596 / attempt 1 | pull_request | RED | `67fbc7ee7f6d` 重复 2 次；`645331b7a358` 重复 2 次 |
| codex/R-261 | 39269110596 / attempt 2 | 整条 PR 重跑 | 进行中 | 截至本报告编写时尚未结束 |
| codex/R-261 | 39269100837 / attempt 1 | push | GREEN | 无复用告警 |
| codex/R-261 | 39228173391 / attempt 1 | push | RED | 非截图问题：PM-E3-01 以 `exception` 失败 |
| codex/R-261 | 39228178991 / attempt 1 | pull_request | GREEN | 无复用告警 |

来源：`gh run list`、`gh run view <id> --attempt 1 --log-failed` 和 `gh run view <id> --attempt 1 --log`。R-261 同一提交的 push 是 GREEN，而 PR 首次尝试因截图复用 RED；R-265 push 首次尝试 RED，整条重跑后 GREEN。这说明问题有间歇性，与测试画面就绪时序不稳定相符，但这只是根据运行结果作出的推断，不能证明具体页面或用例。

## 用例和截图文件映射状态

审核门日志只输出 `screenshot <SHA 前 12 位> reused 2 times`，不输出相关报告文件、截图文件名或 case ID。报告 JSON 和 PNG 原本在 `report-*`、`screens-*` artifact 中。

- R-265 首次尝试的 PM05 截图 artifact ID `12189933839` 在重跑后通过 Actions artifact API 查询返回 404；重跑后的 `pipeline-verdict-summary` 是 GREEN 版本，不能代表首次 RED 的截图。R-261 首次尝试的截图 artifact ID（包括 PM05 的 `12194726592`）也在重跑后返回 404。
- 因此，四个哈希尚不能映射到具体用例和截图文件。没有把猜测写成事实。

## 原因调查和候选修复

- `tests/paw_case.py` 的 `Case.screenshot()` 计算文件 SHA 并把用例内的截图记录进 report JSON；`tests/paw_verdict.py` 汇总全部报告，只报告重复 SHA 与数量，没有打印命中的用例。两者均为审核人文件，本任务未修改。
- `tests/run_e2e_tests.py` 中已有截图横幅、时间戳和随机 nonce（`git blame` 显示始于仓库初始提交 `dee1542`）。本任务没有增加或修改像素、水印或随机内容。该路径无法解释目前缺少原报告映射的情况。
- 新 PM 页面 runner 的截图记录来自 `tests/run_pm_*.py`。例如 `tests/run_pm_frontend_tests.py` 的 `stable_page()` 等待通用标题 `.pm-page h1`，而 `capture()` 本身主要同步响应式布局；调用处通常另做内容断言，但没有取得两次 RED 的映射，无法判断具体哪处等待条件不足。
- 最可能的方向是某个截图在页面已加载通用外框、但该用例自己的数据/路由状态尚未出现时被采集；push 与 PR 首次尝试的不同结果支持“时序竞争”的推断。仍需首次 RED 的 report JSON / PNG 才能确认是否如此、并为对应截图加上用例特定的可见内容等待条件。

## 下一步所需证据

请审核人或工作流维护者提供两次首次 RED 尝试的 `report-*` 和 `screens-*` artifacts（或在可保留首次产物的情况下整条重跑）。拿到报告后即可按 `case.id → screens[].file / sha256` 精确列出哈希对应的用例，再在相关 `tests/run_pm_*.py` 测试运行脚本里补正确的 UI 条件等待。不得用固定 sleep、随机图像内容或审核规则变更规避检查。

## 修改与 CI

- 本分支从最新 `origin/main` 建立为 `codex/R-266`；当前唯一工作区改动是本报告，未修改产品或测试文件。
- 没有推送、没有 PR，也没有为无改动分支启动 CI。故 push / PR 运行编号为 N/A。
- 未完成 lint/unit 验证；本次未改动可执行代码。

## R-266 后续：保留重跑产物并增加截图来源诊断

审核人合并 PR #15 后，已将 `codex/R-266` fast-forward 到最新 `origin/main`（`d472d82`），保留了上面的首次调查记录，并完成以下改动：

- `.github/workflows/test.yml` 中所有 `report-*` 与 `screens-*` artifact 名称均追加 `-attempt-${{ github.run_attempt }}`；汇总作业下载报告时也限定当前 attempt，避免把前一次重跑报告混入本次 verdict。
- 在 Reviewer Verdict Gate 后加 `if: always()` 诊断步骤。它只读取当前 attempt 下载的报告 JSON，按完整 SHA 分组，并在已有 `summary.md` 中追加前 12 位哈希、用例 ID、截图文件名和 block；判定脚本和 verdict 不受诊断影响。
- 用临时虚构报告验证过诊断会列出两张同哈希截图的来源；YAML 可解析，`git diff --check` 通过。

## 现有产物中的潜在同画面用例（非已证实根因）

从 R-265 push 重跑的第二次产物（运行 39261230153，attempt 2）可见多个 case 会在相同 URL 和视口截图，因此属于值得在未来重复哈希诊断中优先检查的候选，但该次产物中的 SHA 均不同：

- PM-E3-01 与 PM-E4-01 都截图 `/app/pm/today`，分别在 1440×1000 与 390×900 视口展示健康检查和知识缺口区域。
- PM-F1-01 与 PM-F2-01 都截图 `/app/pm/today`，分别展示周报/跨项目对比与进化指标。
- PM-A1-01、PM-A2-01、PM-A4-01 都截图 `/app/pm/today`，有桌面与手机视口。

这些用例预期数据不同，但若截图恰好在异步内容更新前完成，可能暂时只拍到相同的公共页面外框。此时应以新增诊断列出的实际 case/file/block 为准，再补该用例自身可见内容的条件等待；本次不修改测试运行脚本，也不将此推断当作既有 RED 的根因。

## 本轮验证

- `python tests/paw_lint.py`：`lint clean`。
- `python -m pytest tests/unit -q`：`207 passed in 65.86s`。
- YAML 解析：发现 `build`、`real-model-smoke`、`test-blocks`、`negative-control`、`summary` 五个 jobs。
- 诊断步骤样例验证：同哈希映射到 `CASE-A / one.png / PMXX` 与 `CASE-B / two.png / PMXX`，summary 正确列出两个来源；这只是临时虚构输入，不是仓库验收用例。
- push 和 PR 云端流水线尚待本轮推送后运行。

## 云端验证（PR #17）

- push 运行 `39277040971` attempt 1：RED。Verdict summary 为 91 项必需用例中 PASS 90、FAIL 1、NOT RUN 0；`PM-U4-01` 因异常失败（PM12 报告指向模型请求 ReadTimeout）。摘要的重复截图诊断显示：当前可用报告里没有重复截图哈希。
- 已对 `39277040971` 发起整条流水线重跑，GitHub API 确认 attempt 2 已创建；截至本报告更新时，attempt 2 的 21 个 job 全部 queued，未分配 runner，无法获得 GREEN/RED 结论。仓库同期其他分支的 Actions jobs 也处于 queued。未单独重跑任何 job。
- PR 运行 `39277214124`：GREEN。Verdict summary 为 91/91 必需用例 PASS、0 FAIL、0 NOT RUN；诊断步骤显示当前可用报告里没有重复截图哈希。`NC-PM-S1` 等负对照按规则显示预期失败，不计入 91 个必需用例。
- PR summary artifact `pipeline-verdict-summary` 中，Duplicate screenshot sources 一节为 “No duplicate screenshot hashes were found in the available report files.”；push attempt 1 的同一诊断也没有找到重复哈希。
- 因 push 的整条重跑仍排队，本轮 push+PR 不能记为双 GREEN。PR #17 保持打开，未合并、未打 tag。

执行者进度：PR #17 的 PR 检查 GREEN（运行 39277214124）；push 首轮 RED（运行 39277040971 attempt 1），整条 attempt 2 仍 queued；PR 检查满足要求，push GREEN 尚未取得。


## R-266 第二次修复：扫描竞争、异常清理与页面就绪条件

依据 latest `origin/main` (`6d08f1ea`) rebase 后，检查了最新提交 `fcbf2cd` 的失败运行产物：push `39284660576` 的 `report-PM06-attempt-1` 和 PR `39284669933` 的 `report-PM07-attempt-1`。

### 失败原文与执行步骤

- `PM-C1-02`（push，PM06）：异常末行为 `httpx.ReadTimeout: timed out`。报告中的异常字段为 2,000 个字符；`tests/paw_case.py` 只保存 traceback 末尾 2,000 个字符，因此报告本身没有完整的上游 traceback 头部。完整的报告字段原文如下：

```text
t_env=trust_env,
    )
  File "C:\hostedtoolcache\windows\Python\3.13.16\x64\Lib\site-packages\httpx\_api.py", line 109, in request
    return client.request(
           ~~~~~~~~~~~~~~^
        method=method,
        ^^^^^^^^^^^^^^
    ...<8 lines>...
        follow_redirects=follow_redirects,
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "C:\hostedtoolcache\windows\Python\3.13.16\x64\Lib\site-packages\httpx\_client.py", line 825, in request
    return self.send(request, auth=auth, follow_redirects=follow_redirects)
           ~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\hostedtoolcache\windows\Python\3.13.16\x64\Lib\site-packages\httpx\_client.py", line 914, in send
    response = self._send_handling_auth(
        request,
    ...<2 lines>...
        history=[],
    )
  File "C:\hostedtoolcache\windows\Python\3.13.16\x64\Lib\site-packages\httpx\_client.py", line 942, in _send_handling_auth
    response = self._send_handling_redirects(
        request,
        follow_redirects=follow_redirects,
        history=history,
    )
  File "C:\hostedtoolcache\windows\Python\3.13.16\x64\Lib\site-packages\httpx\_client.py", line 979, in _send_handling_redirects
    response = self._send_single_request(request)
  File "C:\hostedtoolcache\windows\Python\3.13.16\x64\Lib\site-packages\httpx\_client.py", line 1014, in _send_single_request
    response = transport.handle_request(request)
  File "C:\hostedtoolcache\windows\Python\3.13.16\x64\Lib\site-packages\httpx\_transports\default.py", line 249, in handle_request
    with map_httpcore_exceptions():
         ~~~~~~~~~~~~~~~~~~~~~~~^^
  File "C:\hostedtoolcache\windows\Python\3.13.16\x64\Lib\contextlib.py", line 162, in __exit__
    self.gen.throw(value)
    ~~~~~~~~~~~~~~^^^^^^^
  File "C:\hostedtoolcache\windows\Python\3.13.16\x64\Lib\site-packages\httpx\_transports\default.py", line 118, in map_httpcore_exceptions
    raise mapped_exc(message) from exc
httpx.ReadTimeout: timed out

```

  代码路径是 `run_c06` → `make_conflict` → `seed` → `import_doc` → `poll_candidates_from_scanner`。等待目标是 `/v1/local/scanner/progress` 返回 `completed` 且 `pipeline_complete` 成立，然后再读取候选。对应日志显示第四次扫描 `未定资料.docx` 在 16:29:19 UTC 仍在轮询 progress；下一个读取调用超时，扫描轮询函数没有捕获短暂的 progress-read timeout。该端点单次读取上限为 2 秒，原有扫描总截止为 40 秒。
- `PM-A1-01`（PR，PM07）：完整异常原文：

```text
Traceback (most recent call last):
  File "D:\a\paw-local-dev\paw-local-dev\tests\run_pm_operations_tests.py", line 63, in run_c07
    pid=project();import_doc(case,'输入资料.docx','冲刺项目。\n进度与里程碑：原型已交付。')
                  ~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\a\paw-local-dev\paw-local-dev\tests\run_pm_versions_tests.py", line 30, in import_doc
    candidates=poll_candidates_from_scanner()
  File "D:\a\paw-local-dev\paw-local-dev\tests\run_e2e_tests.py", line 318, in poll_candidates_from_scanner
    return wait_for_scan(candidates, progress, timeout_sec)
  File "D:\a\paw-local-dev\paw-local-dev\tests\scanner_wait.py", line 16, in wait_for_scan
    raise TimeoutError(f'Scanner did not reach completed within {timeout_sec}s (last state={state})')
TimeoutError: Scanner did not reach completed within 40s (last state=phase2_analyzing)

```

  `run_c07` 在导入首份 `输入资料.docx` 时经 `import_doc` 等扫描完成；`scanner_wait.wait_for_scan` 等待 scanner `completed` 与 pipeline completion barrier。40 秒到期时最后状态仍为 `phase2_analyzing`。对应日志中 scanner 从 16:30:47 UTC 开始，progress API 每秒正常返回，但截至 16:31:27 UTC 仍未到达完成状态。脚本原本在 `running()` 里固定等待 3 秒；`start_paw()` 在服务可响应约 3 秒后即返回，因而测试可能在 scheduler 的 startup cruise 仍运行时创建项目和开始扫描。两个后台任务可能争用模型/运行时资源，这是代码证据支持的竞态解释。

### 改动与超时说明

- `running()` 现在等待 `/v1/local/runtime/status` 中 `scheduler.total_cruises >= 1` 且 `scheduler.status == idle` 后才创建样例。30 秒仅作为 startup readiness 的失败保护；没有放宽扫描总超时。
- scanner progress 的单次 HTTP timeout 仍是 2 秒、扫描总截止仍是 40 秒。短暂 `httpx.TimeoutException` 记录日志后继续轮询，并保留最近一次状态；不会把 `phase2_analyzing` 当作成功。
- PM06 的 `run_c06` 与 PM07 的 `run_c07` 保留直接的 `with report.case(...)` 判定块，并在块内用 `clean_environment_on_error()` 包住用例主体；任何异常会在 finally 中调用现有 `clean_environment()` 后再交给原 report case 记录，所以后续用例开始前先清理残留。
- 验收脚本中的 `page.goto(..., wait_until="networkidle")` 改成 `domcontentloaded` 后等待目标 API response、页面 heading、可见列表/表单或非空渲染内容。固定 Playwright 页面等待也替换为 DOM 条件等待。
- page navigation timeout 设为 30 秒，与 Playwright 原默认值相同；没有延长 scanner timeout。

### 本地检查和云端状态

- `python tests/paw_lint.py`：`lint clean`。
- `python -m pytest tests/unit -q`：`219 passed in 51.79s`。
- `compileall` 对涉及的测试模块通过；`git diff --check` 通过；在测试目录中没有剩余 `networkidle` 或 `wait_for_timeout` 调用。
- 分支已 rebase 到 `6d08f1ea`，无冲突；更新远端时使用精确 lease `--force-with-lease=refs/heads/codex/R-266:fcbf2cd21ecfa15e661e939025fbe4fced9b2e66`。
- push 首次运行 `39357377944`：RED，91 项中 PASS 90、FAIL 1、NOT RUN 0；`PM-C1-02`、`PM-A1-01`、`PM-A2-01`、`PM-A4-01`、`PM-UP-01` 均 PASS。
- PR 首次运行 `39357382728`：RED，91 项中 PASS 89、FAIL 2、NOT RUN 0；以上五个扫描/清理修复用例也均 PASS。
- 两条流水线只运行 attempt 1；未重跑。PR #17 保持打开，未合并、未打 tag。

## 首次 CI 的剩余失败（按要求停下）

- push `39357377944` 与 PR `39357382728` 都失败于 `B5-03`，failed check 为 `direct_project_url_contains_project_name`。报告异常原文：

```text
Traceback (most recent call last):
  File "D:\\a\\paw-local-dev\\paw-local-dev\\tests\\run_e2e_tests.py", line 964, in run_b5
    c.check("direct_project_url_contains_project_name", "������֪��������" in body_text, True)
    ~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\\a\\paw-local-dev\\paw-local-dev\\tests\\paw_case.py", line 73, in check
    raise CheckFailed(name)
tests.paw_case.CheckFailed: direct_project_url_contains_project_name
```

  根因证据：`inspect_and_capture_page()` 现在等待 `#root` 可见且根节点文字长度超过 20；这没有等待 B5-03 要证明的项目名称。测试随后读取 `body_text` 并检查名称，页面内容尚未到位时该 check 失败。下一步若继续，应为 B5-03 等待精确项目名称或相应项目接口响应；本轮按“首轮仍失败就写明并停下”不再改动。
- PR `39357382728` 另有 `PM-U3-01`，failed check 为 `no_internal_terms_visible`，报告异常原文：

```text
Traceback (most recent call last):
  File "D:\\a\\paw-local-dev\\paw-local-dev\\tests\\run_pm_c12_tests.py", line 406, in run_c12
    c.check("no_internal_terms_visible", no_internal_terms_visible(case), True)
    ~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\\a\\paw-local-dev\\paw-local-dev\\tests\\paw_case.py", line 73, in check
    raise CheckFailed(name)
tests.paw_case.CheckFailed: no_internal_terms_visible
```

  这是 check 失败而非捕获到的异常；报告没有保存是哪一页的匹配文本。该 case 使用 `page_text()` 在 body 文本长度超过 20 后立即采样，当前证据不足以判定是否过早采样。没有放宽检查，也没有重跑。
- push summary：`PASS 90 | FAIL 1 | NOT RUN 0`，唯一必需用例失败为 `B5-03`。
- PR summary：`PASS 89 | FAIL 2 | NOT RUN 0`，失败用例为 `B5-03`、`PM-U3-01`。

执行者进度：PR #17，push 首次运行 39357377944 RED，PR 首次运行 39357382728 RED；PM-C1-02 与 PM-A1-01 修复项通过，因 B5-03 和 PM-U3-01 失败已按要求停下，未重跑。

## R-266 后续修订（基于 main 3fb84fb）

- 将本地 `DONE-R-266.md` 修改保留后，分支 rebase 到 `origin/main` 的 `3fb84fb`；无冲突。远端 `codex/R-266` rebase 前 SHA 为 `7da3d813cf1ec70387cf102873850fd58f0f903b`，推送时使用对应的精确 `--force-with-lease`。
- B5-03 现在等待页面文字中精确出现“智能认知治理工程”后再读取页面；B1-02、B2-01、B5-02 所有用于判定的页面文字也各自等待该用例断言内容（含备选标题）。其他只截图、没有读取页面文字作判定的用例不增加无关等待。
- PM-U3-01 在采样前分别等待审核卡片、待办标题、项目档案 API 200 响应及项目主标题；助手回答使用请求完成的回答正文。命中内部词时保留每页 URL、命中词及其前后最多 40 个字符，并将诊断序列化进失败用例的异常报告；检查规则与词表不变。
- 本地检查：`python tests/paw_lint.py` 为 `lint clean`；`python -m pytest tests/unit -q` 为 `227 passed in 72.49s`；`git diff --check` 通过。
- 云端 push/PR 首次运行编号及结果待补；按本轮指示，任何一侧首轮 RED 都将记录失败用例和原文后停止，不重跑。

## R-266 后续修订的首次 CI 结果

- push run `39363340764`（attempt 1）：verdict RED。91 个必需用例为 PASS 91、FAIL 0、NOT RUN 0；Reviewer Verdict Gate 仍因重复截图判 RED。PM-U3-01 PASS，`no_internal_terms_visible` 为 true，没有命中词或页面诊断记录。
- push 的截图诊断：PM-E1-02 `screen_PM_E1_02_390.png` 与 PM-E1-03 `screen_PM_E1_03_390.png` 的 SHA256 完全相同，均为 `45d8b72bb53ed909a40946c80132d4daa212087263b294db59a535271ebae06f`。两张截图来自不同项目 URL；本轮按首次 RED 后停止规则没有继续改脚本或追查原因。报告摘要中的问题原文为：`screenshot 45d8b72bb53e reused 2 times`，Duplicate screenshot sources 列出上述两个用例、文件及 block PM08。
- PR run `39363345481`（attempt 1）：verdict RED，必需用例 90 PASS、1 FAIL、0 NOT RUN。B5-03 PASS，PM-U3-01 PASS 且无内部词命中；唯一必需失败为 PM-A4-01，failed_check 为 `exception`。该用例此前的业务检查均通过，随后调用 Playwright `page.screenshot` 时浏览器页面已关闭。报告保留的异常原文（traceback 按审核报告字段限制保留末尾 2000 字符）为：

```text
error(error, f"{parsed_st['apiName']}: {error}") from None
playwright._impl._errors.TargetClosedError: Page.screenshot: Target page, context or browser has been closed
Browser logs:

<launching> C:\Users\runneradmin\AppData\Local\ms-playwright\chromium-1248\chrome-win64\chrome.exe --disable-field-trial-config --disable-background-networking --disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-back-forward-cache --disable-breakpad --disable-client-side-phishing-detection --disable-component-extensions-with-background-pages --disable-component-update --no-default-browser-check --no-default-apps --disable-dev-shm-usage --disable-edgeupdater --disable-extensions --disable-features=AvoidUnnecessaryBeforeUnloadCheckSync,DestroyProfileOnClose,DialMediaRouteProvider,GlobalMediaControls,HttpsUpgrades,LensOverlay,MediaRouter,PaintHolding,ThirdPartyStoragePartitioning,BlockOriginHeaderModificationOnRedirect,AvoidCorsURLLoaderRestartOnRedirect,Translate,AutoDeElevate,OptimizationHints,NetworkTimeServiceQuerying,AimEnabled,msForceBrowserSignIn,msEdgeUpdateLaunchServicesPreferredVersion --enable-features=CDPScreenshotNewSurface --allow-pre-commit-input --disable-hang-monitor --disable-ipc-flooding-protection --disable-popup-blocking --disable-prompt-on-repost --disable-renderer-backgrounding --disable-updater-scheduler --force-color-profile=srgb --metrics-recording-only --no-first-run --password-store=basic --use-mock-keychain --no-service-autorun --export-tagged-pdf --disable-search-engine-choice-screen --unsafely-disable-devtools-self-xss-warnings --edge-skip-compat-layer-relaunch --disable-infobars --disable-search-engine-choice-screen --disable-sync --enable-unsafe-swiftshader --no-sandbox --user-data-dir=C:\Users\RUNNER~1\AppData\Local\Temp\playwright_chromiumdev_profile-UTpRtu --remote-debugging-pipe --no-startup-window
<launched> pid=6424
Call log:
  - taking page screenshot
  - waiting for fonts to load...
  - fonts loaded
```

- push 的所有必需用例均通过，PR 仅 PM-A4-01 失败；两边的 PM-U3-01 与 B5-03 均通过。CI 两次均为 attempt 1；未重跑。PR #17 仍 OPEN，未合并、未打 tag。因首轮 RED，后续不再修改或推送；本段运行结果更新保留在本地工作区。

执行者进度：PR #17，push 39363340764 RED（必需用例 91/91，截图重复），PR 39363345481 RED（PM-A4-01 TargetClosedError）；B5-03 与 PM-U3-01 通过且 PM-U3-01 无命中，报告 _handoff/DONE-R-266.md；停下原因：首轮 CI 未双绿，未重跑。

## R-266 再次修订（基于 main d6e059a）

- 保留此前本地未推送的 DONE 更新后，rebase 到 `origin/main` `d6e059a37899d7659eb48bd2e5afda8ac61e4bb6`，无冲突。rebase 前远端 `codex/R-266` SHA 为 `e9fa81fc78d2b327aac74040ecb30aa14e66bcd1`；推送将仅使用该值作为精确 `--force-with-lease`。
- 上次 push `39363340764` 的重复截图诊断原样列出两个不同用例：`PM-E1-02` `screen_PM_E1_02_390.png` 和 `PM-E1-03` `screen_PM_E1_03_390.png`，均属 PM08。SHA256 为 `45d8b72bb53ed909a40946c80132d4daa212087263b294db59a535271ebae06f`。修复后，PM-E1-02 在打开自己的来源依据对话框后再截图；PM-E1-03 通过页面按钮触发拒绝生成，检查并截图界面显示的拒绝消息与保留建议。
- 上次 PR `39363345481` 的 PM-A4-01 报告中，已有的状态/时间及页面检查均通过，失败步骤为首次桌面视图 `capture(c,page,1440)` 内的 Playwright `page.screenshot`；报告 `screens` 为空，截图尚未登记。报告保留的异常原文末尾为：

```text
error(error, f"{parsed_st['apiName']}: {error}") from None
playwright._impl._errors.TargetClosedError: Page.screenshot: Target page, context or browser has been closed
Call log:
  - taking page screenshot
  - waiting for fonts to load...
  - fonts loaded
```

  用例的 `finally: browser.close()` 位于 capture 之后，没有先行关闭页面的语句。现已在桌面 capture 前重新导航并等待 `/v1/pm/view/today` 的成功响应、可见的“本地维护”标题及该区域的状态/时间文字；调整到手机宽度后也等待同一组页面内容和渲染帧再截图。没有延长超时。
- 本地检查：`python tests/paw_lint.py` 为 `lint clean`；`python -m pytest tests/unit -q` 为 `227 passed in 74.35s`；两个改动脚本 `py_compile` 通过；`git diff --check` 通过。
- 本次 push/PR 首次运行编号与结果待补。任何首次 RED 都将追加失败用例、原文和诊断用例名后停止，不重跑。
