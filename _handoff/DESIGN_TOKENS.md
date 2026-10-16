# PAW 本地前端设计变量

2026-10-15，C-01，只读提取现有产物，没有修改压缩 JS/CSS。

出处 A：`frontend/dist/assets/index-DZlw_Tae.css:1`。文件是单行编译 CSS，存在两组变量；下表取后定义的生效值，避免误用早期绿色主题。
出处 B：`frontend/dist/paw_console.js:4-220` 的 `STYLES`。

| 变量 | 值 | 出处/用途 |
|---|---|---|
| --paw-bg-canvas | #F7F3EB | A，纸张底色 |
| --paw-bg-sidebar | #F1ECE4 | A，侧栏 |
| --paw-bg-surface | #FCFAF6 | A；B #paw-modal-box |
| --paw-bg-surface-subtle | #FAF7F1 | A；B .paw-modal-header |
| --paw-bg-surface-sunken | #ECE5DC | A，凹入区域 |
| --paw-text-primary | #302B26 | A；B .paw-modal-body |
| --paw-text-secondary | #756E65 | A；B .paw-tab-btn |
| --paw-text-tertiary / --paw-text-placeholder | #A1998E | A，次要信息 |
| --paw-text-inverse | #FCFAF6 | A；B .paw-btn-primary |
| --paw-text-link | #A86348 | A，链接 |
| --paw-accent-base | #B66F51 | A；B .paw-btn-primary |
| --paw-accent-hover | #A86348 | A；B .paw-btn-primary:hover |
| --paw-accent-subtle | #F3E2D9 | A，选中背景 |
| --paw-accent-border | #DCC0B0 | A，强调边框 |
| --paw-border-subtle | #ECE5DC | A（最终值）；B 使用 #E8E2D8 |
| --paw-border-strong | #E3DCD2 | A（最终值）；B 表单 #DED8CC |
| --paw-border-hairline | rgba(48,43,38,.08) | A，细分隔线 |
| --paw-border-focus | #B66F51 | A；B input:focus |
| --paw-success-text / --paw-success-bg / --paw-success-border | #648166 / #eef4f0 / #bcd1c4 | A，成功 |
| --paw-warning-text / --paw-warning-bg / --paw-warning-border | #A47A42 / #faf3e7 / #e8d3b0 | A，风险 |
| --paw-danger-text / --paw-danger-bg / --paw-danger-border | #A65F58 / #fbefef / #e6bcba | A，失败 |
| --paw-info-text / --paw-info-bg / --paw-info-border | #63766F / #E2E9E5 / #CEDAD3 | A，信息 |
| --paw-pending-text / --paw-pending-bg / --paw-pending-border | #8A7659 / #ECE4D8 / #DED2C0 | A，待决 |
| --paw-font-sans | -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei","WenQuanYi Micro Hei",sans-serif | A，最终正文栈；B 同类系统字体 |
| --paw-font-serif | "Source Serif Pro","Georgia","Times New Roman",serif | A，早期变量仍存在 |
| 标题的生效 font-family | "Noto Serif SC","Source Han Serif SC",Songti SC,STSong,Georgia,serif | A，.project-card-head strong / .review-detail h2 |
| --paw-font-mono | ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace | A，最终等宽栈 |
| --paw-text-pageTitle / -lh / -w | 22px / 1.28 / 550 | A，标题 |
| --paw-text-section / -lh / -w | 15px / 1.32 / 560 | A，栏目标题 |
| --paw-text-body / -lh / -w | 13.5px / 1.58 / 400 | A，正文；B 正文 13.5px/1.6 |
| --paw-text-metadata / -lh | 12px / 1.4 | A，元数据 |
| --paw-radius-sm / -md / -lg / -xl | 4px / 6px / 8px / 10px | A；B 按钮 6px、面板 8px |
| --paw-sp-1 至 --paw-sp-14 | 4px 至 56px，每档 4px | A，间距 |
| --paw-sp-16 / -20 | 64px / 80px | A，大留白 |
| 边框宽度 | 1px solid | A 各卡片；B 表单/面板 |
| --paw-shadow-card | 0 1px 3px rgba(35,30,25,.04),0 1px 1px rgba(35,30,25,.02) | A，最终值；优先不用阴影 |
| --paw-maxw-prose / --paw-maxw-page | 760px / 1280px | A，正文/页面 |
| --paw-dur-1 / -2 / -3 | .12s / .16s / .2s | A，动效 |
| --paw-ease | cubic-bezier(.2,.8,.2,1) | A；B 同曲线 |

复核命令：`rg -o -- '--paw-[a-zA-Z0-9-]+:[^;}]*' frontend/dist/assets/index-DZlw_Tae.css`；关键输出包含两次 `--paw-border-subtle`，最后为 `#ECE5DC`。`Get-Content frontend/dist/paw_console.js -TotalCount 220` 核对控制中心实际内联样式。

新包在 pm-src 中引用相同变量，独立页面携带一份明确的兼容变量定义。标题本地打包开源中文衬线字体；现有栈仅证明字体名称，不能声称仓库已带字体文件。rough.js 与字体在 C-06 引入并记录版本/许可证；C-01 未引入第三方前端资源。新页面不继承旧页面中的毛玻璃/渐变；状态要同时有文字。390px 时切竖向时间线，按需求的 640px 断点实现。

## C-06 交付设计的本地映射（2026-10-16）

实际源码为 frontend/pm-src/design/styles/pm.css 与 fonts.css，来源是只读 design-export。PM 变量优先使用对应 --paw-*，独立页面使用同色兜底：纸张 #F7F3EB、卡片 #FCFAF6、正文 #302B26、次要字 #756E65、赤陶 #B66F51、细边框 #ECE5DC。AI 建议另用苔绿底 #EDF0E7，并保留建议标签；状态同时使用符号和文字。

| 项目 | 实际采用 |
|---|---|
| 正文 | --pm-font-sans，继承既有无衬线栈；13.5px/1.55，手机14px |
| 标题 | PAW Noto Serif SC 600，本地101个子集；桌面页标题28–38px，手机27px |
| 图上批注 | PAW LXGW WenKai 400，本地97个子集；正文不使用手写字体 |
| 字体体积 | 两个实际引用字重共7,853,936字节，字体二进制保持原样 |
| 圆角 | 4/6/8/10px，卡片细边框与轻阴影沿用设计 |
| 间距 | 4/8/12/16/24/32/48px |
| 断点 | <=640px 切竖向时间线，栏目单列，需要决定排在内容区首位 |
| 绘图 | rough.js4.6.6，随机种子来自稳定数据ID；今天取后端北京时间日期 |
| 本地适配 | 长审批标签、正文及辅助标题允许换行；手机页面不靠隐藏横向溢出来掩盖裁切 |

构建后字体和许可证随 frontend/dist/pm/ 一起安装，不使用字体CDN。第三方名称、版本及许可证原文见 frontend/pm-src/THIRD_PARTY.md 与 licenses/。截图对照来自真实旧AI助手和新驾驶舱，保留原始PNG并生成并排对照HTML。
