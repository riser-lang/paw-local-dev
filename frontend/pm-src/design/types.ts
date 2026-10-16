/* =============================================================================
   PAW 项目管理视图 — View Model 类型（唯一数据契约）
   与 VIEW_MODEL.md / view_model.schema.json 一一对应。
   约定：
   - 日期一律为 "YYYY-MM-DD"（北京时间的日历日），时间戳一律为 ISO-8601 带时区。
   - 任何未知 / 未提供的值用 null，不得用 "" 或 0 代替，组件按 null 显示“待补”。
   - 组件不做任何数据推断：不排序以外的加工、不补日期、不估算进度。
   ============================================================================= */

export type ISODate = string;      // "2026-10-15"
export type ISODateTime = string;  // "2026-10-15T08:30:00+08:00"

/** 一条可追溯的来源。所有“结论”都应至少附带一条。 */
export interface SourceRef {
  id: string;
  title: string;
  kind: "wps_doc" | "capture" | "knowledge" | "metric" | "chat" | "todo" | "other";
  /** 点击后打开的工作台内部地址；没有可打开的地址时为 null */
  href: string | null;
  updated_at: ISODateTime | null;
  /** 来源中的原文摘录（≤ 200 字），用于悬停卡片 */
  excerpt?: string | null;
}

/* ------------------------------------------------------------------ 系统状态 */
export type HealthState = "ok" | "warn" | "error" | "unknown";

export interface SystemStatusItem {
  key: "patrol" | "writeback" | "wps_mirror" | "backup" | "disk" | string;
  label: string;                       // "巡航"
  state: HealthState;
  /** 一句话说明，如 "最后一次巡航 00:16" / "剩余 5.2 GB" */
  summary: string;
  last_success_at: ISODateTime | null;
  detail?: string | null;              // 悬停/点开时显示
}

/* ------------------------------------------------------------------ 需要你决定 */
export type Impact = "high" | "medium" | "low";

export interface DecisionItem {
  id: string;
  /** 要决定什么（一句话，问句） */
  question: string;
  /** 原文摘录，与决定标题分开呈现。 */
  excerpt?: string | null;
  /** 选“是”会怎样 */
  if_yes: string;
  /** 选“否”会怎样 */
  if_no: string;
  impact: Impact;
  /** 影响说明（可选），如 "影响 3 个待办的归属" */
  impact_note?: string | null;
  project_id: string | null;
  project_name: string | null;
  created_at: ISODateTime;
  due_at: ISODateTime | null;
  sources: SourceRef[];
  /** 按钮文字，默认 "是" / "否" */
  yes_label?: string | null;
  no_label?: string | null;
}

/* ------------------------------------------------------------------ 事件与项目行 */
export type ProjectEventType = "new_source" | "conflict" | "decision" | "milestone_done";

export interface ProjectEvent {
  id: string;
  type: ProjectEventType;
  date: ISODate;
  title: string;
  /** 点击跳转目标，null 时只显示详情卡 */
  href: string | null;
  source?: SourceRef | null;
}

export type ProjectStatus = "active" | "at_risk" | "blocked" | "done" | "paused" | "empty";

export interface PortfolioRow {
  id: string;
  name: string;
  status: ProjectStatus;
  /** 行尾一句话现状 */
  one_liner: string | null;
  /** 项目整体周期；未知为 null（显示“周期待补”，不画条） */
  start: ISODate | null;
  end: ISODate | null;
  /** 完成比例 0–1；未知为 null（只画线框，不画斜线） */
  progress: number | null;
  events: ProjectEvent[];
  href: string | null;
  updated_at: ISODateTime | null;
}

export type TimeUnit = "week" | "month";

export interface HealthCheck {
  id:string;project_id:string|null;project_name:string;reference_at:ISODateTime;checked_at:ISODateTime;
  preview:boolean;semantic_status:string;status:string;stale?:boolean;
  thresholds:{undecided_days:number;no_progress_days:number};
  items:{kind:string;title:string;detail:string;basis:{title:string;href:string;quote:string}[]}[];
}
export interface KnowledgeGap {
  id:string;project_id:string;project_name:string;question:string;occurrences:number;
  first_seen_at:ISODateTime;last_seen_at:ISODateTime;state:'open'|'resolved';
}

/* ------------------------------------------------------------------ AI 建议 */
export interface AiSuggestion {
  state?: 'open' | 'pending_review' | 'adopted' | 'declined';
  category_label?: string;
  generated_at?: ISODateTime | null;
  id: string;
  text: string;
  /** 依据（至少 1 条）；没有依据的建议不应下发 */
  basis: SourceRef[];
  project_id: string | null;
  project_name: string | null;
}

/* ------------------------------------------------------------------ 去向回执 */
export interface RoutingReceipt {
  id: string;
  kind: "capture" | "new_file";
  /** 随手记内容摘要或文件名 */
  title: string;
  received_at: ISODateTime;
  /** 已归入的项目；尚未归入时为 null（显示“待归类”） */
  project_id: string | null;
  project_name: string | null;
  /** 点击项目名跳转的地址（通常是项目页）；null 时只显示名字 */
  href: string | null;
  /** 更新了哪些栏目（栏目标题） */
  updated_sections: string[];
  /** 处理状态 */
  state: "routed" | "pending" | "needs_decision";
  note?: string | null;
}

/* ------------------------------------------------------------------ 首页 */
export interface TodayViewModel {
  health_checks?:HealthCheck[];
  knowledge_gaps?:KnowledgeGap[];
  schema: "paw.pm.today/1";
  generated_at: ISODateTime;
  /** 今天（北京时间日历日）—— 甘特图“今天”标线以此为准 */
  today: ISODate;
  system_status: SystemStatusItem[];
  decisions: DecisionItem[];
  portfolio: { projects: PortfolioRow[] };
  ai_suggestions: AiSuggestion[];
  receipts: RoutingReceipt[];
}

/* ------------------------------------------------------------------ 项目页 */
export interface Milestone {
  id: string;
  title: string;
  plan_start: ISODate | null;
  plan_end: ISODate | null;
  actual_start: ISODate | null;
  /** 已完成时为实际完成日；未完成为 null */
  actual_end: ISODate | null;
  status: "done" | "in_progress" | "not_started" | "delayed";
  source: SourceRef | null;
  note?: string | null;
}

export interface ChangeEntry {
  id: string;
  at: ISODateTime;
  /** 变化发生在哪个栏目（栏目 key） */
  section_key: string;
  title: string;
  before: string | null;
  after: string | null;
  source: SourceRef | null;
  /** 是否允许撤销 */
  revertible: boolean;
  /** 已撤销 */
  reverted?: boolean;
}

export interface Metric {
  id: string;
  name: string;
  unit: string | null;
  current: { value: number; period: string | null; as_of: ISODate | null } | null;
  target: { value: number; period: string | null } | null;
  /** 时间顺序（旧 → 新），最多 12 个点 */
  trend: { period: string; value: number }[];
  higher_is_better: boolean | null;
  source: SourceRef | null;
}

export interface Conflict {
  id: string;
  topic: string;
  left: { label: string; text: string; source: SourceRef };
  right: { label: string; text: string; source: SourceRef };
  /** 裁决选项，默认三项：采用左 / 采用右 / 稍后 */
  options?: { key: string; label: string }[] | null;
  resolved?: { choice: string; at: ISODateTime } | null;
}

/** 栏目中的一条结论 */
export interface SectionEntry {
  id: string;
  text: string;
  /** "fact" = 来自工作台的事实；"suggestion" = AI 建议（浅色底并标注） */
  kind: "fact" | "suggestion";
  sources: SourceRef[];
  updated_at: ISODateTime | null;
  /** 历史版本（新 → 旧），点开查看 */
  history?: { at: ISODateTime; text: string; source: SourceRef | null }[] | null;
  /** 最近一次变更可撤销时给出对应 ChangeEntry.id */
  revert_change_id?: string | null;
  /** 可选标记：等级/状态，例如风险等级 */
  tag?: { text: string; tone: "neutral" | "warn" | "danger" | "ok" } | null;
}

export type SectionKind =
  | "summary" | "metrics" | "milestones" | "gaps" | "next_steps"
  | "risks" | "changes" | "sources" | "list";

export interface SectionConfig {
  key: string;
  title: string;
  kind: SectionKind;
  /** 空状态说明；为 null 时用组件默认文案 */
  empty_text?: string | null;
  /** 桌面端宽度："full" 占整行，"half" 半行；省略时按 kind 取默认（gaps/next_steps/list/sources 为 half） */
  span?: "full" | "half" | null;
}

export interface ProjectViewModel {
  health_checks?:HealthCheck[];
  knowledge_gaps?:KnowledgeGap[];
  ai_suggestions?: AiSuggestion[];
  schema: "paw.pm.project/1";
  generated_at: ISODateTime;
  today: ISODate;
  project: {
    id: string;
    name: string;
    status: ProjectStatus;
    one_liner: string | null;
    updated_at: ISODateTime | null;
  };
  /** 栏目顺序与标题由配置决定 */
  sections: SectionConfig[];
  /** 按栏目 key 提供条目（summary/gaps/next_steps/risks/list/sources 使用） */
  entries: Record<string, SectionEntry[]>;
  metrics: Metric[];
  milestones: Milestone[];
  changes: ChangeEntry[];
  conflicts: Conflict[];
  sources: SourceRef[];
  receipts: RoutingReceipt[];
}

/* ------------------------------------------------------------------ 回调（由接入方实现） */
export interface PmCallbacks {
  onRunHealthCheck?: (projectId?:string) => void | Promise<void>;
  onResolveKnowledgeGap?: (gapId:string) => void | Promise<void>;
  onAdoptSuggestion?: (id: string) => void | Promise<void>;
  onGenerateSuggestions?: (projectId: string) => void | Promise<void>;
  /** 打开来源 / 跳转工作台内部地址 */
  onNavigate?: (href: string) => void;
  /** 批量或单条决定：choice = "yes" | "no" | "later" */
  onDecide?: (decisionIds: string[], choice: "yes" | "no" | "later") => void | Promise<void>;
  /** 撤销一条变更 */
  onRevert?: (changeId: string) => void | Promise<void>;
  /** 冲突裁决 */
  onResolveConflict?: (conflictId: string, optionKey: string) => void | Promise<void>;
}
