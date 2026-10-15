export type RecordMap = Record<string, unknown>;

export interface Session { authenticated: boolean; username: string }
export interface Todo {
  id: string; text: string; done: boolean; title?: string; status?: string;
  projectName?: string; project_id?: string | null; dueAt?: string | null;
  priority?: string | null; description?: string | null;
}
export interface ProjectExplainer {
  one_sentence?: string | null;
  current_state?: string | null;
  key_open_issue?: string | null;
  next_step?: string | null;
  what_it_is?: string | null;
  problem_it_solves?: string | null;
  why_it_matters?: string | null;
  how_it_works?: string | null;
  key_results?: string | null;
  glossary?: unknown;
  audience_context?: string | null;
}
export interface Project {
  id: string; name: string; description?: string | null; status?: string | null;
  stage?: string | null; priority?: string | null; dueDate?: string | null;
  latestProgressSummary?: string | null; nextActionSummary?: string | null;
  summary?: string | null; pending_todo_count?: number;
  explainer?: ProjectExplainer | null;
}
export interface Knowledge {
  id: string; title: string; status?: string | null; knowledgeType?: string | null;
  oneLineSummary?: string | null; applicability?: string | null;
  latestRevisionNo?: number; sourceCount?: number; projectCount?: number;
  authorityState?: string | null;
  // TD-003: bounded deterministic projection of the latest revision body,
  // used to derive the card summary with the audited classifier.
  contentBusinessFields?: RecordMap | null;
}
export interface Source {
  id: string; title: string; category?: string | null; status?: string | null;
  wpsDocumentId?: string | null; isCompiled?: boolean; updatedAt?: string | null;
  content?: string | null; sourceOrigin?: string | null; canonicalState?: string | null;
}
export interface Capture {
  id: string; rawText: string; capturedAt: string; source?: string; processedAt?: string | null;
  patrolState?: string | null; lifecycleDisplayLabel?: string | null; patrolEligible?: boolean | null;
}
export interface Daily { date: string; captures: Capture[]; daily?: RecordMap | null; readOnly?: boolean; }
export interface Presentation {
  display_title?: string; category_label?: string; situation?: string;
  decision_question?: string; approval_effect?: string; recommendation?: string;
  proposed_actions?: string[]; affected_entities?: Array<{type?: string; title?: string; label?: string; current_state?: string; action?: string; proposed_state?: string}>;
  decision_options?: Array<{key?: string; label?: string; description?: string; meaning?: string}>;
  presentation_status?: string;
  evidence_items?: string[]; evidence_summary?: string[] | string; attention_points?: string[];
  source_label?: string; review_mode?: string; review_mode_label?: string;
  confidence_label?: string; effective_review_state?: string;
}
export interface ReviewCandidate {
  candidateId: string; candidateType?: string | null; status?: string | null;
  effectiveReviewState?: string | null; confidence?: string | null;
  recommendedAction?: string | null; createdAt?: string | null; sourceLabel?: string | null;
  summary?: string; presentation?: Presentation | null; proposedChange?: unknown;
}
export interface ReviewDetail extends ReviewCandidate {
  inferenceLevel?: string | null; reviewedAt?: string | null; reviewedBy?: string | null;
}
export interface Activity { id?: string; timestamp?: string; activity_type?: string; source_entity_type?: string; message?: string; summary?: string; status?: string; }
export interface IntegrationStatus { cloudHub?: string; workspaceBackend?: {phase?: string; status?: string}; windowsBridge?: string; wpsAvailable?: string; wpsProvider?: string; adapterStatus?: RecordMap; serverMetrics?: {ram?: number; disk?: number}; ksheetPipeline?: RecordMap; }
export interface AgnesResponse { session_id: string; response: string; mode: string }
export interface LunaDraft {
  id: string; draftId: string; sourceVersionId: string; sourceTitle?: string | null;
  sourcePath?: string | null; projectId?: string | null; projectName?: string | null;
  summary?: string | null; status?: string | null; authorityLevel?: string | null;
  isCanonical?: boolean; coverageState?: string | null; reviewBundleId?: string | null;
  facts?: unknown[]; decisions?: unknown[]; metrics?: unknown[]; timeline?: unknown[];
  risks?: unknown[]; ambiguities?: unknown[]; relatedProjects?: unknown[];
  evidenceRefs?: unknown[]; provenance?: RecordMap; updatedAt?: string | null;
}

const text = (value: unknown, fallback = "") => typeof value === "string" && value.trim() ? value : fallback;
const nullableText = (value: unknown) => typeof value === "string" ? value : null;
const bool = (value: unknown) => value === true || value === "done";
const humanPriority = (value: unknown) => ({ low: "低", normal: "普通", medium: "中", high: "高", urgent: "紧急" } as Record<string, string>)[String(value ?? "")] ?? (typeof value === "string" && /[\u3400-\u9fff\s·]/.test(value) ? value : value ? "已设置" : null);
const humanStage = (value: unknown) => ({ discovery: "探索", planning: "规划", execution: "执行", review: "复盘", delivery: "交付", maintenance: "维护" } as Record<string, string>)[String(value ?? "")] ?? (typeof value === "string" && /[\u3400-\u9fff\s·]/.test(value) ? value : value ? "阶段已记录" : null);

export const mapTodo = (item: RecordMap): Todo => ({
  id: text(item.id), text: text(item.title, "未命名待办"), title: nullableText(item.title) ?? undefined,
  done: bool(item.done) || item.status === "done", status: nullableText(item.status) ?? undefined,
  projectName: nullableText(item.project_name) ?? nullableText(item.projectName) ?? undefined,
  project_id: nullableText(item.project_id), dueAt: nullableText(item.due_at) ?? nullableText(item.dueAt),
  priority: humanPriority(item.priority), description: nullableText(item.description),
});
const mapExplainer = (value: unknown): ProjectExplainer | null => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const item = value as RecordMap;
  const pick = (key: string) => nullableText(item[key]);
  const explainer: ProjectExplainer = {
    one_sentence: pick("one_sentence"),
    current_state: pick("current_state"),
    key_open_issue: pick("key_open_issue"),
    next_step: pick("next_step"),
    what_it_is: pick("what_it_is"),
    problem_it_solves: pick("problem_it_solves"),
    why_it_matters: pick("why_it_matters"),
    how_it_works: pick("how_it_works"),
    key_results: pick("key_results"),
    glossary: item.glossary,
    audience_context: pick("audience_context"),
  };
  return Object.values(explainer).some(v => v != null && v !== "") ? explainer : null;
};
export const mapProject = (item: RecordMap): Project => ({
  id: text(item.id), name: text(item.name, "未命名项目"), description: nullableText(item.description),
  status: nullableText(item.status), stage: humanStage(item.stage), priority: humanPriority(item.priority),
  dueDate: nullableText(item.due_date) ?? nullableText(item.dueDate),
  latestProgressSummary: nullableText(item.latest_progress_summary) ?? nullableText(item.latestProgressSummary),
  nextActionSummary: nullableText(item.next_action_summary) ?? nullableText(item.nextActionSummary),
  summary: nullableText(item.summary), pending_todo_count: typeof item.pending_todo_count === "number" ? item.pending_todo_count : undefined,
  explainer: mapExplainer(item.explainer ?? item.project_explainer),
});
const numOrUndef = (value: unknown): number | undefined => { if (typeof value === "number") return value; if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) return Number(value); return undefined; };
export const mapKnowledge = (item: RecordMap): Knowledge => ({
  id: text(item.id), title: text(item.title, "未命名知识条目"), status: nullableText(item.status),
  knowledgeType: nullableText(item.knowledge_type) ?? nullableText(item.knowledgeType),
  oneLineSummary: nullableText(item.one_line_summary) ?? nullableText(item.oneLineSummary),
  applicability: nullableText(item.applicability), latestRevisionNo: numOrUndef(item.latest_revision_no),
  sourceCount: numOrUndef(item.source_count), projectCount: numOrUndef(item.project_count),
  authorityState: nullableText(item.authority_state) ?? nullableText(item.authorityState),
  contentBusinessFields: (item.content_business_fields && typeof item.content_business_fields === "object" ? item.content_business_fields as RecordMap : null),
});
export const mapSource = (item: RecordMap): Source => ({
  id: text(item.id), title: text(item.title, "未命名资料"), category: nullableText(item.category), status: nullableText(item.status),
  wpsDocumentId: nullableText(item.wps_document_id) ?? nullableText(item.wpsDocumentId),
  isCompiled: item.is_compiled === true || item.isCompiled === true || item.last_compiled_at != null, updatedAt: nullableText(item.updated_at) ?? nullableText(item.updatedAt),
  content: nullableText(item.content), sourceOrigin: nullableText(item.source_origin), canonicalState: nullableText(item.canonical_state),
});
export const mapCapture = (item: RecordMap): Capture => ({
  id: text(item.id), rawText: text(item.raw_text, "（记录内容为空）"), capturedAt: text(item.captured_at),
  source: nullableText(item.source) ?? undefined, processedAt: nullableText(item.processed_at),
  patrolState: nullableText(item.patrol_state), lifecycleDisplayLabel: nullableText(item.lifecycle_display_label),
  patrolEligible: typeof item.patrol_eligible === "boolean" ? item.patrol_eligible : null,
});
export const captureDisplayBadge = (capture: Capture): { label: string; tone: "success" | "warning" | "neutral" } => {
  if (capture.lifecycleDisplayLabel) {
    if (capture.lifecycleDisplayLabel === "已整理") return { label: capture.lifecycleDisplayLabel, tone: "success" };
    if (capture.lifecycleDisplayLabel === "待你确认") return { label: capture.lifecycleDisplayLabel, tone: "warning" };
    return { label: capture.lifecycleDisplayLabel, tone: "warning" };
  }
  if (capture.processedAt) return { label: "已整理", tone: "success" };
  if (capture.patrolState === "waiting_human") return { label: "待你确认", tone: "warning" };
  return { label: "待巡航整理", tone: "warning" };
};
export const mapReview = (item: RecordMap): ReviewCandidate => ({
  candidateId: text(item.candidate_id ?? item.id), candidateType: nullableText(item.candidate_type), status: nullableText(item.status),
  effectiveReviewState: nullableText(item.effective_review_state), confidence: nullableText(item.confidence),
  recommendedAction: nullableText(item.recommended_action), createdAt: nullableText(item.created_at), sourceLabel: nullableText(item.source_label),
  summary: nullableText(item.summary) ?? undefined, presentation: (item.presentation as Presentation | null) ?? null,
  proposedChange: item.proposed_change,
});
export const mapReviewDetail = (item: RecordMap): ReviewDetail => ({
  ...mapReview(item), inferenceLevel: nullableText(item.inference_level), reviewedAt: nullableText(item.reviewed_at), reviewedBy: nullableText(item.reviewed_by),
});
export const mapDraft = (item: RecordMap): LunaDraft => ({
  id: text(item.draft_id ?? item.id),
  draftId: text(item.draft_id ?? item.id),
  sourceVersionId: text(item.source_version_id),
  sourceTitle: nullableText(item.source_title),
  sourcePath: nullableText(item.source_path),
  projectId: nullableText(item.project_id),
  projectName: nullableText(item.project_name),
  summary: nullableText(item.summary),
  status: nullableText(item.status),
  authorityLevel: nullableText(item.authority_level) ?? "draft",
  isCanonical: item.is_canonical === true,
  coverageState: nullableText(item.coverage_state),
  reviewBundleId: nullableText(item.review_bundle_id),
  facts: Array.isArray(item.facts) ? item.facts : undefined,
  decisions: Array.isArray(item.decisions) ? item.decisions : undefined,
  metrics: Array.isArray(item.metrics) ? item.metrics : undefined,
  timeline: Array.isArray(item.timeline) ? item.timeline : undefined,
  risks: Array.isArray(item.risks) ? item.risks : undefined,
  ambiguities: Array.isArray(item.ambiguities) ? item.ambiguities : undefined,
  relatedProjects: Array.isArray(item.related_projects) ? item.related_projects : undefined,
  evidenceRefs: Array.isArray(item.evidence_refs) ? item.evidence_refs : undefined,
  provenance: typeof item.provenance === "object" && item.provenance ? item.provenance as RecordMap : undefined,
  updatedAt: nullableText(item.updated_at),
});
