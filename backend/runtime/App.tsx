import { FormEvent, ReactElement, ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { Sun, FolderKanban, BookOpen, Files, ListTodo, CalendarDays, ClipboardCheck, Compass, MoreHorizontal, X, ChevronRight, ChevronLeft, Plus, ArrowUp, type LucideIcon } from "lucide-react";
import { HeaderIllustrationToday, HeaderIllustrationProjects, HeaderIllustrationKnowledge, HeaderIllustrationSources, HeaderIllustrationTodo, HeaderIllustrationCalendar, HeaderIllustrationReview, HeaderIllustrationAgy, type HeaderIllustrationKey } from "./header-illustrations";
import { ApiError, client } from "./api/client";
import type { Activity, Capture, Daily, IntegrationStatus, Knowledge, LunaDraft, Project, ProjectExplainer, RecordMap, ReviewCandidate, ReviewDetail, Source, Todo } from "./types";
import { captureDisplayBadge } from "./types";
import { buildKnowledgePresentation, businessSummaryFrom, summaryFromContent } from "./knowledge-presentation";

type Route = { path: string; query: URLSearchParams };
const navItems = [
  ["今天", "/today", Sun],
  ["项目", "/projects", FolderKanban],
  ["知识", "/knowledge", BookOpen],
  ["资料", "/files", Files],
  ["待办", "/todos", ListTodo],
  ["日历", "/daily", CalendarDays],
  ["待我审核", "/review", ClipboardCheck],
] as const;
function NavIcon({ icon: Icon, size = 17 }: { icon: LucideIcon; size?: number }) { return <Icon size={size} strokeWidth={1.75} aria-hidden="true" />; }
function PawLogo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" fill="none" role="img" aria-label="PAW">
      <rect x="5" y="5" width="38" height="38" rx="9" stroke="currentColor" strokeWidth="3" />
      <rect x="16" y="16" width="16" height="16" rx="4.5" stroke="currentColor" strokeWidth="2.5" opacity="0.72" />
      <rect x="23.5" y="23.5" width="5" height="5" rx="1.6" fill="currentColor" />
    </svg>
  );
}

function normalizePath(pathname: string) {
  const path = pathname.replace(/^\/app(?=\/|$)/, "") || "/";
  return path.length > 1 ? path.replace(/\/$/, "") : path;
}
function readRoute(): Route { return { path: normalizePath(window.location.pathname), query: new URLSearchParams(window.location.search) }; }
function useRoute() {
  const [route, setRoute] = useState(readRoute);
  const navigate = useCallback((to: string) => { const [path, search] = to.split("?"); const mountedPath = path === "/" ? "/app" : `/app${path.startsWith("/") ? path : `/${path}`}`; window.history.pushState({}, "", `${mountedPath}${search ? `?${search}` : ""}`); setRoute(readRoute()); window.scrollTo({ top: 0, behavior: "smooth" }); }, []);
  useEffect(() => { const handler = () => setRoute(readRoute()); window.addEventListener("popstate", handler); return () => window.removeEventListener("popstate", handler); }, []);
  return { ...route, navigate };
}

function useResource<T>(loader: () => Promise<T>, deps: readonly unknown[]) {
  const [state, setState] = useState<{data?: T; loading: boolean; error?: string}>({ loading: true });
  const [reload, setReload] = useState(0);
  const retry = useCallback(() => setReload(value => value + 1), []);
  useEffect(() => { let live = true; setState({ loading: true }); loader().then(data => live && setState({ data, loading: false })).catch(error => live && setState({ loading: false, error: error instanceof Error ? error.message : "加载失败，请稍后重试。" })); return () => { live = false; }; // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, reload]);
  return { ...state, retry };
}

function dateText(value?: string | null, includeTime = false) {
  if (!value) return "未记录";
  const date = new Date(value); if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", includeTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" }).format(date);
}
function humanStatus(value?: string | null) { return ({ active: "进行中", in_progress: "进行中", planning: "计划中", paused: "暂停", archived: "已归档", done: "已完成", completed: "已完成", open: "未完成", in_progress_todo: "进行中", cancelled: "已取消", draft: "草稿", current: "有效", needs_review: "待复核", deprecated: "已过期", working: "处理中", captured: "已记录" } as Record<string, string>)[value ?? ""] ?? (value ? "状态待确认" : "未标注"); }
function humanType(value?: string | null) { return ({ todo: "待办调整", knowledge: "知识调整", project_update: "项目更新", ambiguity: "需要补充信息", conflict: "冲突需要解决", question: "需要回答问题", fact: "事实确认" } as Record<string, string>)[value ?? ""] ?? "待确认"; }
function humanReviewTitle(value: unknown, candidateType?: string | null) {
  const title = String(value ?? "").trim();
  return title && /[\u3400-\u9fff]/.test(title) && !/[A-Za-z]{3,}/.test(title)
    ? title
    : `${humanType(candidateType)}待确认事项`;
}
function humanReviewState(value?: string | null) { return ({ NEEDS_REVIEW: "待确认", RESOLVED: "方向已确认", ACCEPTED_WAITING_COMMIT: "已接受·待写入", REVIEWED_WAITING_GPT: "已审核·待最终复核", APPLIED_OR_COMMITTED: "已写入工作台", COMMITTED: "已写入工作台", REJECTED: "已拒绝" } as Record<string, string>)[value ?? ""] ?? "待确认"; }
function humanDraftStatus(value?: string | null) { return ({ LUNA_UNREVIEWED: "待确认", GPT_ACCEPTED: "已接受", GPT_REVISED: "已修订", GPT_REJECTED: "已拒绝", OPUS_PENDING: "待复核", USER_DECISION_REQUIRED: "待你决策", CANONICALIZED: "已正式沉淀", NO_SEMANTIC: "暂无可读内容", PROJECTION_ONLY: "仅作背景参考" } as Record<string, string>)[value ?? ""] ?? "待确认"; }
function draftStatusTone(value?: string | null) { return value === "GPT_ACCEPTED" || value === "GPT_REVISED" || value === "CANONICALIZED" ? "success" : value === "GPT_REJECTED" ? "danger" : value === "OPUS_PENDING" || value === "USER_DECISION_REQUIRED" ? "warning" : "neutral"; }
function humanMode(value?: string | null) { return value === "DECISION_ONLY" ? "方向确认" : value === "EXECUTION_READY" ? "可执行变更" : "审核建议"; }
function mappedLabel(value: string | null | undefined, labels: Record<string, string>, fallback: string) {
  if (!value) return fallback;
  if (labels[value]) return labels[value];
  return /[\u3400-\u9fff]/.test(value) && !/^[A-Z][A-Z0-9_:-]*$/.test(value) ? value : fallback;
}
function humanPriority(value?: string | null) { return mappedLabel(value, { low: "低", normal: "普通", medium: "中", high: "高", urgent: "紧急" }, "已设置"); }
function humanStage(value?: string | null) { return mappedLabel(value, { discovery: "探索", planning: "规划", execution: "执行", review: "复盘", delivery: "交付", maintenance: "维护" }, "阶段已记录"); }
function humanKnowledgeType(value?: string | null) { return mappedLabel(value, { project: "项目知识", domain: "领域知识", rule: "工作规则", method: "方法沉淀", analysis: "分析结论" }, "知识条目"); }
function humanSourceCategory(value?: string | null) { return mappedLabel(value, { document: "文档", doc: "文档", spreadsheet: "表格", sheet: "表格", presentation: "演示文稿", pdf: "PDF 资料", image: "图像资料", note: "工作笔记" }, "资料"); }
function humanRelation(value?: string | null) { return mappedLabel(value, { primary: "主要关联", related: "相关联" }, "关联信息"); }
function humanSource(value?: string | null) { return mappedLabel(value, { web: "网页记录", chatgpt_action: "工作台记录", manual: "手动记录", office_sync: "办公资料", agnes: "Agy 对话" }, "工作记录"); }
function humanProvider(value?: string | null) { return mappedLabel(value, { workbuddy: "WorkBuddy", wps_mcp: "WPS 只读适配器", unknown: "未识别适配器" }, "资料读取适配器"); }
function humanSourceLabel(value?: string | null) { return !value || /unknown source/i.test(value) ? "原始工作记录" : value; }
function humanContentKey(value: string) { return knowledgeLabels[value] || mappedLabel(value, {}, "补充信息"); }
// R-268: a data/metric proposal must stay reachable in the review list even when
// its stored candidate_type is "fact"; both name and target type are read here.
function proposalTargetTypeOf(value: unknown) {
  const proposal = safeJson(value);
  const original = safeJson(proposal.original_payload ?? proposal);
  return String(original.target_type ?? proposal.target_type ?? "");
}
function isDataTargetProposal(value: unknown) { return ["data", "metric"].includes(proposalTargetTypeOf(value)); }
function readableContent(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未记录";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(readableContent).join("、");
  if (typeof value === "object") return Object.entries(value as RecordMap).map(([key, item]) => `${humanContentKey(key)}：${readableContent(item)}`).join("；");
  return "已记录";
}
function errorMessage(error: unknown) { return error instanceof ApiError ? error.message : error instanceof Error ? error.message : "操作失败，请稍后重试。"; }
function safeJson(value: unknown): RecordMap {
  if (!value) return {};
  if (typeof value === "string") { try { const parsed = JSON.parse(value); return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as RecordMap : {}; } catch { return {}; } }
  return typeof value === "object" && !Array.isArray(value) ? value as RecordMap : {};
}
function proposalText(value: unknown) { const data = safeJson(value); return String(data.statement ?? data.title ?? data.summary ?? data.description ?? "这条候选内容暂无可读摘要。"); }

function Loading({ label = "正在加载…", skeleton = false }: {label?: string; skeleton?: boolean}) { return skeleton ? <div className="skeleton-block" role="status" aria-label={label}><div className="skeleton-row w80" /><div className="skeleton-row" /><div className="skeleton-row w60" /></div> : <div className="loading-state" role="status"><span className="loading-dot" />{label}</div>; }
function StateBoundary<T>({ resource, children, empty, loading }: {resource: {loading: boolean; error?: string; retry: () => void; data?: T}; children: (data: T) => ReactNode; empty?: ReactNode; loading?: ReactNode}) {
  if (resource.loading) return <>{loading ?? <Loading skeleton />}</>;
  if (resource.error) return <div className="state-message state-message-error" role="alert"><p>{resource.error}</p><button className="secondary-button" onClick={resource.retry}>重试</button></div>;
  return resource.data === undefined ? <>{empty ?? <p className="empty-state">这里暂时没有可显示的内容。</p>}</> : <>{children(resource.data)}</>;
}
function PageHeader({ eyebrow, title, description, action, illustration }: {eyebrow?: string; title: string; description?: string; action?: ReactNode; illustration?: HeaderIllustrationKey}) { return <header className="page-header"><div>{eyebrow && <p className="page-eyebrow">{eyebrow}</p>}<h1>{title}</h1>{description && <p>{description}</p>}</div>{illustration && <span className="page-header-art" aria-hidden="true"><PageHeaderIllustrationAuto page={illustration} /></span>}{action}</header>; }
function PageHeaderIllustrationAuto({ page }: {page: HeaderIllustrationKey}) { const map: Record<HeaderIllustrationKey, () => ReactElement> = { today: HeaderIllustrationToday, projects: HeaderIllustrationProjects, knowledge: HeaderIllustrationKnowledge, sources: HeaderIllustrationSources, todo: HeaderIllustrationTodo, calendar: HeaderIllustrationCalendar, review: HeaderIllustrationReview, agy: HeaderIllustrationAgy }; const Component = map[page]; return <Component />; }
function Section({ title, action, children, className = "" }: {title: string; action?: ReactNode; children: ReactNode; className?: string}) { return <section className={`card today-section ${className}`}><div className="section-heading"><h2>{title}</h2>{action}</div>{children}</section>; }
function Empty({ message }: {message: string}) { return <p className="empty-state">{message}</p>; }
function StatusBadge({ label, tone = "neutral" }: {label: string; tone?: string}) { return <span className={`badge badge-${tone}`}>{label}</span>; }

const L1_EXPLAINER_QUESTIONS: Array<[string, keyof ProjectExplainer]> = [
  ["这是什么", "what_it_is"],
  ["为什么要做", "why_it_matters"],
  ["解决什么问题", "problem_it_solves"],
  ["它怎么运作", "how_it_works"],
  ["目前做到哪里", "current_state"],
  ["已经取得什么结果", "key_results"],
  ["当前最大问题", "key_open_issue"],
  ["下一步", "next_step"],
];
const L2A_SECTIONS: Array<[string, string]> = [
  ["why_and_background", "1 项目背景"],
  ["project_goals", "2 项目目标"],
  ["scope", "3 项目范围"],
  ["how_it_works", "4 项目如何运作"],
  ["historical_evolution", "5 项目发展历程"],
  ["current_phase_and_status", "6 当前阶段"],
];
const L2B_SECTIONS: Array<[string, string]> = [
  ["historical_evolution", "一、历史演进"],
  ["historical_key_decisions", "二、历史关键决策"],
  ["data_evolution", "三、数据与指标演变"],
  ["current_effective_rules", "四、当前有效规则"],
  ["current_phase_and_status", "五、当前阶段与状态"],
  ["latest_progress", "六、最新进展"],
  ["completed_milestones", "七、已完成事项"],
  ["current_todos_and_open_items", "八、当前待办与未决事项"],
  ["risks_blockers_ambiguities", "九、风险 / 阻塞 / 歧义"],
  ["cross_project_relations", "十、跨项目关系"],
  ["next_actions", "十一、下一步行动"],
  ["future_direction", "十二、未来方向"],
];
const HANDBOOK_KEY_LABELS: Record<string, string> = {
  before: "以前是什么情况", problem_with_old_way: "原来的方式有什么问题", why_this_project: "为什么要建立这个项目",
  business_value: "对业务有什么价值", narrative: "说明", ultimate: "最终目标", stage: "阶段目标",
  not_this_project: "不属于本项目", in: "范围内", out: "范围外",
  why_important: "为什么重要", solved: "解决了什么", leftover: "留下的问题",
  what: "发生了什么", what_changed: "发生了什么变化", what_it_means: "这个变化意味着什么",
  limits: "口径限制 / 风险", note: "说明", text: "内容", process: "流程",
  steps: "步骤", pending_state: "待确认状态", evidence: "证据", kind: "类型",
  trust: "证据可信度", from: "来自", to: "指向", type: "关系", summary: "关系说明",
  proven: "是否证实", sufficiency: "证据充分性", confidence: "把握",
  canonical_todos: "正式待办", open_canonical: "未完成待办", open_business_items: "仍待处理的业务事项",
  unassigned_related: "相关但未挂本项目的待办", term: "名词", meaning: "含义",
};
const BOUNDARY_ASSESSMENTS = new Set(["RENAME", "MERGE", "SPLIT", "ARCHIVE", "ARCHIVE_CANDIDATE", "NEW_PROJECT", "NEW_PROJECT_CANDIDATE", "REMAP"]);

function AppShell({ username, path, navigate, onLogout, children, onCapture }: {username: string; path: string; navigate: (path: string) => void; onLogout: () => void; children: ReactNode; onCapture: () => void}) {
  const active = (target: string) => target === "/today" ? path === "/" || path === target : path === target || path.startsWith(`${target}/`);
  const reviewRes = useResource(() => client.reviewCandidates("pending").then(res => res.length).catch(() => 0), [path]);
  const pendingCount = reviewRes.data ?? 0;
  return <div className={`app-shell ${path === "/" || path === "/today" ? "app-shell-today" : ""}`}><aside className="desktop-sidebar"><div className="brand-block"><span className="brand-mark"><PawLogo size={22} /></span><span><strong>个人 AI 工作台</strong><small>知识与效率中心</small></span></div><nav className="desktop-nav" aria-label="主导航">{navItems.map(([label, target, icon]) => <button key={target} className={`nav-item ${active(target) ? "active" : ""}`} onClick={() => navigate(target)}><span className="nav-icon"><NavIcon icon={icon} /></span>{label}</button>)}</nav><button className={`desktop-agy-entry ${active("/agnes") ? "active" : ""}`} onClick={() => navigate("/agnes")}><span className="agy-entry-mark"><Compass size={17} strokeWidth={1.75} aria-hidden="true" /></span><span><strong>Antigravity</strong><small>工作台助手</small></span></button><button className={`nav-item more-entry ${active("/more") ? "active" : ""}`} onClick={() => navigate("/more")}><span className="nav-icon"><MoreHorizontal size={17} strokeWidth={1.75} aria-hidden="true" /></span>更多</button><div className="sidebar-account"><div><span className="status-pulse" />在线</div><strong>{username}</strong><button className="text-button" onClick={() => navigate("/more")}>更多设置</button><button className="text-button" onClick={onLogout}>退出登录</button></div></aside><header className="app-topbar"><span className="brand-mark"><PawLogo size={22} /></span><strong>个人 AI 工作台</strong><button className="topbar-capture" onClick={onCapture}>快速记录</button></header><main className="app-main">{children}</main><button className="quick-capture-fab" type="button" onClick={onCapture} aria-label="快速记录" title="快速记录"><span aria-hidden="true"><Plus size={18} strokeWidth={2.2} /></span><span className="quick-capture-label">快速记录</span></button><nav className="mobile-tab-bar" aria-label="移动端导航"><button className={active("/today") ? "active" : ""} onClick={() => navigate("/today")}><span className="mobile-tab-icon"><Sun size={18} strokeWidth={1.75} aria-hidden="true" /></span><span>今天</span></button><button className={active("/projects") ? "active" : ""} onClick={() => navigate("/projects")}><span className="mobile-tab-icon"><FolderKanban size={18} strokeWidth={1.75} aria-hidden="true" /></span><span>项目</span></button><button className={active("/review") ? "active" : ""} onClick={() => navigate("/review")}><span className="mobile-tab-icon"><ClipboardCheck size={18} strokeWidth={1.75} aria-hidden="true" />{pendingCount > 0 && <span className="mobile-tab-badge" aria-label={`${pendingCount} 条待审核`}>{pendingCount}</span>}</span><span>审核</span></button><button className={active("/agnes") ? "active" : ""} onClick={() => navigate("/agnes")}><span className="mobile-tab-icon"><Compass size={18} strokeWidth={1.75} aria-hidden="true" /></span><span>Agy</span></button><button className={active("/more") ? "active" : ""} onClick={() => navigate("/more")}><span className="mobile-tab-icon"><MoreHorizontal size={18} strokeWidth={1.75} aria-hidden="true" /></span><span>更多</span></button></nav></div>;
}

function CaptureSheet({ onClose, onSaved }: {onClose: () => void; onSaved: () => void}) {
  const [text, setText] = useState(""); const [saving, setSaving] = useState(false); const [error, setError] = useState("");
  async function submit(event: FormEvent) { event.preventDefault(); if (!text.trim() || saving) return; setSaving(true); setError(""); try { await client.saveCapture(text.trim(), `web-${Date.now()}-${Math.random().toString(36).slice(2)}`); onSaved(); onClose(); } catch (e) { setError(errorMessage(e)); } finally { setSaving(false); } }
  return <div className="capture-sheet" role="presentation" onMouseDown={e => e.target === e.currentTarget && onClose()}><section className="capture-sheet-panel" role="dialog" aria-modal="true" aria-labelledby="capture-title"><div className="capture-sheet-header"><div><p className="page-eyebrow">工作记录</p><h2 id="capture-title">快速记录</h2></div><button className="icon-button" aria-label="关闭" onClick={onClose}><X size={16} strokeWidth={1.7} aria-hidden="true" /></button></div><form onSubmit={submit}><textarea autoFocus value={text} onChange={e => setText(e.target.value)} placeholder="记下一个事实、想法或下一步…" rows={7} maxLength={10000} /><div className="capture-form-footer">{error && <p className="login-error" role="alert">{error}</p>}<span>{text.length}/10000</span><button className="primary-button" disabled={saving || !text.trim()}>{saving ? "保存中…" : "保存记录"}</button></div></form></section></div>;
}

function TodayPage({ navigate, onCapture }: {navigate: (path: string) => void; onCapture: () => void}) {
  const [todoLocal, setTodoLocal] = useState<Record<string, boolean>>({});
  const [captureText, setCaptureText] = useState(""); const [captureSaving, setCaptureSaving] = useState(false); const [captureError, setCaptureError] = useState("");
  const today = useResource(() => client.today(), []); const knowledge = useResource(() => client.knowledge(), []); const reviews = useResource(() => client.reviewCandidates("pending"), []); const activity = useResource(() => client.activities(), []);
  const todos = (today.data?.todos ?? []).map(todo => ({ ...todo, done: todoLocal[todo.id] ?? todo.done })); const activeTodos = todos.filter(todo => !todo.done);
  const hour = new Date().getHours(); const greeting = hour < 5 ? "夜深了" : hour < 11 ? "早上好" : hour < 14 ? "中午好" : hour < 18 ? "下午好" : "晚上好";
  const weekday = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"][new Date().getDay()];
  const dateLabel = today.data?.date ? `${today.data.date.replace(/^\d{4}-/, "").replace("-", "月")}日 ${weekday}` : weekday;
  async function submitCapture(event: FormEvent) { event.preventDefault(); const text = captureText.trim(); if (!text || captureSaving) return; setCaptureSaving(true); setCaptureError(""); try { await client.saveCapture(text, `web-${Date.now()}-${Math.random().toString(36).slice(2)}`); setCaptureText(""); today.retry(); } catch (e) { setCaptureError(errorMessage(e)); } finally { setCaptureSaving(false); } }
  const reviewItems = (reviews.data ?? []).slice(0, 3);
  return <div className="page-stack home-page">
    <section className="home-hero">
      <div className="home-hero-text">
        <h1 className="home-greeting"><span className="home-greeting-salute">{greeting}，</span><span className="home-greeting-prompt">今天想先处理什么？</span></h1>
        <p className="home-subtitle">专注重要的事，让 PAW 帮你记住其余内容。</p>
      </div>
      <span className="home-hero-art" aria-hidden="true"><HeaderIllustrationToday /></span>
    </section>
    <form className="home-capture" onSubmit={submitCapture}>
      <textarea value={captureText} onChange={e => setCaptureText(e.target.value)} placeholder="记录一个想法、任务、问题或刚刚发生的事……" rows={2} maxLength={10000} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submitCapture(e); } }} />
      <div className="home-capture-footer">
        {captureError && <p className="login-error" role="alert">{captureError}</p>}
        <span className="home-capture-hint">Enter 保存 · Shift+Enter 换行</span>
        <button className="home-capture-submit" type="submit" disabled={captureSaving || !captureText.trim()} aria-label="保存记录">{captureSaving ? "…" : <ArrowUp size={16} strokeWidth={2.4} aria-hidden="true" />}</button>
      </div>
    </form>
    <Section title="今天的重点" className="home-order-today" action={<span className="home-date-label">{dateLabel}</span>}>
      <StateBoundary resource={today}>{() => activeTodos.length ? <div className="compact-list">{activeTodos.slice(0, 5).map(todo => <TodoRow key={todo.id} todo={todo} compact onChange={done => setTodoLocal(prev => ({ ...prev, [todo.id]: done }))} />)}</div> : <Empty message="今天没有待办，享受片刻清闲。" />}</StateBoundary>
      <button className="home-new-todo" onClick={() => navigate("/todos")}><Plus size={14} strokeWidth={2} aria-hidden="true" /> 新建待办</button>
    </Section>
    <Section title="待我审核" className="home-order-review" action={reviews.data?.length ? <button className="text-button" onClick={() => navigate("/review")}>查看全部 {reviews.data.length} 项 →</button> : undefined}>
      <StateBoundary resource={reviews}>{items => items.length ? <div className="home-review-list">{reviewItems.map(item => <button className="home-review-item" key={item.candidateId} onClick={() => navigate(`/review/${item.candidateId}`)}><span className="home-review-icon" aria-hidden="true"><ClipboardCheck size={14} strokeWidth={1.75} /></span><span className="home-review-body"><strong>{item.presentation?.display_title ?? item.summary ?? "有事项等待你的判断"}</strong><small>{item.presentation?.situation ?? ""}</small></span><span className="chevron" aria-hidden="true"><ChevronRight size={16} strokeWidth={1.7} /></span></button>)}</div> : <Empty message="目前没有需要你确认的内容。" />}</StateBoundary>
    </Section>
    <Section title="最近变化" className="home-order-recent" action={<button className="text-button" onClick={() => navigate("/knowledge")}>查看知识 ›</button>}>
      <StateBoundary resource={activity}>{data => data.items?.length ? <div className="home-recent-list">{data.items.slice(0, 3).map((item, index) => <div className="home-recent-item" key={item.id ?? index}><span className="home-recent-dot" aria-hidden="true" /><span><strong>{item.summary || item.message || "工作台完成一次整理"}</strong><small>{item.timestamp ? dateText(item.timestamp, true) : "最近"}</small></span></div>)}</div> : <Empty message="最近没有新的变化。" />}</StateBoundary>
      <div className="home-recent-extra"><span className="content-label">最近知识</span><strong>{knowledge.data?.[0]?.title || "尚无最近知识"}</strong></div>
    </Section>
  </div>;
}

function TodoRow({ todo, compact = false, onChange }: {todo: Todo; compact?: boolean; onChange?: (done: boolean) => void}) {
  const [saving, setSaving] = useState(false);
  const [optimisticDone, setOptimisticDone] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  const done = optimisticDone ?? todo.done;
  const toggle = async (nextDone: boolean) => {
    if (!onChange || saving || nextDone === done) return;
    const previous = done;
    setOptimisticDone(nextDone);
    onChange(nextDone);
    setError("");
    setSaving(true);
    try {
      await client.setTodoDone(todo.id, nextDone);
      setOptimisticDone(null);
    } catch (e) {
      setOptimisticDone(null);
      onChange(previous);
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };
  return <div className={`todo-row ${done ? "todo-done" : ""} ${compact ? "todo-row-compact" : ""}`}>
    <input type="checkbox" checked={done} disabled={saving || !onChange} aria-label={onChange ? `标记“${todo.text}”${done ? "未完成" : "完成"}` : `“${todo.text}”完成状态`} onClick={e => e.stopPropagation()} onChange={e => { e.stopPropagation(); void toggle(e.target.checked); }} />
    <span><strong>{todo.text}</strong><small>{todo.projectName || "未分配项目"}{todo.dueAt ? ` · 截止 ${dateText(todo.dueAt)}` : ""}{todo.priority ? ` · ${humanPriority(todo.priority)}优先级` : ""}</small>{error && <small className="todo-error" role="alert">{error}</small>}</span>
  </div>;
}
function ActivityList({ items }: {items: Activity[]}) { return <div className="activity-list">{items.map((item, index) => <div className="activity-row" key={item.id ?? index}><span className="activity-dot" /><span><strong>{item.summary || item.message || "AI 已完成一次工作台分析"}</strong><small>{item.timestamp ? dateText(item.timestamp, true) : "最近"}</small></span></div>)}</div>; }
function SystemSummary() { const status = useResource(() => client.status(), []); return <StateBoundary resource={status}>{data => <div className="system-summary"><ServiceChip name="云端工作空间" state={data.cloudHub} /><ServiceChip name="办公电脑连接" state={data.windowsBridge} /><ServiceChip name="WPS 资料读取" state={data.wpsAvailable} /></div>}</StateBoundary>; }
function ServiceChip({ name, state }: {name: string; state?: string}) { const online = ["online", "available", "ready"].includes(state ?? ""); return <div className="service-chip"><span className={`service-dot ${online ? "online" : ""}`} /><span><strong>{name}</strong><small>{online ? "可用" : state === "quota_limited" ? "配额暂时受限" : "暂不可用"}</small></span></div>; }

function padDatePart(value: number) { return String(value).padStart(2, "0"); }
function formatLocalDate(date: Date) { return `${date.getFullYear()}-${padDatePart(date.getMonth() + 1)}-${padDatePart(date.getDate())}`; }
function formatYearMonth(year: number, month: number) { return `${year}-${padDatePart(month)}`; }
function moveMonth(month: string, delta: number) {
  const [year, mon] = month.split("-").map(Number);
  let nextYear = year;
  let nextMonth = mon + delta;
  while (nextMonth < 1) { nextMonth += 12; nextYear -= 1; }
  while (nextMonth > 12) { nextMonth -= 12; nextYear += 1; }
  return formatYearMonth(nextYear, nextMonth);
}
function buildCalendarDays(month: string) {
  const [year, mon] = month.split("-").map(Number);
  const first = new Date(year, mon - 1, 1);
  const count = new Date(year, mon, 0).getDate();
  const offset = (first.getDay() + 6) % 7;
  return Array.from({ length: Math.ceil((offset + count) / 7) * 7 }, (_, i) => {
    const d = new Date(year, mon - 1, i - offset + 1);
    return { date: formatLocalDate(d), inMonth: d.getMonth() === mon - 1 };
  });
}

function DailyPage({ route, navigate }: {route: Route; navigate: (path: string) => void}) {
  const business = useResource(() => client.today().then(data => data.date), []); const today = business.data ?? formatLocalDate(new Date()); const selected = route.query.get("date") || today; const [month, setMonth] = useState(selected.slice(0, 7)); const daily = useResource(() => client.daily(selected), [selected]); const calendar = useResource(() => client.calendar(month), [month]); const [search, setSearch] = useState(""); const [query, setQuery] = useState(""); const results = useResource(() => query ? client.searchCaptures(query) : Promise.resolve({ items: [], total: 0, query: "", nextCursor: null }), [query]);
  useEffect(() => { const selectedMonth = selected.slice(0, 7); setMonth(current => current === selectedMonth ? current : selectedMonth); }, [selected]);
  const days = useMemo(() => buildCalendarDays(month), [month]);
  return <div className="page-stack daily-page"><PageHeader eyebrow="原始记录 · 只读追溯" title="日记" description="用月历找回记录过的事实，查看不会触发整理或同步。" illustration="calendar" action={selected !== today ? <button className="secondary-button" onClick={() => navigate("/daily")}>回到今天</button> : undefined} /><section className="card daily-search-panel"><form className="search-form" onSubmit={e => { e.preventDefault(); setQuery(search.trim()); }}><label htmlFor="daily-search">搜索历史记录</label><div><input id="daily-search" value={search} onChange={e => setSearch(e.target.value)} placeholder="例如：运营导师、会议" maxLength={200} /><button className="primary-button" disabled={!search.trim()}>搜索</button></div></form>{query && <StateBoundary resource={results}>{data => data.items.length ? <div className="search-results"><p>“{query}”找到 {data.total} 条记录</p>{data.items.map(item => <button key={item.id} onClick={() => navigate(`/daily?date=${encodeURIComponent(item.date)}&capture=${encodeURIComponent(item.id)}`)}><strong>{item.date}</strong><small>{dateText(item.capturedAt, true)}</small><span>{item.snippet}</span></button>)}</div> : <Empty message="没有找到匹配的历史原始记录。" />}</StateBoundary>}</section><section className="card daily-calendar-card"><div className="calendar-heading"><button className="icon-button" aria-label="上个月" onClick={() => setMonth(moveMonth(month, -1))}><ChevronLeft size={16} strokeWidth={1.7} aria-hidden="true" /></button><h2>{month.replace("-", "年")}月</h2><button className="icon-button" aria-label="下个月" onClick={() => setMonth(moveMonth(month, 1))}><ChevronRight size={16} strokeWidth={1.7} aria-hidden="true" /></button></div><div className="weekdays">{["一", "二", "三", "四", "五", "六", "日"].map(day => <span key={day}>{day}</span>)}</div><StateBoundary resource={calendar}>{data => <div className="calendar-grid" role="grid">{days.map(day => <button key={day.date} className={`${day.inMonth ? "" : "outside-month"} ${day.date === selected ? "selected" : ""} ${day.date === today ? "today" : ""}`} onClick={() => navigate(`/daily?date=${day.date}`)}><span>{Number(day.date.slice(-2))}</span>{data.days[day.date] ? <small>{data.days[day.date]}</small> : null}</button>)}</div>}</StateBoundary></section><section className="daily-entry"><div className="section-heading"><div><p className="page-eyebrow">{selected === today ? "今天" : "历史记录 · 只读"}</p><h2>{selected}</h2></div>{selected === today && <button className="secondary-button" onClick={() => navigate("/today")}>去今天记录</button>}</div><StateBoundary resource={daily}>{data => data.captures.length ? <div className="daily-capture-list">{data.captures.map(capture => <article className="card" key={capture.id} id={`capture-${capture.id}`}><div className="capture-meta"><time>{dateText(capture.capturedAt, true)}</time><span>{humanSource(capture.source)}</span>{(() => { const badge = captureDisplayBadge(capture as Capture); return <StatusBadge label={badge.label} tone={badge.tone} />; })()}</div><p>{capture.rawText}</p></article>)}</div> : <Empty message={`${selected} 当天没有记录。`} />}</StateBoundary></section></div>;
}

function TodosPage({ navigate }: {navigate: (path: string) => void}) { const [filter, setFilter] = useState<"all" | "active" | "done">("active"); const todos = useResource(() => client.todos({ status: filter === "all" ? "all" : filter }), [filter]); const [local, setLocal] = useState<Record<string, boolean>>({}); const items = (todos.data ?? []).map(item => ({ ...item, done: local[item.id] ?? item.done })).filter(item => filter === "all" || (filter === "done" ? item.done : !item.done)); return <div className="page-stack"><PageHeader eyebrow="工作清单" title="待办" description="把明确的下一步行动放在一起，完成状态由你决定。" illustration="todo" /><div className="segmented-control" role="tablist">{([["active", "未完成"], ["all", "全部"], ["done", "已完成"]] as const).map(([key, label]) => <button key={key} className={filter === key ? "active" : ""} onClick={() => setFilter(key)}>{label}</button>)}</div><Section title="待办事项" action={<span className="section-count">{items.length}</span>}><StateBoundary resource={todos}>{data => items.length ? <div className="todo-list">{items.map(todo => <TodoRow key={todo.id} todo={todo} onChange={done => setLocal(prev => ({ ...prev, [todo.id]: done }))} />)}</div> : <Empty message={filter === "done" ? "还没有已完成的待办。" : "这里暂时没有待办。"} />}</StateBoundary></Section><p className="read-only-note">待办完成状态通过现有工作台接口保存。</p></div>; }

function ProjectsPage({ navigate }: {navigate: (path: string) => void}) {
  const projects = useResource(() => client.projects(), []);
  const groups = [["进行中", (projects.data ?? []).filter(p => ["active", "in_progress"].includes(p.status ?? ""))], ["计划中", (projects.data ?? []).filter(p => p.status === "planning")], ["其他", (projects.data ?? []).filter(p => !["active", "in_progress", "planning", "archived"].includes(p.status ?? ""))]] as const;
  return <div className="page-stack"><PageHeader eyebrow="Project Workspace" title="项目" description="扫一眼就能知道每个项目是什么、做到哪、卡在哪、下一步是什么。" illustration="projects" /><StateBoundary resource={projects}>{() => projects.data?.filter(p => p.status !== "archived").length ? <div className="project-groups">{groups.map(([title, items]) => items.length ? <section key={title}><div className="group-heading"><h2>{title}</h2><span>{items.length}</span></div><div className="project-grid">{items.map(project => {
    const explainer = project.explainer;
    const statusLabel = project.stage ? `${humanStatus(project.status)} · ${humanStage(project.stage)}` : humanStatus(project.status);
    return <button className="project-card" key={project.id} onClick={() => navigate(`/projects/${project.id}`)}>
      <span className="project-card-head"><strong>{project.name}</strong><StatusBadge label={statusLabel} tone={project.status === "planning" ? "neutral" : "success"} /></span>
      <span className="project-explainer-card-field"><span className="project-explainer-label">一句话读懂</span><span className="project-card-progress">{explainer?.one_sentence || "这份项目还没有30秒说明。"}</span></span>
      <span className="project-explainer-card-field"><span className="project-explainer-label">目前做到哪里</span><span className="project-explainer-card-text">{explainer?.current_state || "尚未记录。"}</span></span>
      <span className="project-explainer-card-field"><span className="project-explainer-label">当前关键问题</span><span className="project-explainer-card-text">{explainer?.key_open_issue || "尚未记录。"}</span></span>
      <span className="project-card-next"><span className="project-explainer-label">下一步</span>{explainer?.next_step || "尚未记录下一步。"}</span>
      <span className="project-explainer-cta">查看完整项目 ›</span>
    </button>;
  })}</div></section> : null)}</div> : <Empty message="项目列表暂时为空。" />}</StateBoundary></div>;
}
function ProjectDetail({ id, navigate }: {id: string; navigate: (path: string) => void}) {
  const resource = useResource(() => client.project(id), [id]);
  const projection = useResource(() => client.projectProjection(id), [id]);
  const [local, setLocal] = useState<Record<string, boolean>>({});
  const [handbookOpen, setHandbookOpen] = useState(true);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  return <div className="page-stack"><button className="back-button" onClick={() => navigate("/projects")}>‹ 返回项目</button><StateBoundary resource={resource} empty={<Empty message="找不到这个项目。" />}>{data => {
    if (!data) return <Empty message="找不到这个项目。" />;
    const record = data as RecordMap;
    const project = data.project as Project;
    const todos = (data.todos as Todo[]).map(todo => ({ ...todo, done: local[todo.id] ?? todo.done }));
    const handbook = (record.project_handbook as RecordMap | undefined) ?? null;
    const explainer = (project.explainer || (record.project_explainer as ProjectExplainer | undefined) || (handbook?.project_explainer as ProjectExplainer | undefined) || null);
    const handbookRevision = record.handbook_revision;
    const handbookQuality = String(record.handbook_quality_gate ?? "");
    const evidenceLabel = handbookQuality === "PASS" ? "证据充分" : (handbook ? "部分证据待确认" : "说明书待生成");
    const drafts = Array.isArray(record.drafts) ? (record.drafts as RecordMap[]).map(item => ({ id: String(item.draft_id ?? item.id), draftId: String(item.draft_id ?? item.id), sourceVersionId: String(item.source_version_id ?? ""), sourceTitle: String(item.source_title ?? ""), summary: String(item.summary ?? ""), status: String(item.status ?? "LUNA_UNREVIEWED"), projectName: String(item.project_name ?? project.name) })) : [];
    const draftContext = (record.draft_context as RecordMap | undefined) ?? null;
    const draftTopics = Array.isArray(record.draft_knowledge_topics) ? record.draft_knowledge_topics as RecordMap[] : [];
    const dataSummary = (record.data_summary as RecordMap | undefined) ?? null;
    const timeline = Array.isArray(record.timeline) ? record.timeline as RecordMap[] : [];
    const draftDecisions = Array.isArray(record.draft_decisions) ? record.draft_decisions as RecordMap[] : [];
    const draftRisks = Array.isArray(record.draft_risks) ? record.draft_risks as RecordMap[] : [];
    const draftConflicts = Array.isArray(record.draft_conflicts) ? record.draft_conflicts as RecordMap[] : [];
    const sourceEvidence = Array.isArray(record.source_evidence) ? record.source_evidence as RecordMap[] : [];
    const observationPreview = Array.isArray(dataSummary?.observation_preview) ? dataSummary?.observation_preview as RecordMap[] : [];
    const openTodos = todos.filter(t => !t.done);
    const doneTodos = todos.filter(t => t.done);
    const proj = (projection.data ?? null) as RecordMap | null;
    const formalMetrics = Array.isArray(proj?.formal_metrics) ? proj.formal_metrics as RecordMap[] : [];
    const recentChanges = Array.isArray(proj?.recent_changes) ? proj.recent_changes as RecordMap[] : [];
    const currentRules = Array.isArray(proj?.current_rules) ? proj.current_rules as RecordMap[] : [];
    const relatedProjects = Array.isArray(proj?.related_projects) ? proj.related_projects as RecordMap[] : [];
    const historyInfo = (proj?.history as RecordMap | undefined) ?? null;
    const projectProjectionMeta = (proj?.project as RecordMap | undefined) ?? null;
    const freshnessLabel = projectProjectionMeta?.freshness_label ? String(projectProjectionMeta.freshness_label) : null;
    const projectUpdatedAt = projectProjectionMeta?.updated_at ? String(projectProjectionMeta.updated_at) : "";
  return <>
    <PageHeader
      eyebrow={`${humanStatus(project.status)} · ${project.stage || "阶段未记录"}`}
      title={project.name}
      description={explainer?.one_sentence || "打开下方说明，用三分钟理解这个项目。"}
      action={<div className="project-handbook-meta">
        <StatusBadge label={handbookRevision != null ? `项目说明书 v${String(handbookRevision)}` : "项目说明书"} />
        <StatusBadge label={evidenceLabel} tone={evidenceLabel === "证据充分" ? "success" : "warning"} />
        <StatusBadge label={freshnessLabel ?? "信息待刷新"} tone="neutral" />
      </div>}
    />
    <div className="detail-layout">
      <main>
        <ExplainerL1 explainer={explainer} />
        {formalMetrics.length > 0 && <section className="detail-section v13c-metrics-section">
          <h2>正式指标</h2>
          <p className="project-explainer-layer-note">治理链可追溯的正式数字；多维指标按维度分行展示。</p>
          <div className="v13c-metric-grid">{formalMetrics.map(m => <div className="v13c-metric-card" key={String(m.metric_id)}>
            <strong>{String(m.name ?? "")}</strong>
            {m.is_multidimensional ? <div className="v13c-metric-dims">{(m.dimension_rows as RecordMap[] ?? []).map((d, i) => <div className="v13c-metric-dim-row" key={i}><span>{Object.entries((d.dimensions as RecordMap) ?? {}).map(([k, v]) => `${k}=${String(v)}`).join(" · ")}</span><strong>{String(d.value ?? "")}</strong></div>)}</div> : <div className="v13c-metric-value">{String(m.effective_current_value ?? "暂无当前值")}{m.unit ? ` ${String(m.unit)}` : ""}</div>}
          </div>)}</div>
        </section>}
        {recentChanges.length > 0 && <section className="detail-section v13c-changes-section">
          <h2>最近业务变化</h2>
          <div className="v13c-change-list">{recentChanges.slice(0, 8).map((c, i) => <div className="v13c-change-row" key={i}><span className="v13c-change-type">{String(c.change_type ?? "")}</span><span className="v13c-change-date">{dateText(String(c.committed_at ?? ""))}</span></div>)}</div>
        </section>}
        {currentRules.length > 0 && <section className="detail-section v13c-rules-section">
          <h2>当前适用规则</h2>
          <div className="v13c-rule-list">{currentRules.slice(0, 10).map(r => <button className="v13c-rule-row" key={String(r.knowledge_id)} onClick={() => navigate(`/knowledge/${encodeURIComponent(String(r.knowledge_id))}`)}>
            <span className="v13c-rule-body"><strong>{String(r.title ?? "")}</strong><small>{String(r.one_line_summary ?? "")}</small></span>
            <span className="v13c-rule-state">{String(r.authority_label ?? "")} · {String(r.evidence_label ?? "")}</span>
          </button>)}</div>
        </section>}
        {relatedProjects.length > 0 && <section className="detail-section v13c-related-section">
          <h2>关联项目</h2>
          <div className="v13c-related-list">{relatedProjects.map(p => <button className="text-button" key={String(p.project_id)} onClick={() => navigate(`/projects/${encodeURIComponent(String(p.project_id))}`)}>{String(p.name ?? "")}</button>)}</div>
        </section>}
        {historyInfo && <section className="detail-section v13c-history-section">
          <h2>历史与版本</h2>
          <div className="meta-stack"><Metric label="说明书版本" value={historyInfo.handbook_revision != null ? `v${String(historyInfo.handbook_revision)}` : "未记录"} /><Metric label="关联知识" value={`${String(historyInfo.knowledge_total ?? 0)} 条（含 ${String(historyInfo.knowledge_provisional_count ?? 0)} 条待确认）`} />{historyInfo.valid_time_disclosure ? <Metric label="历史有效期" value="历史信息不完整" /> : null}</div>
        </section>}
        {handbook ? <>
          <section className="detail-section project-handbook-l2a">
            <h2>项目说明</h2>
            <p className="project-explainer-layer-note">用几分钟看懂背景、目标、范围、怎么运作、怎么走到今天。</p>
            {L2A_SECTIONS.map(([key, title]) => (
              key === "scope"
                ? <HandbookSection key={key} title={title} value={{ 范围内: handbook.scope, 不属于本项目: handbook.out_of_scope }} />
                : <HandbookSection key={key} title={title} value={handbook[key]} />
            ))}
          </section>
          <section className="detail-section project-handbook-l2b">
            <h2>完整项目说明书</h2>
            <button className="text-button handbook-toggle" onClick={() => setHandbookOpen(o => !o)} aria-expanded={handbookOpen}>
              {handbookOpen ? "收起完整说明书" : "展开完整说明书"}
            </button>
            {handbookOpen && <>
              {L2B_SECTIONS.map(([key, title]) => (
                <HandbookSection key={`l2b-${key}`} title={title} value={handbook[key]} defaultOpen={key === "historical_evolution" || key === "current_effective_rules" || key === "risks_blockers_ambiguities"} />
              ))}
              {(openTodos.length || doneTodos.length) ? (
                <DetailSection title="当前正式待办">
                  {openTodos.length ? <div className="todo-list">{openTodos.map(todo => <TodoRow key={todo.id} todo={todo} onChange={done => setLocal(prev => ({ ...prev, [todo.id]: done }))} />)}</div> : null}
                  {doneTodos.length ? <><p className="section-label">已完成待办</p><div className="todo-list">{doneTodos.map(todo => <TodoRow key={todo.id} todo={todo} onChange={done => setLocal(prev => ({ ...prev, [todo.id]: done }))} />)}</div></> : null}
                </DetailSection>
              ) : null}
            </>}
          </section>
        </> : <Empty message="这份项目还没有可阅读的说明书。下方证据与分析仍可下钻。" />}
      </main>
      <aside>
        <section className="action-panel"><span>下一步</span><strong>{explainer?.next_step || project.nextActionSummary || "尚未记录下一步"}</strong><small>{project.dueDate ? `截止：${dateText(project.dueDate)}` : "尚未设置截止时间"}</small></section>
        <DetailSection title="项目信息"><div className="overview-grid"><Metric label="状态" value={humanStatus(project.status)} /><Metric label="阶段" value={project.stage || "未记录"} /><Metric label="优先级" value={project.priority || "未记录"} /><Metric label="截止日期" value={dateText(project.dueDate)} /></div><div className="meta-stack"><Metric label="信息同步" value={freshnessLabel ?? "信息待刷新"} />{projectUpdatedAt ? <Metric label="最近更新" value={dateText(projectUpdatedAt)} /> : null}</div></DetailSection>
      </aside>
    </div>
    <DetailSection title="证据与分析">
      <p className="project-explainer-layer-note">普通阅读不需要打开这一层。这里是知识、资料、分析草稿和证据索引。</p>
      <button className="text-button handbook-toggle" onClick={() => setEvidenceOpen(o => !o)} aria-expanded={evidenceOpen}>
        {evidenceOpen ? "收起证据与分析" : "展开证据与分析"}
      </button>
      {evidenceOpen && <div className="evidence-drilldown">
        {handbook?.evidence_index != null ? <HandbookSection title="证据索引" value={handbook.evidence_index} defaultOpen={false} /> : null}
        {handbook?.semantic_assessment != null ? <SemanticAssessmentRow assessment={(handbook.semantic_assessment as RecordMap | undefined) ?? {}} /> : null}
        {handbook?._provenance != null ? <HandbookSection title="来源说明" value={handbook._provenance} defaultOpen={false} /> : null}
        {draftContext && <DetailSection title="分析草稿摘要"><div className="meta-stack"><Metric label="权威级别" value="草稿" /><Metric label="当前状态" value={String(draftContext.current_state ?? "未记录")} /><Metric label="关联来源" value={String((draftContext.linked_source_versions as unknown[] | undefined)?.length ?? 0)} /></div></DetailSection>}
        {drafts.length ? <DetailSection title={`分析草稿 · ${drafts.length}`}><div className="draft-list">{drafts.map(draft => <button className="draft-card" key={draft.draftId} onClick={() => navigate(`/drafts/${encodeURIComponent(draft.draftId)}`)}><span className="draft-card-head"><StatusBadge label={humanDraftStatus(draft.status)} tone={draftStatusTone(draft.status)} /><span className="draft-card-label">分析草稿</span></span><strong>{draft.sourceTitle || draft.sourceVersionId}</strong><p>{draft.summary || "暂无摘要。"}</p></button>)}</div></DetailSection> : null}
        {Array.isArray(data.knowledge) && data.knowledge.length ? <DetailSection title={`知识 · ${data.knowledge.length}`}><DetailList items={data.knowledge as RecordMap[]} /></DetailSection> : null}
        {draftTopics.length ? <DetailSection title="草稿主题"><div className="detail-list">{draftTopics.map((item, index) => <div className="detail-list-row" key={`draft-topic-${index}`}><strong>{String(item.topic ?? "未命名主题")}</strong><small>草稿 · 非正式知识</small></div>)}</div></DetailSection> : null}
        {observationPreview.length ? <DetailSection title={`数据明细 · ${observationPreview.length}`}><div className="detail-list">{observationPreview.map((item, index) => <div className="detail-list-row" key={`metric-${index}`}><strong>{String(item.metric ?? "未命名指标")}</strong><small>{[item.period && typeof item.period === "object" ? String((item.period as RecordMap).label ?? "") : "", item.value != null ? `值 ${String(item.value)}` : "", item.unit ? String(item.unit) : ""].filter(Boolean).join(" · ") || "分析平面"}</small></div>)}</div></DetailSection> : null}
        {timeline.length ? <DetailSection title="时间线"><div className="detail-list">{timeline.map((item, index) => <div className="detail-list-row" key={`timeline-${index}`}><strong>{String(item.label ?? item.event_id ?? "事件")}</strong><small>{String(item.stage ?? item.effective_from ?? "草稿")}</small></div>)}</div></DetailSection> : null}
        {draftDecisions.length ? <DetailSection title="草稿决策"><div className="detail-list">{draftDecisions.map((item, index) => <div className="detail-list-row" key={`draft-decision-${index}`}><strong>{String(item.summary ?? item.decision_id ?? "决策")}</strong><small>草稿 · 带来源证据</small></div>)}</div></DetailSection> : null}
        {draftRisks.length || draftConflicts.length ? <DetailSection title="风险与冲突"><div className="detail-list">{[...draftRisks, ...draftConflicts].map((item, index) => <div className="detail-list-row" key={`risk-${index}`}><strong>{String(item.summary ?? item.risk_id ?? item.conflict_id ?? "事项")}</strong><small>草稿</small></div>)}</div></DetailSection> : null}
        {Array.isArray(data.sources) && data.sources.length ? <DetailSection title="资料"><DetailList items={data.sources as RecordMap[]} /></DetailSection> : null}
        {sourceEvidence.length ? <DetailSection title="草稿证据"><div className="detail-list">{sourceEvidence.slice(0, 20).map((item, index) => <div className="detail-list-row" key={`evidence-${index}`}><strong>{String(item.source_version_id ?? "来源")}</strong><small>{String(item.evidence_locator ?? "证据定位")}</small></div>)}</div></DetailSection> : null}
        {Array.isArray(data.pending) && data.pending.length ? <DetailSection title="待确认事项"><DetailList items={data.pending as RecordMap[]} /></DetailSection> : null}
      </div>}
    </DetailSection>
  </>;
  }}</StateBoundary></div>;
}
function ExplainerL1({ explainer }: { explainer: ProjectExplainer | null }) {
  if (!explainer) {
    return <section className="detail-section project-explainer-l1"><h2>30秒读懂这个项目</h2><Empty message="这份项目还没有30秒说明。完整说明书如果已经写好，仍可往下阅读。" /></section>;
  }
  const glossary = explainer.glossary;
  return (
    <section className="detail-section project-explainer-l1">
      <h2>30秒读懂这个项目</h2>
      {explainer.one_sentence ? <p className="project-explainer-lead">{explainer.one_sentence}</p> : null}
      <div className="project-explainer-qa-grid">
        {L1_EXPLAINER_QUESTIONS.map(([question, key]) => {
          const answer = explainer[key];
          if (typeof answer !== "string" || !answer.trim()) return null;
          return <div className="project-explainer-qa" key={key}><h3>{question}</h3><p className="handbook-read">{answer}</p></div>;
        })}
      </div>
      {glossary != null && glossary !== "" ? <div className="project-explainer-glossary"><h3>名词解释</h3><HandbookValue value={glossary} /></div> : null}
    </section>
  );
}
function DetailSection({ title, children }: {title: string; children: ReactNode}) { return <section className="detail-section"><h2>{title}</h2>{children}</section>; }
function Metric({ label, value }: {label: string; value: string}) { return <div className="metric"><span>{label}</span><strong>{value}</strong></div>; }

// ─── Project Handbook Renderer ───────────────────────────────────────────────
// Renders the canonical project_handbook as the primary user reading surface.
// Handles string / string[] / object[] / object / null safely. Never JSON.stringify.

function humanLevel(value: unknown): string {
  const v = String(value ?? "").toUpperCase();
  if (v === "FACT") return "事实";
  if (v === "INFERENCE") return "推断";
  if (v === "PROPOSAL") return "建议";
  if (v === "PROVISIONAL") return "暂定";
  return v || "未标注";
}
function humanState(value: unknown): string {
  const v = String(value ?? "").toUpperCase();
  if (v === "CURRENT") return "当前有效";
  if (v === "SUPERSEDED") return "已被替代";
  if (v === "CONFLICT") return "冲突待裁定";
  if (v === "UNKNOWN") return "未知";
  if (v === "PENDING_SUPERSESSION_CONFIRMATION") return "待确认是否替代";
  return v || "未标注";
}
function stateTone(value: unknown): string {
  const v = String(value ?? "").toUpperCase();
  if (v === "CURRENT") return "success";
  if (v === "CONFLICT") return "danger";
  if (v === "UNKNOWN" || v.includes("PENDING")) return "warning";
  if (v === "SUPERSEDED") return "neutral";
  return "neutral";
}
function levelTone(value: unknown): string {
  const v = String(value ?? "").toUpperCase();
  if (v === "FACT") return "success";
  if (v === "INFERENCE" || v === "PROVISIONAL") return "warning";
  if (v === "PROPOSAL") return "neutral";
  return "neutral";
}

/** Render a single handbook value node safely. */
function HandbookValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <Empty message="暂无记录。" />;
  if (typeof value === "string") return <p className="handbook-read">{value}</p>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <Empty message="暂无记录。" />;
    return <div className="handbook-list">{value.map((item, idx) => <HandbookItem key={idx} item={item} index={idx} />)}</div>;
  }
  if (typeof value === "object") {
    return <HandbookObject obj={value as RecordMap} />;
  }
  return <p className="handbook-read">{String(value)}</p>;
}

function HandbookItem({ item, index }: { item: unknown; index: number }) {
  if (typeof item === "string") return <div className="handbook-list-row"><p className="handbook-read">{item}</p></div>;
  if (typeof item === "object" && item !== null) {
    const obj = item as RecordMap;
    const when = obj.when ?? obj.date ?? obj.period;
    const level = obj.level;
    const state = obj.state;
    const text = obj.text ?? obj.what ?? obj.summary ?? obj.title ?? obj.description ?? obj.narrative ?? obj.content;
    return (
      <div className="handbook-list-row">
        <div className="handbook-item-head">
          {when != null && when !== "" ? <span className="handbook-when">{String(when)}</span> : null}
          {level != null && level !== "" ? <StatusBadge label={humanLevel(level)} tone={levelTone(level)} /> : null}
          {state != null && state !== "" ? <StatusBadge label={humanState(state)} tone={stateTone(state)} /> : null}
        </div>
        {text != null && text !== "" ? <p className="handbook-read">{String(text)}</p> : null}
        {Object.keys(obj).length > 0 && <HandbookObject obj={obj} skipKeys={new Set(["when","date","period","level","state","text","what","summary","title","description","narrative","content"])} />}
      </div>
    );
  }
  return <div className="handbook-list-row"><p className="handbook-read">{String(item)}</p></div>;
}

function HandbookObject({ obj, skipKeys }: { obj: RecordMap; skipKeys?: Set<string> }) {
  const entries = Object.entries(obj).filter(([k]) => !skipKeys?.has(k)).filter(([,v]) => v !== null && v !== undefined && v !== "");
  if (entries.length === 0) return null;
  return (
    <div className="handbook-object">
      {entries.map(([key, val]) => (
        <div key={key} className="handbook-object-row">
          <span className="handbook-object-label">{HANDBOOK_KEY_LABELS[key] || key}</span>
          <div className="handbook-object-value">
            <HandbookValue value={val} />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Render a full handbook section with Chinese title. */
function HandbookSection({ title, value, defaultOpen = true }: { title: string; value: unknown; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const empty = value === null || value === undefined || (Array.isArray(value) && value.length === 0) || (typeof value === "object" && !Array.isArray(value) && Object.keys(value as object).length === 0);
  return (
    <DetailSection title={title}>
      <button className="text-button handbook-toggle" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        {open ? "收起" : "展开"}
      </button>
      {open && (empty ? <Empty message="暂无记录。" /> : <HandbookValue value={value} />)}
    </DetailSection>
  );
  }

/** Semantic assessment badge row, shown as secondary info. */
function SemanticAssessmentRow({ assessment }: { assessment: RecordMap }) {
  const verdict = String(assessment.assessment ?? "").toUpperCase();
  if (!verdict) return null;
  const boundary = BOUNDARY_ASSESSMENTS.has(verdict);
  const tone = verdict === "KEEP" ? "success" : boundary ? "warning" : "neutral";
  return (
    <div className="project-handbook-assessment">
      <div className="badge-row">
        <StatusBadge label={verdict} tone={tone} />
        {boundary ? <StatusBadge label="建议，尚未改变正式项目登记" tone="warning" /> : <StatusBadge label="正式登记未改" />}
      </div>
      {assessment.reason != null && assessment.reason !== "" ? <p className="project-handbook-prose">{String(assessment.reason)}</p> : null}
      {assessment.user_visible_notice != null && assessment.user_visible_notice !== "" ? <p className="read-only-note">{String(assessment.user_visible_notice)}</p> : null}
    </div>
  );
}
function DetailList({ items }: {items: RecordMap[]}) { return <div className="detail-list">{items.map((item, index) => <div className="detail-list-row" key={String(item.id ?? index)}><strong>{String(item.title ?? item.name ?? item.summary ?? "未命名事项")}</strong><small>{item.description ? String(item.description) : humanRelation(typeof item.relation_type === "string" ? item.relation_type : undefined)}</small></div>)}</div>; }

function KnowledgePage({ navigate }: {navigate: (path: string) => void}) {
  const knowledge = useResource(() => client.knowledge(), []);
  const drafts = useResource(() => client.drafts({ limit: 50 }), []);
  const [searchText, setSearchText] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [draftsOpen, setDraftsOpen] = useState(false);
  const searchResults = useResource(() => searchQuery ? client.knowledgeSearch(searchQuery) : Promise.resolve({ items: [], count: 0, query: "" } as RecordMap), [searchQuery]);
  return <div className="page-stack">
    <PageHeader eyebrow="知识库" title="知识" description="查看已经确认的知识、项目经验和业务规则。" illustration="knowledge" />
    <Section title="知识搜索" className="knowledge-search-section">
      <div className="knowledge-search-bar">
        <input className="knowledge-search-input" type="search" placeholder="搜索正式知识" aria-label="搜索正式知识" value={searchText} onChange={e => setSearchText(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && searchText.trim()) setSearchQuery(searchText.trim()); }} />
        <button className="text-button" onClick={() => { if (searchText.trim()) setSearchQuery(searchText.trim()); }}>搜索</button>
        {searchQuery ? <button className="text-button" onClick={() => { setSearchText(""); setSearchQuery(""); }}>清除</button> : null}
      </div>
      <p className="section-label">在所有正式知识中检索，草稿不会混入结果。</p>
      {searchQuery ? <StateBoundary resource={searchResults} loading={<Loading label="正在搜索知识…" skeleton />}>{res => {
        const items = (res.items ?? []) as RecordMap[];
        const count = Number(res.count ?? items.length);
        return items.length ? <>
          <p className="search-result-count">找到 {count} 条与「{searchQuery}」相关的正式知识。</p>
          <div className="knowledge-grid">{items.map(item => <button className="knowledge-card" key={String(item.knowledge_id)} onClick={() => navigate(`/knowledge/${encodeURIComponent(String(item.knowledge_id))}`)}>
            <span className="knowledge-card-label">{String(item.authority_label ?? "状态待确认")} · {item.canonical ? "证据可追溯" : "来源依据不完整"}</span>
            <strong>{String(item.title ?? "")}</strong>
            <p>{businessSummaryFrom(String(item.snippet ?? "")) ?? "暂无摘要"}</p>
            <span className="knowledge-card-meta">{String(item.evidence_label ?? "")}</span>
          </button>)}</div>
        </> : <Empty message="没有找到匹配的当前有效知识。" />;
      }}</StateBoundary> : null}
    </Section>
    <Section title="正式知识">
      <StateBoundary resource={knowledge} loading={<Loading label="正在加载知识…" skeleton />}>{items => {
        const formal = items.filter(item => item.authorityState === "CURRENT_CONFIRMED");
        return formal.length ? <div className="knowledge-grid">{formal.map(item => <button className="knowledge-card" key={item.id} onClick={() => navigate(`/knowledge/${item.id}`)}>
          <span className="knowledge-card-label">{humanKnowledgeType(item.knowledgeType)} · 当前有效</span>
          <strong>{item.title}</strong>
          <p>{knowledgeCardSummary(item)}</p>
          <span className="knowledge-card-meta">{item.projectCount ? `${item.projectCount} 个关联项目 · ` : ""}已确认{item.latestRevisionNo ? ` · 第 ${item.latestRevisionNo} 版` : ""}{item.sourceCount ? ` · ${item.sourceCount} 个来源` : ""}</span>
        </button>)}</div> : <Empty message="暂无正式知识。草稿需完成治理确认后进入此处。" />;
      }}</StateBoundary>
    </Section>
    <Section title={`待确认草稿 · ${drafts.data?.total ?? 0}`} className="draft-section" action={<button className="text-button" onClick={() => setDraftsOpen(o => !o)} aria-expanded={draftsOpen}>{draftsOpen ? "收起" : "展开"}</button>}>
      <p className="section-label">这些内容还没有正式确认，仅供了解背景，不能作为正式依据。</p>
      {draftsOpen ? <StateBoundary resource={drafts} loading={<Loading label="正在加载草稿…" skeleton />}>{data => data.items.length ? <div className="knowledge-grid">{data.items.map(item => <button className="draft-card knowledge-card" key={item.draftId} onClick={() => navigate(`/drafts/${encodeURIComponent(item.draftId)}`)}>
        <span className="knowledge-card-label draft-card-label">待确认 · 未正式确认</span>
        <span className="draft-card-head"><StatusBadge label={humanDraftStatus(item.status)} tone={draftStatusTone(item.status)} /></span>
        <strong>{item.sourceTitle || "未命名草稿"}</strong>
        <p>{businessSummaryFrom(item.summary) ?? "暂无摘要"}</p>
        <span className="knowledge-card-meta">{item.projectName || "未关联项目"}</span>
      </button>)}</div> : <Empty message="暂无待确认草稿。" />}</StateBoundary> : null}
    </Section>
  </div>;
}
// TD-003 — the Knowledge card summary.
// Preference: the Formal one-line summary, then the bounded deterministic
// projection of the latest revision body. Both go through the SAME audited
// classifier the detail page uses, so a card can never show parser metadata
// and can never disagree with its detail page.
function knowledgeCardSummary(item: Knowledge): string {
  const direct = businessSummaryFrom(item.oneLineSummary);
  if (direct) return direct;
  const projected = summaryFromContent(item.contentBusinessFields as RecordMap | null | undefined, null);
  return projected ?? "暂无摘要";
}
const knowledgeLabels: Record<string, string> = { management_summary: "管理摘要", project_positioning: "项目定位", core_conclusions: "核心结论", operating_model: "运行机制", current_rules: "当前规则", decision_records: "决策记录", metrics: "核心指标", findings: "关键发现", action_recommendations: "建议动作", timeline: "时间线", risks: "风险", unresolved_items: "待复核事项", lessons_learned: "经验总结", validation_framework: "验证框架", evidence_pending: "待补证据" };
// TD-004 — the evidence panel.  Source Document Name is ONLY ever what the
// backend RECORDED for this source_version_id; it is never inferred from the
// object id, a path, a filename convention or the knowledge title.
function EvidenceSection({ refs, evidenceLabel, hasSources }: {refs: {sourceVersionId: string; locator: string}[]; evidenceLabel: string; hasSources: boolean}) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, RecordMap>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const toggle = (ref: {sourceVersionId: string; locator: string}) => {
    const key = `${ref.sourceVersionId}|${ref.locator}`;
    if (openKey === key) { setOpenKey(null); return; }
    setOpenKey(key);
    if (results[key] || errors[key]) return;
    client.evidenceLookup(ref.sourceVersionId, ref.locator)
      .then(res => setResults(prev => ({ ...prev, [key]: res })))
      .catch(err => setErrors(prev => ({ ...prev, [key]: err instanceof Error ? err.message : "暂时无法读取依据。" })));
  };
  const statusText = (value: unknown) => value === "PASS" ? "已定位到原文" : value === "EVIDENCE_NOT_AVAILABLE" ? "当前无法读取该位置的原文" : "证据状态待确认";
  const metaValue = (value: unknown) => value === true ? "可调阅" : value === false ? "未记录" : "未记录";
  if (!refs.length) return <div className="meta-stack">
    <Metric label="证据状态" value={evidenceLabel} />
    <p className="read-only-note">{hasSources ? "来源可追溯 · 暂无精确定位" : "来源依据不可证明"}</p>
  </div>;
  return <div className="meta-stack">
    <Metric label="证据状态" value={evidenceLabel} />
    <p className="section-label">这条知识记录了 {refs.length} 处依据，可逐条查看对应原文位置。</p>
    <div className="evidence-actions">{refs.map((ref, index) => {
      const key = `${ref.sourceVersionId}|${ref.locator}`;
      return <button key={key} className={`evidence-action${openKey === key ? " evidence-action-open" : ""}`} onClick={() => toggle(ref)} aria-expanded={openKey === key}>{`依据 ${index + 1}`}</button>;
    })}</div>
    {openKey ? (() => {
      if (errors[openKey]) return <div className="evidence-panel"><p className="read-only-note">{errors[openKey]}</p></div>;
      const res = results[openKey];
      if (!res) return <div className="evidence-panel"><Loading label="正在读取依据…" skeleton /></div>;
      if (String(res.status ?? "") !== "PASS") return <div className="evidence-panel">
        <p className="read-only-note">来源可追溯 · 暂无精确定位</p>
        <div className="meta-stack"><Metric label="依据状态" value={statusText(res.status)} /></div>
      </div>;
      const excerpt = res.excerpt == null ? "" : String(res.excerpt);
      let quoted: string | null = null;
      try { const parsed = JSON.parse(excerpt); if (parsed && typeof parsed === "object" && typeof (parsed as RecordMap).text === "string" && String((parsed as RecordMap).text).trim()) quoted = String((parsed as RecordMap).text); } catch { quoted = null; }
      return <div className="evidence-panel">
        <div className="meta-stack">
          <Metric label="依据状态" value="已定位到原文" />
          <Metric label="来源资料" value={String(res.source_document_name_label ?? "未记录关联资料")} />
          <Metric label="来源版本" value={String(res.source_version_id ?? "未记录")} />
          <Metric label="原文位置" value={String(res.locator ?? "未记录")} />
          <Metric label="内容类型" value={String(res.block_type ?? "未记录")} />
          <Metric label="身份校验" value={res.identity_verification === "READER_METADATA_VERIFIED" ? "已校验" : "未记录"} />
          <Metric label="原始材料" value={metaValue(res.raw_artifact_available)} />
        </div>
        {String(res.source_document_name_state ?? "") !== "RECORDED" ? <p className="read-only-note">这份依据没有记录来源文档名称，因此只显示可核对的来源版本与原文位置。</p> : null}
        {quoted ? <div className="evidence-quote"><span className="section-label">引用原文</span><p>{quoted}</p></div> : null}
        <div className="evidence-quote"><span className="section-label">原始定位内容</span><pre className="draft-provenance">{excerpt || "（该位置没有可显示的文本）"}</pre></div>
      </div>;
    })() : null}
  </div>;
}

function KnowledgeDetail({ id, navigate }: {id: string; navigate: (path: string) => void}) { const resource = useResource(() => client.knowledgeItem(id), [id]); const projection = useResource(() => client.knowledgeProjection(id), [id]); const [techOpen, setTechOpen] = useState(false); return <div className="page-stack"><button className="back-button" onClick={() => navigate("/knowledge")}>‹ 返回知识库</button><StateBoundary resource={resource} empty={<Empty message="找不到这条知识。" />}>{data => { if (!data) return <Empty message="找不到这条知识。" />; const page = data.page as Knowledge; const revision = data.latest_revision as RecordMap | null; const content = (revision?.content_json && typeof revision.content_json === "object" ? revision.content_json : {}) as RecordMap; const proj = (projection.data ?? null) as RecordMap | null; const presentation = buildKnowledgePresentation(content, page.oneLineSummary ?? null); const authorityLabel = String(proj?.authority_label ?? humanStatus(page.status)); const evidenceLabel = String(proj?.evidence_label ?? "证据状态待确认"); const validTime = (proj?.valid_time as RecordMap | undefined) ?? null; const validTimeDisclosure = proj?.valid_time_disclosure ? String(proj.valid_time_disclosure) : null; const revisions = Array.isArray(proj?.revisions) ? proj.revisions as RecordMap[] : []; const relatedProjects = Array.isArray(proj?.related_projects) ? proj.related_projects as RecordMap[] : []; const provisional = proj?.provisional_reason ? String(proj.provisional_reason) : null; const sources = Array.isArray(data.sources) ? data.sources as RecordMap[] : []; const latestAt = revisions.length ? String(revisions[0].created_at ?? "") : ""; return <><PageHeader eyebrow={`${humanKnowledgeType(page.knowledgeType)} · ${authorityLabel}`} title={page.title} description={presentation.summary ?? "暂无摘要"} /><div className="knowledge-detail-layout"><main><DetailSection title="知识内容">{presentation.mainGroups.length ? <div className="knowledge-content">{presentation.mainGroups.map(group => <section key={group.key}><h3>{group.label}</h3>{group.items.map((item, index) => <p key={index}>{item.text}</p>)}</section>)}</div> : <Empty message="这个知识条目还没有可展示的正文。" />}</DetailSection>{presentation.optionalGroups.map(group => <DetailSection key={group.key} title={group.label}><div className="knowledge-content">{group.items.map((item, index) => <p key={index}>{item.text}</p>)}</div></DetailSection>)}{revisions.length > 1 && <DetailSection title="历史版本"><div className="meta-stack">{revisions.map(rev => <div key={String(rev.revision_id)} className="revision-row"><span className={`revision-state ${rev.is_current ? "revision-current" : "revision-historical"}`}>{String(rev.state_label)}</span><span>v{String(rev.revision_no)}</span><span className="revision-date">{dateText(String(rev.created_at ?? ""))}</span></div>)}{validTimeDisclosure && <p className="disclosure-note">历史有效期信息不完整</p>}</div></DetailSection>}{presentation.ambiguous.length > 0 && <DetailSection title="其他结构化信息"><p className="section-label">这些内容包含系统处理信息，保留原文以便核对。</p><div className="meta-stack">{presentation.ambiguous.map((entry, index) => <div className="tech-entry" key={`${entry.key}-${index}`}><span className="section-label">{entry.label}</span><p className="tech-entry-text">{entry.text}</p></div>)}</div></DetailSection>}</main><aside><DetailSection title="权威与状态"><div className="meta-stack"><Metric label="权威状态" value={authorityLabel} />{provisional && <Metric label="来源依据" value="来源依据不完整" />}<Metric label="最近更新" value={latestAt ? dateText(latestAt) : "未记录"} /><Metric label="业务生效时间" value={validTime ? `${String(validTime.valid_from ?? "起")} → ${validTime.valid_to ? String(validTime.valid_to) : "至今"}` : (validTimeDisclosure ?? "未记录明确业务生效时间")} /></div></DetailSection><DetailSection title="关联项目">{relatedProjects.length ? <div className="meta-stack">{relatedProjects.map(p => <button key={String(p.project_id)} className="text-button" onClick={() => navigate(`/projects/${encodeURIComponent(String(p.project_id))}`)}>{String(p.name)}</button>)}</div> : <Empty message="暂无关联项目。" />}</DetailSection><DetailSection title="来源与证据"><EvidenceSection refs={presentation.evidenceRefs} evidenceLabel={evidenceLabel} hasSources={sources.length > 0 || (Array.isArray(content.source_version_ids) && (content.source_version_ids as unknown[]).length > 0) || (Array.isArray(proj?.source_version_ids) && (proj?.source_version_ids as unknown[]).length > 0)} />{sources.length ? <div className="meta-stack source-list">{sources.map((item, index) => <div className="detail-list-row" key={String(item.id ?? index)}><strong>{String(item.title ?? item.name ?? item.summary ?? "未命名资料")}</strong><small>{humanSourceCategory(typeof item.category === "string" ? item.category : undefined)}</small></div>)}</div> : <p className="read-only-note">暂无关联来源。</p>}</DetailSection></aside></div><DetailSection title="来源与技术信息"><p className="project-explainer-layer-note">普通阅读不需要打开这一层。这里保留系统处理信息与原始记录，便于核对。</p><button className="text-button handbook-toggle" onClick={() => setTechOpen(o => !o)} aria-expanded={techOpen}>{techOpen ? "收起来源与技术信息" : "展开来源与技术信息"}</button>{techOpen && <div className="tech-drawer">{presentation.technical.map((entry, index) => <div className="tech-entry" key={`${entry.key}-${index}`}><span className="section-label">{entry.label}</span><p className="tech-entry-text">{entry.text}</p></div>)}<div className="tech-entry"><span className="section-label">原始记录</span><pre className="draft-provenance">{JSON.stringify(content, null, 2)}</pre></div></div>}</DetailSection></>; }}</StateBoundary></div>; }

function DraftDetail({ id, navigate }: {id: string; navigate: (path: string) => void}) {
  const resource = useResource(() => client.draft(id), [id]);
  const renderItems = (items?: unknown[]) => items?.length ? <div className="draft-json-list">{items.map((item, index) => <article key={index}><p>{readableContent(item)}</p></article>)}</div> : <Empty message="暂无内容。" />;
  return <div className="page-stack"><button className="back-button" onClick={() => navigate("/knowledge")}>‹ 返回知识库</button><StateBoundary resource={resource} empty={<Empty message="找不到这条待确认草稿。" />}>{draft => draft ? <><PageHeader eyebrow={`待确认草稿 · ${humanDraftStatus(draft.status)}`} title={draft.sourceTitle || "未命名草稿"} description={draft.summary || "这是自动整理出的待确认内容，还没有正式确认，不能作为正式依据。"} action={<StatusBadge label="非 Canonical" tone="warning" />} /><div className="knowledge-detail-layout"><main><DetailSection title="摘要"><p>{draft.summary || "暂无摘要。"}</p></DetailSection><DetailSection title="关键事实">{renderItems(draft.facts)}</DetailSection><DetailSection title="指标信号">{renderItems(draft.metrics)}</DetailSection><DetailSection title="时间线与决策">{renderItems(draft.timeline)}</DetailSection><DetailSection title="风险">{renderItems(draft.risks)}</DetailSection><DetailSection title="歧义与冲突">{renderItems(draft.ambiguities)}</DetailSection></main><aside><DetailSection title="草稿状态"><div className="meta-stack"><Metric label="状态" value={humanDraftStatus(draft.status)} /><Metric label="权威级别" value="draft" /><Metric label="来源版本" value={draft.sourceVersionId} /><Metric label="关联项目" value={draft.projectName || "未关联"} /><Metric label="最近更新" value={dateText(draft.updatedAt)} /></div></DetailSection><DetailSection title="证据与溯源">{renderItems(draft.evidenceRefs)}<pre className="draft-provenance">{readableContent(draft.provenance)}</pre></DetailSection></aside></div></> : <Empty message="找不到这条待确认草稿。" />}</StateBoundary></div>;
}

function SourcesPage({ navigate, basePath = "/sources", backPath = "/knowledge" }: {navigate: (path: string) => void; basePath?: string; backPath?: string}) {
  const resources = useResource(() => client.sources(), []);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const sourceItems = resources.data ?? [];
  const items = sourceItems.filter(item => `${item.title} ${item.category ?? ""} ${item.wpsDocumentId ?? ""}`.toLowerCase().includes(search.toLowerCase())).filter(item => category === "all" || item.category === category);
  const categories = Array.from(new Set(sourceItems.map(s => s.category).filter(Boolean))) as string[];
  return <div className="page-stack">
    {backPath && <button className="back-button" onClick={() => navigate(backPath)}>‹ 返回知识库</button>}
    <PageHeader eyebrow="Source Registry" title="资料索引" description="这里保存原始资料的位置和状态，不等同于已经沉淀的知识。" illustration="sources" />
    <StateBoundary resource={resources}>{() => <>
      <div className="source-toolbar"><input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索文件名或分类" aria-label="搜索资料" /><select value={category} onChange={e => setCategory(e.target.value)} aria-label="按分类筛选"><option value="all">全部分类</option>{categories.map(item => <option key={item} value={item}>{humanSourceCategory(item)}</option>)}</select></div>
      {items.length > 0 && <div className="source-grid">{items.map(item => <button className="source-card" key={item.id} onClick={() => navigate(`${basePath}/${item.id}`)}><strong>{item.title}</strong><span>{humanSourceCategory(item.category)}</span><small>{item.wpsDocumentId ? "已关联 WPS 资料" : "尚未关联 WPS 资料"}</small><StatusBadge label={item.isCompiled ? "已沉淀为知识" : "尚未沉淀为知识"} tone={item.isCompiled ? "success" : "neutral"} /><small>更新于 {dateText(item.updatedAt)}</small><span className="chevron"><ChevronRight size={16} strokeWidth={1.7} aria-hidden="true" /></span></button>)}</div>}
      {items.length === 0 && <Empty message={sourceItems.length ? "没有符合条件的资料。" : "资料索引暂时为空。"} />}
    </>}</StateBoundary>
  </div>;
}
function SourceDetail({ id, navigate, backPath = "/sources" }: {id: string; navigate: (path: string) => void; backPath?: string}) { const resource = useResource(() => client.source(id), [id]); return <div className="page-stack"><button className="back-button" onClick={() => navigate(backPath)}>‹ 返回资料索引</button><StateBoundary resource={resource} empty={<Empty message="找不到这条资料索引。" />}>{source => source ? <><PageHeader eyebrow={`原始资料 · ${humanSourceCategory(source.category)}`} title={source.title} description={source.isCompiled ? "该资料已有知识沉淀。" : "该资料尚未沉淀为知识，仅作为事实来源保存。"} /><div className="meta-grid"><Metric label="资料状态" value={humanStatus(source.status)} /><Metric label="WPS 文档" value={source.wpsDocumentId ? "已关联（按需读取）" : "未关联"} /><Metric label="最近更新" value={dateText(source.updatedAt)} /></div><section className="card source-content-card"><h2>按需读取内容</h2>{source.content ? <pre>{source.content}</pre> : <Empty message="暂时无法读取正文，资料索引仍然保留。" />}</section></> : <Empty message="找不到这条资料索引。" />}</StateBoundary></div>; }

function ReviewPage({ navigate, backTo, initialCandidateId }: {navigate: (path: string) => void; backTo?: string; initialCandidateId?: string}) {
  const [status, setStatus] = useState<"pending" | "accepted" | "rejected">("pending");
  const list = useResource(() => client.reviewCandidates(status), [status]);
  const [selected, setSelected] = useState<string | null>(initialCandidateId ?? null);
  const [dismissed, setDismissed] = useState<Record<string, true>>({});
  const detail = useResource(() => selected ? client.reviewCandidate(selected) : Promise.resolve(undefined), [selected]);
  const reload = () => { list.retry(); if (selected) detail.retry(); };
  const visible = (list.data ?? []).filter(item => {
    if (status === "pending" && dismissed[item.candidateId]) return false;
    // R-268: the shipped bundle keeps a data/metric card visible even when its
    // effective state is not NEEDS_REVIEW, so the source does the same.
    if (status === "pending" && item.effectiveReviewState && item.effectiveReviewState !== "NEEDS_REVIEW" && !isDataTargetProposal(item.proposedChange)) return false;
    return true;
  });
  const markSubmitted = (candidateId: string) => {
    setDismissed(prev => ({ ...prev, [candidateId]: true }));
    reload();
  };
  return <div className="page-stack review-page"><PageHeader eyebrow="AI 候选确认" title="待确认" description="审阅 AI 分析产生的候选内容。接受不等于写入，任何写入都需要你显式确认。" illustration="review" action={backTo ? <button className="secondary-button" onClick={() => navigate(backTo)}>‹ 返回今日</button> : undefined} /><div className="review-tabs" role="tablist" aria-label="候选状态">{([["pending", "待确认"], ["accepted", "方向已确认 / 待写入"], ["rejected", "已拒绝"]] as const).map(([key, label]) => <button key={key} role="tab" aria-selected={status === key} className={status === key ? "active" : ""} onClick={() => { setStatus(key); setSelected(null); }}>{label}</button>)}</div><div className={`review-layout ${selected ? "has-selected" : ""}`}><section className="review-list-panel"><StateBoundary resource={list}>{() => visible.length ? visible.map(item => <ReviewCard key={item.candidateId} candidate={item} active={selected === item.candidateId} onClick={() => setSelected(item.candidateId)} />) : <Empty message={status === "pending" ? "暂无待确认内容。" : status === "accepted" ? "暂无已接受内容。" : "暂无已拒绝内容。"} />}</StateBoundary></section><section className="review-detail-panel">{selected ? <StateBoundary resource={detail} empty={<Loading label="正在加载详情…" skeleton />}>{item => item ? <ReviewDetailView detail={item} submittedLocally={!!dismissed[item.candidateId]} onDone={reload} onSubmitted={() => markSubmitted(item.candidateId)} navigate={navigate} onBack={() => setSelected(null)} /> : null}</StateBoundary> : <Empty message="从左侧选择一条候选内容开始查看。" />}</section></div></div>;
}
function ReviewCard({ candidate, active, onClick }: {candidate: ReviewCandidate; active: boolean; onClick: () => void}) { const presentation = candidate.presentation ?? {}; return <button className={`review-card ${active ? "active" : ""}`} onClick={onClick} aria-pressed={active}><div className="review-card-head"><StatusBadge label={mappedLabel(presentation.category_label, {}, humanType(candidate.candidateType))} tone="neutral" />{presentation.confidence_label && <StatusBadge label={mappedLabel(presentation.confidence_label, { HIGH: "高置信度", MEDIUM: "中等置信度", LOW: "低置信度" }, "置信度待确认")} tone="neutral" />}{presentation.review_mode_label && <StatusBadge label={humanMode(presentation.review_mode)} tone="warning" />}</div><strong>{humanReviewTitle(presentation.display_title, candidate.candidateType)}</strong><p>{presentation.situation || "这条审核信息暂不能进行确认。"}</p><footer><span>{humanSourceLabel(presentation.source_label || candidate.sourceLabel)}</span><time>{dateText(candidate.createdAt, true)}</time></footer></button>; }

function ReviewDetailView({ detail, onDone, onSubmitted, submittedLocally = false, navigate, onBack }: {detail: ReviewDetail; onDone: () => void; onSubmitted?: () => void; submittedLocally?: boolean; navigate: (path: string) => void; onBack: () => void}) {
  const pres = detail.presentation ?? {};
  const effective = detail.effectiveReviewState || (detail.status === "rejected" ? "REJECTED" : detail.status === "accepted" ? "REVIEWED_WAITING_GPT" : "NEEDS_REVIEW");
  const waitingGpt = submittedLocally || effective === "REVIEWED_WAITING_GPT";
  const clarification = detail.candidateType === "ambiguity" || detail.candidateType === "question";
  const conflict = detail.candidateType === "conflict";
  const proposal = safeJson(detail.proposedChange);
  const originalProposal = safeJson(proposal.original_payload ?? proposal);
  const proposalTargetType = proposalTargetTypeOf(detail.proposedChange);
  const proposalPayload = safeJson(proposal.reviewed_payload ?? originalProposal.payload ?? originalProposal);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [draft, setDraft] = useState<RecordMap>(() => proposalPayload);
  useEffect(() => setDraft(proposalPayload), [detail.candidateId, detail.proposedChange]);
  const actions = async (action: () => Promise<unknown>, success: string, leavePending = false) => {
    setBusy(true);
    setError("");
    try {
      await action();
      setMessage(success);
      if (!leavePending) onSubmitted?.();
      onDone();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const dataCard = ["data", "metric"].includes(String(detail.candidateType)) || ["data", "metric"].includes(proposalTargetType);
  const accept = () => actions(async () => {
    if (dataCard) {
      if (!String(draft.title ?? "").trim() || !String(draft.project_name ?? draft.project_id ?? "").trim()) {
        throw new Error("请补充标题和项目名称后再接受。");
      }
      await client.editCandidate(detail.candidateId, draft);
    }
    return client.acceptCandidate(detail.candidateId, note.trim() || undefined);
  }, "已提交，等待 AI 巡航。")
  const reject = () => actions(() => client.rejectCandidate(detail.candidateId), "已拒绝，审核记录已保留。");
  const apply = () => actions(() => client.applyCandidate(detail.candidateId), "已写入工作台。", true);
  const saveDraft = () => actions(() => client.editCandidate(detail.candidateId, draft), "草稿已保存，尚未接受，也未写入工作台。", true);
  const clarify = () => actions(() => client.clarifyCandidate(detail.candidateId, note.trim()), "已提交，等待 AI 巡航。");
  const ignore = () => actions(() => client.ignoreCandidate(detail.candidateId), "已暂时忽略。");
  const chooseConflict = (option: "A" | "B" | "NEITHER") => actions(() => client.resolveConflict(detail.candidateId, option, note.trim() || undefined), "已提交，等待 AI 巡航。");
  const fields = [...new Set([...Object.keys(draft).filter(key => ["title", "description", "due_at", "priority", "project_id", "project_name", "statement"].includes(key)), ...(dataCard ? ["title", "project_name"] : [])])];
  const fieldLabels: Record<string, string> = { title: "标题", description: "说明", due_at: "截止日期", priority: "优先级", project_id: "项目", project_name: "项目名称", statement: "内容" };
  const writeIntent = dataCard || ["todo", "knowledge", "project_update"].includes(String(detail.candidateType));
  const needsHumanInput = !waitingGpt && effective === "NEEDS_REVIEW";
  return <div className="review-detail">
    <div className="review-detail-toolbar"><button className="secondary-button" onClick={onBack}>‹ 返回列表</button><button className="text-button" onClick={onDone}>刷新</button></div>
    <div className="review-title-block"><h2>{humanReviewTitle(pres.display_title, detail.candidateType)}</h2><div className="badge-row"><StatusBadge label={mappedLabel(pres.category_label, {}, humanType(detail.candidateType))} /><StatusBadge label={humanReviewState(waitingGpt ? "REVIEWED_WAITING_GPT" : effective)} tone={effective === "REJECTED" ? "danger" : "warning"} /></div></div>
    {waitingGpt && <p className="review-note review-note-success">已提交，等待 AI 巡航。用户审核已保存；内部重分析失败也不会再次询问同一问题。</p>}
    {clarification && needsHumanInput && <p className="review-note">这条内容需要你补充说明，你的说明将作为更高优先级事实参与 AI 巡航重分析。</p>}
    <ReviewSection title="Agy 发现了什么"><p className="review-lead">{pres.situation || "审核信息不足，暂不能进行确认。"}</p></ReviewSection>
    <ReviewSection title="需要你确认什么"><p className="review-question">{pres.decision_question || "请根据页面列出的事实作出决定。"}</p>{pres.recommendation && <p>建议：{pres.recommendation}</p>}</ReviewSection>
    <ReviewSection title="原始来源 / 证据摘要"><p><strong>{humanSourceLabel(pres.source_label || detail.sourceLabel)}</strong></p>{(pres.evidence_items || []).map((item, i) => <p key={i}>{item}</p>)}</ReviewSection>
    {clarification && needsHumanInput && <ReviewSection title="补充说明"><textarea rows={4} value={note} onChange={e => setNote(e.target.value)} placeholder="请补充事实、上下文或你希望系统继续核对的内容。" /><div className="review-action-bar"><button className="secondary-button" onClick={() => void ignore()} disabled={busy}>暂时忽略</button><button className="primary-button" onClick={() => void clarify()} disabled={busy || !note.trim()}>提交说明</button></div></ReviewSection>}
    {conflict && needsHumanInput && <ReviewSection title="冲突处理"><textarea rows={3} value={note} onChange={e => setNote(e.target.value)} placeholder="请写明正确事实或处理规则。" /><div className="review-action-bar"><button className="primary-button" onClick={() => void chooseConflict("NEITHER")} disabled={busy || !note.trim()}>提交处理说明</button></div></ReviewSection>}
    {writeIntent && needsHumanInput && detail.status === "pending" && !clarification && !conflict && fields.length > 0 && <ReviewSection title="编辑草稿"><p className="muted">编辑只会保存候选草稿，不会写入工作台。</p><div className="review-edit-grid">{fields.map(field => <label key={field}><span>{fieldLabels[field] ?? "补充信息"}</span><input aria-label={fieldLabels[field] ?? "补充信息"} value={String(draft[field] ?? "")} onChange={e => setDraft(prev => ({ ...prev, [field]: e.target.value }))} /></label>)}</div><button className="secondary-button" onClick={() => void saveDraft()} disabled={busy}>保存草稿</button></ReviewSection>}
    {writeIntent && needsHumanInput && detail.status === "pending" && <ReviewSection title="审核意见（可选）"><textarea rows={3} value={note} onChange={e => setNote(e.target.value)} placeholder="告诉 AI 巡航你希望保留的意图、项目归属或处理方式。" /></ReviewSection>}
    {error && <div className="state-message state-message-error" role="alert"><p>{error}</p></div>}{message && <p className="review-note review-note-success" role="status">{message}</p>}
    <div className="review-action-bar">{needsHumanInput && detail.status === "pending" && !clarification && !conflict && <><button className="danger-button" disabled={busy} onClick={() => void reject()}>拒绝</button><button className="primary-button" disabled={busy} onClick={() => void accept()}>{busy ? "处理中…" : "接受"}</button></>}{effective === "ACCEPTED_WAITING_COMMIT" && <button className="primary-button" disabled={busy} onClick={() => void apply()}>写入工作台</button>}{waitingGpt && <button className="secondary-button" onClick={() => navigate("/review")}>返回审核列表</button>}</div>
  </div>;
}

function ReviewSection({ title, children, className = "" }: {title: string; children: ReactNode; className?: string}) { return <section className={`review-section ${className}`}><h3>{title}</h3>{children}</section>; }

function AgnesPage() {
  type AgnesChatMessage = { id: string; role: "user" | "agy"; text: string; failed?: boolean; sources?: string[] };
  type AgnesChatSummary = { session_id: string; title: string; updated_at: string; message_count: number };
  const LAST_SESSION_KEY = "agnes.lastSessionId";
  const [messages, setMessages] = useState<AgnesChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [chats, setChats] = useState<AgnesChatSummary[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [restoring, setRestoring] = useState(true);

  const rememberSession = (sid: string | null) => {
    try { if (sid) localStorage.setItem(LAST_SESSION_KEY, sid); else localStorage.removeItem(LAST_SESSION_KEY); } catch { /* storage unavailable */ }
  };

  const refreshChats = useCallback(async () => {
    try { const list = await client.listAgnesChats(); setChats(list); return list; }
    catch { return [] as AgnesChatSummary[]; }
  }, []);

  const loadChat = useCallback(async (sid: string) => {
    try {
      const detail = await client.getAgnesChat(sid);
      setSessionId(sid);
      rememberSession(sid);
      setMessages(detail.messages.filter(m => m.role === "user" || m.role === "agy").map(m => ({ id: m.message_id, role: m.role === "user" ? "user" : "agy", text: m.content, failed: m.status === "failed" })));
    } catch { setError("历史对话载入失败，请重试。"); }
  }, []);

  // Mount: restore the server-authoritative conversation.  Deliberately never
  // creates a session — a new chat only starts on an explicit user action.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const list = await refreshChats();
      let target: string | null = null;
      try { const last = localStorage.getItem(LAST_SESSION_KEY); if (last && list.some(c => c.session_id === last)) target = last; } catch { /* ignore */ }
      if (!target && list.length) target = list[0].session_id;
      if (target && !cancelled) await loadChat(target);
      if (!cancelled) setRestoring(false);
    })();
    return () => { cancelled = true; };
  }, [loadChat, refreshChats]);

  const newChat = () => { setMessages([]); setSessionId(null); rememberSession(null); setError(""); };

  const openChat = async (sid: string) => {
    if (busy) return;
    setHistoryOpen(false);
    setError("");
    setRestoring(true);
    await loadChat(sid);
    setRestoring(false);
  };

  const send = async (text: string, retryId?: string) => {
    const value = text.trim();
    if (!value || busy) return;
    setBusy(true);
    setError("");
    const id = retryId || crypto.randomUUID();
    if (!retryId) { setMessages(prev => [...prev, { id, role: "user", text: value }]); setInput(""); }
    try {
      const sid = sessionId || (await client.createAgnesChat()).session_id;
      if (!sessionId) { setSessionId(sid); rememberSession(sid); }
      // The same client id is reused on retry so the server updates the existing
      // user row instead of storing a duplicate.
      const response = await client.sendAgnesMessage(sid, value, id);
      // R-267: the answer carries the documents behind each cited item, so the
      // "查看来源" panel can show the original file next to the knowledge page.
      const answerSources = Array.isArray((response as { sources?: unknown }).sources) ? (response as { sources: string[] }).sources : [];
      setMessages(prev => [...prev.map(msg => msg.id === id ? { ...msg, failed: false } : msg), { id: crypto.randomUUID(), role: "agy", text: response.response, sources: answerSources }]);
      await refreshChats();
    } catch (e) {
      setMessages(prev => prev.map(msg => msg.id === id ? { ...msg, failed: true } : msg));
      setError(errorMessage(e));
    } finally { setBusy(false); }
  };

  const chatDate = (iso: string) => {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const same = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    const now = new Date();
    if (same(d, now)) return "今天";
    if (same(d, new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1))) return "昨天";
    return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };

  return <div className="page-stack agy-page">
    <PageHeader eyebrow="Workspace Copilot" title="Antigravity" description="工作台助手 · 基于当前工作台状态回答你的问题，不会自动写入任何数据。" illustration="agy" action={<span className="agy-header-actions"><button className="text-button" onClick={() => setHistoryOpen(true)} disabled={busy}>历史对话</button><button className="text-button" onClick={newChat} disabled={busy}>新对话</button></span>} />
    <div className="agy-conversation" aria-live="polite">
      {restoring && <div className="agy-empty"><p>正在载入对话…</p></div>}
      {!restoring && !messages.length && !busy && <div className="agy-empty"><p>可以试试：</p><div>{["今天有什么需要处理？", "我有哪些正在进行的项目？", "最近记录了什么？", "有哪些待确认内容？"].map(prompt => <button className="agy-suggestion" key={prompt} onClick={() => void send(prompt)}>{prompt}</button>)}</div></div>}
      {messages.map(msg => <div className={`agy-bubble-row ${msg.role === "user" ? "user" : "agy"}`} key={msg.id}><div className={`agy-bubble ${msg.failed ? "failed" : ""}`}>{msg.role === "user" ? <p>{msg.text}</p> : <MessageText text={msg.text} />}{msg.role !== "user" && (msg.sources?.length ?? 0) > 0 && <details className="agy-sources"><summary>查看来源</summary><ul>{msg.sources!.map((source, index) => <li key={`${msg.id}-${index}`}>{source}</li>)}</ul></details>}{msg.failed && <button className="text-button" onClick={() => void send(msg.text, msg.id)}>重试</button>}</div></div>)}
      {busy && <div className="agy-bubble-row agy"><div className="agy-bubble agy-thinking">Agy 正在分析工作台数据…</div></div>}
      {error && <div className="state-message state-message-error" role="alert"><p>{error}</p><button className="text-button" onClick={() => setError("")}>关闭</button></div>}
    </div>
    <form className="agy-composer" onSubmit={e => { e.preventDefault(); void send(input); }}>
      <textarea value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(input); } }} placeholder="问问 Agy，例如：今天有哪些待办？" rows={3} disabled={busy} />
      <button className="primary-button" disabled={busy || !input.trim()}>{busy ? "发送中…" : "发送"}</button>
    </form>
    {historyOpen && <>
      <div className="agy-history-scrim" onClick={() => setHistoryOpen(false)} aria-hidden="true" />
      <aside className="agy-history-drawer" aria-label="历史对话">
        <div className="agy-history-head"><span>历史对话</span><button className="text-button" onClick={() => setHistoryOpen(false)}>关闭</button></div>
        <button className="agy-history-new" onClick={() => { newChat(); setHistoryOpen(false); }}>新对话</button>
        {chats.length ? <ul className="agy-history-list">{chats.map(chat => <li key={chat.session_id}><button className={`agy-history-item ${chat.session_id === sessionId ? "current" : ""}`} onClick={() => void openChat(chat.session_id)}><span className="agy-history-title">{chat.title || "新对话"}</span><span className="agy-history-time">{chatDate(chat.updated_at)}</span></button></li>)}</ul> : <p className="agy-history-empty">还没有历史对话</p>}
        <p className="agy-history-foot">对话仅保留最近 7 天</p>
      </aside>
    </>}
  </div>;
}
function MessageText({ text }: {text: string}) { return <>{text.split(/\n{2,}/).filter(Boolean).map((paragraph, index) => <p key={index}>{paragraph.split("\n").map((line, i) => <span key={i}>{line}{i < paragraph.split("\n").length - 1 && <br />}</span>)}</p>)}</>; }

function SystemPage() { const resource = useResource(() => client.status(), []); return <div className="page-stack"><PageHeader eyebrow="连接与诊断" title="系统" description="各层状态彼此独立；本地集成不可用不会影响云端工作空间。" /><StateBoundary resource={resource}>{data => <div className="system-grid"><IntegrationCard title="云端工作空间" state={data.cloudHub} detail="负责工作台数据和分析服务。" /><IntegrationCard title="Windows Bridge" state={data.windowsBridge} detail="负责办公电脑的可选连接。" /><IntegrationCard title="WPS 资料读取" state={data.wpsAvailable} detail={data.wpsProvider ? `适配器：${humanProvider(data.wpsProvider)}` : "可选的只读资料读取能力。"} /><IntegrationCard title="服务器资源" state="online" detail={`内存使用 ${data.serverMetrics?.ram ?? "—"}% · 磁盘使用 ${data.serverMetrics?.disk ?? "—"}%`} /></div>}</StateBoundary></div>; }
function IntegrationCard({ title, state, detail }: {title: string; state?: string; detail: string}) { const online = ["online", "available", "ready"].includes(state ?? ""); return <section className="card integration-card"><div className="integration-heading"><span className={`service-dot ${online ? "online" : ""}`} /><h2>{title}</h2><StatusBadge label={online ? "在线" : state === "quota_limited" ? "配额受限" : "暂不可用"} tone={online ? "success" : state === "quota_limited" ? "warning" : "danger"} /></div><p>{detail}</p></section>; }
function MorePage({ navigate, username, onLogout }: {navigate: (path: string) => void; username: string; onLogout: () => void}) { return <div className="page-stack"><PageHeader eyebrow="其他功能" title="更多" description={`当前账户：${username}`} /><div className="ios-list"><button className="ios-list-item" onClick={() => navigate("/daily")}><span><strong>日记</strong><small>按日期查看每日回顾</small></span><span className="chevron"><ChevronRight size={16} strokeWidth={1.7} aria-hidden="true" /></span></button><button className="ios-list-item" onClick={() => navigate("/settings/system")}><span><strong>系统</strong><small>查看云端与办公电脑集成状态</small></span><span className="chevron"><ChevronRight size={16} strokeWidth={1.7} aria-hidden="true" /></span></button><button className="ios-list-item" onClick={onLogout}><span><strong>退出登录</strong><small>结束当前工作台会话</small></span><span className="chevron"><ChevronRight size={16} strokeWidth={1.7} aria-hidden="true" /></span></button></div></div>; }

function LoginPage({ onLogin }: {onLogin: (username: string, password: string) => Promise<void>}) { const [username, setUsername] = useState(""); const [password, setPassword] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false); async function submit(e: FormEvent) { e.preventDefault(); setBusy(true); setError(""); try { await onLogin(username.trim(), password); } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); } } return <main className="login-page"><section className="login-card"><div className="login-brand"><PawLogo size={26} /></div><p className="page-eyebrow">个人 AI 工作台</p><h1>欢迎回来</h1><p>登录后继续访问你的记录、待办、项目和知识库。</p><form onSubmit={submit} className="login-form"><label><span>用户名</span><input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required /></label><label><span>密码</span><input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>{error && <p className="login-error" role="alert">{error}</p>}<button className="primary-button" disabled={busy}>{busy ? "正在登录…" : "登录"}</button></form><small className="login-security">使用安全 Cookie 保持登录状态，密码不会保存在浏览器中。</small></section></main>; }

function NotFound({ navigate }: {navigate: (path: string) => void}) { return <div className="not-found"><p className="page-eyebrow">404</p><h1>找不到页面</h1><p>这个地址不存在或已经移动。</p><button className="primary-button" onClick={() => navigate("/today")}>返回今日</button></div>; }

function renderPage(route: Route, navigate: (path: string) => void, onCapture: () => void, username: string, onLogout: () => void) {
  const { path } = route; if (path === "/" || path === "/today") return <TodayPage navigate={navigate} onCapture={onCapture} />;
  if (path === "/daily") return <DailyPage route={route} navigate={navigate} />;
  if (path === "/todos") return <TodosPage navigate={navigate} />;
  if (path === "/projects") return <ProjectsPage navigate={navigate} />; if (path.startsWith("/projects/")) return <ProjectDetail id={decodeURIComponent(path.slice("/projects/".length))} navigate={navigate} />;
  if (path === "/knowledge") return <KnowledgePage navigate={navigate} />; if (path.startsWith("/knowledge/")) return <KnowledgeDetail id={decodeURIComponent(path.slice("/knowledge/".length))} navigate={navigate} />;
  if (path.startsWith("/drafts/")) return <DraftDetail id={decodeURIComponent(path.slice("/drafts/".length))} navigate={navigate} />;
  if (path === "/files" || path === "/sources") return <SourcesPage navigate={navigate} basePath={path} backPath={path === "/files" ? "/more" : "/knowledge"} />; if (path.startsWith("/files/") || path.startsWith("/sources/")) return <SourceDetail id={decodeURIComponent(path.split("/").slice(2).join("/"))} navigate={navigate} backPath={path.startsWith("/files/") ? "/files" : "/sources"} />;
  if (path === "/review") return <ReviewPage navigate={navigate} backTo="/today" />; if (path.startsWith("/review/")) return <ReviewPage navigate={navigate} initialCandidateId={decodeURIComponent(path.slice("/review/".length))} />;
  if (path === "/agnes") return <AgnesPage />; if (path === "/settings/system") return <SystemPage />; if (path === "/more") return <MorePage navigate={navigate} username={username} onLogout={onLogout} />;
  return <NotFound navigate={navigate} />;
}

export function App() { const { path, query, navigate } = useRoute(); const [session, setSession] = useState<{username: string} | null>(null); const [checking, setChecking] = useState(true); const [captureOpen, setCaptureOpen] = useState(false); useEffect(() => { const unauthorized = () => setSession(null); window.addEventListener("paw:unauthorized", unauthorized); client.session().then(data => setSession({ username: data.username })).catch(() => setSession(null)).finally(() => setChecking(false)); return () => window.removeEventListener("paw:unauthorized", unauthorized); }, []); const login = async (username: string, password: string) => { const data = await client.login(username, password); setSession({ username: data.username }); }; const logout = async () => { try { await client.logout(); } finally { setSession(null); navigate("/today"); } }; if (checking) return <Loading label="正在检查登录状态…" skeleton />; if (!session) return <LoginPage onLogin={login} />; return <AppShell username={session.username} path={path} navigate={navigate} onLogout={() => void logout()} onCapture={() => setCaptureOpen(true)}>{renderPage({ path, query }, navigate, () => setCaptureOpen(true), session.username, () => void logout())}{captureOpen && <CaptureSheet onClose={() => setCaptureOpen(false)} onSaved={() => {}} />}</AppShell>; }
