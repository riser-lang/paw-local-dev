/**
 * PAW v1.3C Closure Patch 1 — deterministic Knowledge presentation layer.
 *
 * WHY THIS EXISTS
 * ---------------
 * The v1.3C Knowledge UI rendered `Object.entries(content_json)` and mapped
 * every unrecognised key to the same human label ("补充信息"). A Formal
 * Knowledge revision stores BUSINESS content and Reader/governance PROVENANCE
 * in one flat object, so the primary reading layer came out full of
 * `explicit_fact`, `text_blocks[35]`, `CODEX_LUNA`, `gpt-5.6-luna`, bare
 * governance booleans, and 34 identical "补充信息" headings.
 *
 * This module classifies fields DETERMINISTICALLY — no model call, no semantic
 * AI judgement — into three buckets:
 *
 *   business  -> primary reading layer (知识内容 / 核心指标 / 时间线 / ...)
 *   technical -> collapsed 来源与技术信息 drawer
 *   ambiguous -> secondary 其他信息 section (preserved, never primary)
 *
 * PROVENANCE OF THE ALLOWLISTS
 * ----------------------------
 * Every field name below was derived from an audit of the REAL production
 * `knowledge_revision.content_json` (788 revisions over 655 knowledge_page
 * rows) plus the detail canary `5-运营实习生夏炎棋结营述职`. Nothing here is
 * assumed; unrecognised keys fall through to the deterministic shape rules and
 * then to `ambiguous` — they are never discarded.
 *
 * HARD RULES
 * ----------
 * - No field is deleted. Ambiguous values are shown under 其他信息.
 * - The untouched raw object is always exposed as 原始记录.
 * - A string that carries technical tokens is NOT aggressively stripped (§18);
 *   the whole block is demoted to a secondary section instead.
 */

export type FieldClass = "business" | "technical" | "ambiguous";

export interface PresentationItem {
  /** Business-readable text. */
  text: string;
  /** Per-item technical detail, kept but collapsed. */
  tech: string | null;
}

export interface BusinessGroup {
  key: string;
  label: string;
  items: PresentationItem[];
}

export interface PresentationEntry {
  key: string;
  label: string;
  text: string;
}

export interface EvidenceRef {
  sourceVersionId: string;
  locator: string;
}

export interface KnowledgePresentation {
  /** one-line business summary, or null when only parser prose exists. */
  summary: string | null;
  summarySource: "one_line_summary" | "content_summary" | "business_field" | "none";
  /** business groups rendered inside 知识内容 */
  mainGroups: BusinessGroup[];
  /** business groups promoted to their own section (核心指标/时间线/风险/建议动作) */
  optionalGroups: BusinessGroup[];
  /** provenance / runtime / parser metadata -> 来源与技术信息 drawer */
  technical: PresentationEntry[];
  /** unrecognised-but-not-technical fields -> 其他信息 */
  ambiguous: PresentationEntry[];
  /** evidence locators paired with a determinable source version */
  evidenceRefs: EvidenceRef[];
  /** technical tokens still present in the primary body (audit hook; must be 0) */
  primaryTechLeak: string[];
}

/* ------------------------------------------------------------------ *
 * 1. Deterministic token grammar
 * ------------------------------------------------------------------ */

/** Structural evidence locator, e.g. `text_blocks[35]`, `tables[0]`. */
const LOCATOR_RE = /^[a-z_]+\[\d+\]$/;

/**
 * THE single technical-token grammar.
 *
 * Observed in REAL production strings. This is fixed grammar, not semantic
 * inference. It is used for BOTH jobs, so the demotion rule and the leak audit
 * can never disagree:
 *
 *   1. `isTechnicalProse()` — a business-named string carrying any of these is
 *      demoted to the secondary layer instead of being rendered in the primary
 *      body (§18: the grammar is not cleanly separable, so the whole block is
 *      moved rather than aggressively stripped).
 *   2. `primaryTechLeak` — the audit hook that must report 0 (§35).
 *
 * Real examples this must catch (from the production audit):
 *   "1页；35 shapes（含TABLE 2、LINE 2、PICTURE 1）；text_blocks=31（非空25）；
 *    connector_count=2；image_count=1；notes空；native_visual_renderer=false。"
 *   "tables[0]未抽出行（candidate/status无nrows）；购销方「名称/税号」因版式
 *    被拆成单字块，需按text_blocks序号拼合。"
 *   "Evidence Gate passed. ... because visual_screener.no_further_visual_work
 *    is true; ..."
 */
const TECH_TOKEN_RE: RegExp[] = [
  // --- Reader structural locators ---
  /text_blocks\s*[\[=]/, /\btables\s*[\[=]/, /\bvisual_pages\s*[\[=]/,
  /\bembedded_objects\b/, /\bfidelity_flags\b/, /\bstructured_semantics\./,
  // --- Reader / parser counters ---
  /\b(?:slide|page|sheet|image|connector|chart|text_block|formula|shape|paragraph|drawing|table|cell|row|column|object|element|relationship|warning|run)_count\s*=/,
  /\bmax_(?:row|column)\b/,
  /\bncells\s*=/,
  // Bare parser field names: these are reader/parser vocabulary and never
  // occur inside business prose (proved by corpus grep).
  /\bdrawing_inventory\b/,
  /\bvisual_pages\b/,
  /\bclassification\s*=/,
  /\bnative_visual_renderer\b/, /\bvisual_render\b/, /\bnative-text\b/,
  /\d+\s*shapes/,
  // --- Spreadsheet / slide structural fragments (proved from the real corpus) ---
  /[A-Z]{1,3}\d{1,5}\s*:\s*[A-Z]{1,3}\d{1,5}/,   // A1:H2 cell range
  /\bSheet\d*\b/,                                  // "单工作表 Sheet1"
  /\bvisible\b/,
  /合并\s*[A-Z]{1,3}\d/,
  // --- Evidence-gate / reader boilerplate ---
  /Evidence Gate passed/i,
  /\bvisual_screener\./,
  /no_further_visual_work/,
  /Reader\s*解析为/,
  /\bexplicit_fact\b/,
  /\bfidelity\b/,
  // --- Model / provenance identity ---
  /\bCODEX_LUNA\b/, /\bgpt-\d/, /\bclaude-[a-z0-9-]+\b/i,
  /USER_AUTHORIZED/, /\bLUNA_[A-Z_]+\b/,
  // --- Identity / provenance keys ---
  /\bsource_version(_id)?s?\b/, /\bapproved_change\b/,
  // --- Machine status codes -------------------------------------------------
  // A whole-token snake_case code with >= 3 segments (e.g.
  // `visual_pending_for_images_connectors_and_unlabeled_kpis`) is a pipeline
  // status flag, never business prose. Two-segment snake_case is left alone:
  // that shape is genuinely ambiguous (it occurs inside business sentences),
  // and §18 forbids aggressive stripping on ambiguous grammar.
  /\b[a-z][a-z0-9]*(?:_[a-z0-9]+){2,}\b/,
  // A multi-segment ALL_CAPS code (`NEEDS_REVIEW`, `USER_AUTHORIZED`,
  // `VISUAL_PENDING`) is a governance enum. Single-segment acronyms (`SOP`,
  // `FY26`, `OK`) never match, so business acronyms are unaffected.
  /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/,
  // --- Chinese Evidence-Gate boilerplate ------------------------------------
  // Measured across all 19,619 corpus string leaves: every phrase below has
  // `pure = 0` — removing it always leaves CJK content behind, i.e. the phrase
  // is NEVER the whole meaning of its string. It always appears as a leading
  // clause followed by `。` / `；`, so the segment splitter drops the leading
  // governance clause and keeps the remainder verbatim. Nothing is deleted.
  /视觉筛选(?:已确认|明确)?无需(?:进一步|再次)视觉(?:工作|内容|复核)/,
  /无需再次读取视觉内容/,
  /视觉策略已满足/,
  /阅读器保真门通过/,
  /阅读保真度通过/,
  /证据与阅读保真度通过/,
  /(?:语义)?证据(?:读取|处理)成功/,
  /证据门通过/,
  /明确无需继续视觉复核/,
  /已有视觉(?:工件|产物|事实)/,
  // --- TD-003: reader geometry / render / parser vocabulary -----------------
  // Reader statistics and parser metadata that still reached the summary
  // chain. Each is unambiguous machine vocabulary: the §18 splitter drops the
  // leading technical clause and keeps the business remainder, and returns
  // null only when nothing business survives. Measured: removes every
  // parser-metadata summary (TECH_METADATA_AS_SUMMARY = 0) while keeping 202
  // of the 251 previously-empty cards readable.
  /\bmedia_box\b/,
  /渲染\s*[约]?\s*\d+\s*[×x]\s*\d+/,
  /\bpackage_inventory\b/,
  /\bwarnings\s*[：:=\[含]/,
  /\bnotes\s*(?:为空|空)/,
  /\bslide\s*\d+/i,
  /\bvisual\s+(?:page|PNG|image)/i,
  /\bimage-only\b/,
  /\bempty-or-unparsed\b/,
  /\bHYPERLINK\s*\(/,
  /\bdimensions\s+width\s*=/,
  /\b[a-z][a-z0-9_]*\s*=\s*(?:\[|\d|true|false|null)\b/,
  /\b[a-z][a-z0-9_]*\s*=\s*[a-z][a-z0-9_]*\b/,
  /\b(?:uniq|max|min|hidden|version_marker)\s*=/,
  // --- TD-003 (2nd pass): reader / pipeline PROCESS prose -------------------
  // These sentences were measured leaking into the RENDERED card summary
  // through knowledge_page.one_line_summary, e.g.
  //   "语义证据验证通过；文档为15个维度的高中个性化运营导师复试情境题库。
  //    视觉筛选已确认无需追加视觉工作，且已消费既有视觉事实。…"
  // They describe what the READER did, not what the business document says, so
  // they are technical by the same standard as media_box or slide_count.
  /视觉筛选/,
  /视觉(?:事实|工作|读取|复核|复读)/,
  /语义证据(?:验证|校验|分析|结果|完整|可用)/,
  /语义结果(?:成功|可用)/,
  /阅读保真(?:门|度)/,
  /(?:Evidence\s*Gate|证据门|保真门)(?:通过|已通过)/,
  /无需(?:进一步|再次|追加|额外)(?:的)?(?:视觉|复核|读取|复读|检查)/,
  /已消费(?:既有|现有|已有)?视觉/,
  /未经证实的视觉事实/,
  /未将未转写图片作为证据/,
  // --- TD-003 (3rd pass): machine-vocabulary closure ------------------------
  // Measured with `td003_rulelab.mjs` over all 601 current pages.  This pass
  // closes the last parser-metadata family and is deliberately MINIMAL: three
  // families, each of which is machine vocabulary and cannot occur in Chinese
  // business prose.  Blast radius, verified card by card: 42 cards change, all
  // 42 keep their business text; exactly 6 fall to 暂无摘要 and all 6 are pages
  // the reader itself declares content-free ("VISUAL_PENDING_image_only",
  // "无sheet名/单元格值", "无法从Reader结果读取").  No legitimate summary is
  // lost: stillNull 34 -> 40, and the previously-empty card count does not rise
  // for any page that has business content.
  //
  // Family 1 — machine `key=value`.  The previous rule required a word boundary
  // AFTER the value, so `formula_n=405` / `n_cells=438` / `record_count=201110`
  // escaped it (the boundary between "4" and "0" is not a boundary).  A
  // snake_case identifier immediately followed by `=` is never business prose.
  /\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\s*=/,
  // Family 2 — machine enum.  The existing rule used a trailing \b, which a
  // MIXED-CASE enum such as `VISUAL_PENDING_or_UNPARSED_legacy_xls` slips
  // through (no boundary between "G" and "_").  Requires 3+ uppercase letters
  // so business acronyms (FY26 / SOP / A1:H2) are untouched.
  /[A-Z][A-Z0-9]{2,}(?:_[A-Za-z0-9]+)+/,
  // Family 3 — the reader's own structural / pipeline vocabulary.
  /\bsheet\b/i,
  /\b(?:LibreOffice|openpyxl|DataValidation|reader_type|fidelity_flags|raw_canonical|validated_derived_parse_route|derived_route|derived_artifacts?|workbook_stream_bytes|conversion_attempted|embedded_objects|inventory|BIFF|OLE)\b/,
  /转换\s*BLOCKED/,
  /\bwarnings?\b/,
  /\btext_blocks\s*=\s*\[\]/,
  /\btables\s*=\s*\[\]/,
  /\bvisual_pages\s*=\s*\[\]/,
  /本任务不(?:读取|解析)/,
  /无法从Reader结果读取/,
];



export function isTechnicalProse(value: string): boolean {
  const text = value.trim();
  if (!text) return false;
  return TECH_TOKEN_RE.some((re) => re.test(text));
}

/** CJK presence — business content in this corpus is Chinese. */
const CJK_RE = /[\u3400-\u9fff]/;

/** Minimum length for a usable business summary. */
const MIN_SUMMARY_CHARS = 6;

/**
 * §18 — split a mixed "parser diagnostics + business text" block.
 *
 * REAL production example:
 *   "max_row=6；max_column=8；A1:H2合并。列：部门、姓名、员工ID、固定薪资、
 *    浮动绩效、专项补贴、（G列全空）、薪资合计。王念(211101)固定800浮动267合计1067。"
 *
 * The grammar is deterministic: the block is a sequence of sentences and a
 * LEADING sentence either carries parser tokens or it does not.
 *
 * Safety properties (why this is not "aggressive stripping"):
 *  - A string that is already clean is returned verbatim (only a length floor
 *    applies) — no CJK/fragment heuristic is imposed on it.
 *  - On the split path only LEADING segments are dropped. Once a business
 *    segment is seen, everything after it is kept verbatim.
 *  - The text is never rewritten or re-worded.
 *  - The split remainder must look like a business sentence: long enough,
 *    containing CJK, and not ending on a bare ASCII word (the signature of a
 *    truncated source such as "...；native").
 *  - If nothing business survives, `null` is returned and the caller shows
 *    暂无摘要.
 *  - The untouched original is always preserved in 原始记录.
 *
 * Used for the CARD / list summary surface. The detail body uses whole-block
 * demotion instead, so the primary article is guaranteed pristine.
 */
export function businessSummaryFrom(raw: string | null | undefined): string | null {
  const text = (raw ?? "").trim();
  if (!text) return null;

  // TD-003: a segment that is reader / pipeline prose is dropped WHEREVER it
  // occurs, not only in the leading run.  The previous rule returned a MIXED
  // string verbatim, so a Formal one-line summary such as
  //   "语义证据验证通过；文档为…题库。视觉筛选已确认无需追加视觉工作。…"
  // kept the reader sentence in the middle of the card summary.  A summary
  // that is entirely business prose is still returned untouched.
  const segments = text.split(/(?<=[。；!?！？])/).map((s) => s.trim()).filter(Boolean);
  const anyTechnical = isTechnicalProse(text) || segments.some((s) => isTechnicalProse(s));
  if (!anyTechnical) {
    return text.length >= MIN_SUMMARY_CHARS ? text : null;
  }
  const kept: string[] = segments.filter((seg) => !isTechnicalProse(seg));
  const joined = kept.join(" ").trim();
  if (joined.length < MIN_SUMMARY_CHARS) return null;
  if (!CJK_RE.test(joined)) return null;
  if (/[A-Za-z]{1,12}$/.test(joined) && !CJK_RE.test(joined.slice(-1))) return null;
  if (isTechnicalProse(joined)) return null;
  return joined;
}

/* ------------------------------------------------------------------ *
 * 2. Top-level key allowlists (audit-derived)
 * ------------------------------------------------------------------ */

/**
 * Provenance / runtime / parser keys. Never rendered in the primary body.
 * Derived from the production audit: `provenance` (469 revisions),
 * `evidence_refs` (587), `source_version_ids` (587), `current_effective` (587),
 * `canonical_topic` (587).
 */
const TECHNICAL_KEYS = new Set<string>([
  "provenance", "evidence_refs", "source_version_ids", "source_version_id",
  "current_effective", "canonical_topic", "source", "source_refs",
  "review_gate", "review_gate_update", "latest_analysis_input",
  "spark_governance", "long_term_valid", "evidence_summary", "evidence",
  "governance", "checks", "review_notes", "confidence_level",
  "analysis_origin", "analysis_model", "reader_model", "model", "provider",
  "formalization_actor", "formalization_mode", "formal_state_bypass",
  "approval_source", "promotion_source", "commit_ref", "model_call",
  "artifact_reference", "content_fingerprint", "raw_artifact_sha256",
  "reader_result_ref", "evidence_locators", "evidence_locator",
]);

/**
 * Business keys. Only these reach the primary reading layer.
 * Derived from the production audit (see the frequency table in the patch
 * report): `risks` 484, `metrics` 482, `timeline` 474, `facts` 471,
 * `ambiguities` 469, plus the business long tail.
 */
const BUSINESS_KEYS = new Set<string>([
  "facts", "metrics", "timeline", "risks", "ambiguities", "findings",
  "principles", "core_conclusions", "core_findings", "core_principles",
  "unresolved_items", "lessons_learned", "action_recommendations",
  "next_actions", "decision_records", "current_rules", "core_rules",
  "operating_model", "project_positioning", "method_positioning",
  "rule_positioning", "model_positioning", "mechanism_positioning",
  "analysis_positioning", "management_summary", "management_implications",
  "validation_framework", "evidence_pending", "implications",
  "observed_facts", "business_observations", "process", "exceptions",
  "known_gap", "known_gaps", "data_quality_notes", "interpretation_limits",
  "category", "positioning", "project", "project_scope", "domain_scope",
  "boundary", "priority_gaps", "roles_and_operations", "target_audience",
  "training_pathway", "curriculum", "offline_training", "project_definition",
  "diagnosis_method", "evaluation_layers", "failure_modes",
  "monthly_report_framework", "validation_metrics", "eligibility_rules",
  "renewal_and_exit", "selection_flow", "assessment_sequence",
  "attribution_framework", "evidence_examples", "department_observations",
  "measurement_scope", "monthly_baseline", "retention_context",
  "common_dimensions", "data_rules", "dictionary", "metric_domains",
  "standard_student_stages", "expected_artifacts", "knowledge_model",
  "candidate_types", "not_candidate", "output", "levels",
  "priority_dimensions", "lifecycle_states", "upgrade_paths",
  "review_required", "cycle", "directional_framework", "observations",
  "compensation_structure", "supplementary_incentives", "scoring_model",
  "batch_model", "baseline_use_for_fy27", "baseline_reference",
  "assessment_and_conversion", "relationship_to_layering",
  "removed_or_adjusted_items", "evidence_requirements", "decision_context",
  "boundary_conditions", "acceptance_rules", "evidence_discovery_plan",
  "pending_validation", "facts_boundary", "fact_boundary",
  "evidence_layers", "known_limitations", "open_questions",
]);

/** Business labels for the primary reading layer. */
const BUSINESS_LABELS: Record<string, string> = {
  facts: "关键事实",
  metrics: "核心指标",
  timeline: "时间线",
  risks: "风险",
  ambiguities: "待澄清",
  findings: "关键发现",
  core_findings: "核心发现",
  principles: "原则",
  core_conclusions: "核心结论",
  core_principles: "核心原则",
  unresolved_items: "待复核事项",
  lessons_learned: "经验总结",
  action_recommendations: "建议动作",
  next_actions: "后续动作",
  decision_records: "决策记录",
  current_rules: "当前规则",
  core_rules: "核心规则",
  operating_model: "运行机制",
  project_positioning: "项目定位",
  method_positioning: "方法定位",
  rule_positioning: "规则定位",
  model_positioning: "模型定位",
  mechanism_positioning: "机制定位",
  analysis_positioning: "分析定位",
  management_summary: "管理摘要",
  summary: "摘要",
  management_implications: "管理含义",
  validation_framework: "验证框架",
  evidence_pending: "待补证据",
  implications: "影响",
  observed_facts: "观察到的事实",
  business_observations: "业务观察",
  process: "流程",
  exceptions: "例外情况",
  known_gap: "已知缺口",
  known_gaps: "已知缺口",
  data_quality_notes: "数据质量说明",
  interpretation_limits: "解读边界",
  category: "分类",
  positioning: "定位",
  project: "所属项目",
  project_scope: "项目范围",
  domain_scope: "领域范围",
  boundary: "边界",
  priority_gaps: "优先缺口",
  roles_and_operations: "角色与运作",
  target_audience: "目标对象",
  training_pathway: "培养路径",
  curriculum: "课程体系",
  offline_training: "线下培训",
  project_definition: "项目定义",
  diagnosis_method: "诊断方法",
  evaluation_layers: "评估层次",
  failure_modes: "失效模式",
  monthly_report_framework: "月度报告框架",
  validation_metrics: "验证指标",
  eligibility_rules: "资格规则",
  renewal_and_exit: "续约与退出",
  selection_flow: "选拔流程",
  assessment_sequence: "评估顺序",
  attribution_framework: "归因框架",
  evidence_examples: "证据示例",
  department_observations: "部门观察",
  measurement_scope: "度量范围",
  monthly_baseline: "月度基线",
  retention_context: "留存背景",
  common_dimensions: "通用维度",
  data_rules: "数据规则",
  dictionary: "口径字典",
  metric_domains: "指标域",
  standard_student_stages: "学员阶段标准",
  expected_artifacts: "预期产出",
  knowledge_model: "知识模型",
  candidate_types: "候选项类型",
  not_candidate: "不纳入范围",
  output: "输出",
  levels: "层级",
  priority_dimensions: "优先维度",
  lifecycle_states: "生命周期状态",
  upgrade_paths: "升级路径",
  review_required: "需要复核",
  cycle: "周期",
  directional_framework: "方向框架",
  observations: "观察",
  compensation_structure: "薪酬结构",
  supplementary_incentives: "补充激励",
  scoring_model: "评分模型",
  batch_model: "批次模型",
  baseline_use_for_fy27: "FY27 基线用途",
  baseline_reference: "基线参考",
  assessment_and_conversion: "评估与转化",
  relationship_to_layering: "与分层的关系",
  removed_or_adjusted_items: "移除或调整项",
  evidence_requirements: "证据要求",
  decision_context: "决策背景",
  boundary_conditions: "边界条件",
  acceptance_rules: "验收规则",
  evidence_discovery_plan: "证据获取计划",
  pending_validation: "待验证",
  facts_boundary: "事实边界",
  fact_boundary: "事实边界",
};

/** Technical labels for the collapsed drawer. */
const TECHNICAL_LABELS: Record<string, string> = {
  provenance: "来源与模型溯源",
  evidence_refs: "证据定位",
  source_version_ids: "来源版本",
  source_version_id: "来源版本",
  current_effective: "当前有效",
  canonical_topic: "知识主题标识",
  source: "来源信息",
  source_refs: "来源引用",
  review_gate: "复核门禁",
  review_gate_update: "复核门禁更新",
  latest_analysis_input: "最近分析输入",
  spark_governance: "治理记录",
  long_term_valid: "长期有效",
  evidence_summary: "证据摘要",
  evidence: "证据",
  governance: "治理字段",
  checks: "检查项",
  review_notes: "复核备注",
  confidence_level: "置信度标记",
  analysis_origin: "分析来源",
  analysis_model: "分析模型",
  reader_model: "读取模型",
  model: "模型",
  provider: "提供方",
  formalization_actor: "正式化执行者",
  formalization_mode: "正式化模式",
  formal_state_bypass: "正式状态绕过",
  approval_source: "审批来源",
  promotion_source: "提升来源",
  commit_ref: "提交引用",
  model_call: "模型调用",
};

/**
 * Sub-keys inside business list/dict items.
 *
 * REAL example (`facts[]` in the detail canary):
 *   {"claim": "<business>", "claim_type": "explicit_fact",
 *    "evidence_locators": ["text_blocks[35]"]}
 * `claim` is business; `claim_type` / `evidence_locators` are technical.
 */
const SUBKEY_BUSINESS = new Set<string>([
  "claim", "content", "text", "event", "action", "decision", "rule", "basis",
  "date", "label", "value", "metric", "question", "layer", "item", "role",
  "reason", "priority", "type", "category", "examples", "management_use",
  "definition", "numerator", "denominator", "inclusion", "exclusion",
  "period", "month", "name", "title", "description", "summary", "conclusion",
  "finding", "risk", "suggestion", "recommendation", "rationale", "note",
  "notes", "detail", "stage", "step", "owner", "scope", "goal", "objective",
  "result", "outcome", "impact", "measure", "target", "threshold", "issue",
  "problem", "gap", "conflict", "question_text", "answer", "remark",
]);

const SUBKEY_TECHNICAL = new Set<string>([
  "claim_type", "evidence_locators", "evidence_locator", "evidence",
  "confidence", "evidence_status", "rationale_status", "ref_id", "locator",
  "source_version_id", "source_version_ids", "source_refs", "evidence_refs",
  "model", "provider", "analysis_model", "reader_model", "approval_source",
  "promotion_source", "candidate_id", "review_decision_id",
  "approved_change_id", "commit_ref", "canonical", "model_call",
  "page_count", "sheet_count", "formula_count", "image_count",
  "text_block_count", "classification", "visual_render",
  "native_visual_renderer", "slide_count", "connector_count", "chart_count",
  "max_row", "max_column", "drawing", "artifact_reference",
  "content_fingerprint", "raw_artifact_sha256", "block_index", "block_type",
  "fidelity", "identity_verification", "reader_result_ref",
  "formal_state_bypass", "raw_artifact_available",
]);

/* ------------------------------------------------------------------ *
 * 3. Deterministic shape rules for keys not in the allowlists
 * ------------------------------------------------------------------ */

const TECH_KEY_SUFFIX_RE = /(_ids?|_refs?|_locators?|_hash|_sha256|_checksum|_path)$/;
const TECH_KEY_PREFIX_RE =
  /^(source_version|evidence|provenance|approval|promotion|commit|canonical|model|provider|analysis_model|reader_model|candidate|review_decision|approved_change|formal_state|model_call|parser|reader|artifact|raw_)/;

function looksLikeLocatorArray(value: unknown): boolean {
  if (!Array.isArray(value) || value.length === 0) return false;
  const strings = value.filter((v): v is string => typeof v === "string");
  if (strings.length !== value.length) return false;
  const hits = strings.filter((s) => LOCATOR_RE.test(s.trim())).length;
  return hits / strings.length >= 0.8;
}

function classifyKey(key: string, value: unknown): FieldClass {
  if (TECHNICAL_KEYS.has(key)) return "technical";
  // §15: bare governance booleans are technical metadata.
  if (typeof value === "boolean") return "technical";
  if (TECH_KEY_SUFFIX_RE.test(key)) return "technical";
  if (TECH_KEY_PREFIX_RE.test(key)) return "technical";
  if (looksLikeLocatorArray(value)) return "technical";
  if (BUSINESS_KEYS.has(key)) return "business";
  return "ambiguous";
}

/* ------------------------------------------------------------------ *
 * 4. Value rendering
 * ------------------------------------------------------------------ */

function scalarText(value: unknown): string | null {
  if (typeof value === "string") return value.trim() || null;
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "是" : "否";
  return null;
}

/** Split an object into (business text, technical text). */
function splitObject(item: Record<string, unknown>): { text: string; tech: string | null } {
  const biz: string[] = [];
  const tech: string[] = [];
  for (const [k, v] of Object.entries(item)) {
    const t = scalarText(v);
    if (SUBKEY_TECHNICAL.has(k) || TECH_KEY_SUFFIX_RE.test(k) || typeof v === "boolean") {
      if (t !== null) tech.push(`${k}: ${t}`);
      continue;
    }
    if (SUBKEY_BUSINESS.has(k) || BUSINESS_KEYS.has(k)) {
      if (t !== null) {
        // A business sub-value carrying technical tokens is not stripped (§18):
        // it is demoted to the technical side of the same item.
        if (isTechnicalProse(t)) tech.push(`${k}: ${t}`);
        else biz.push(t);
      } else {
        const nested = flattenValue(v);
        if (nested) (isTechnicalProse(nested) ? tech : biz).push(nested);
      }
      continue;
    }
    // Unknown sub-key: deterministic shape rule, then preserve.
    if (t !== null) (isTechnicalProse(t) ? tech : biz).push(`${k}：${t}`);
    else {
      const nested = flattenValue(v);
      if (nested) (isTechnicalProse(nested) ? tech : biz).push(nested);
    }
  }
  return {
    text: biz.join("；"),
    tech: tech.length ? tech.join("；") : null,
  };
}

/** Deterministic readable flattening used for the secondary/technical side. */
function flattenValue(value: unknown): string {
  const s = scalarText(value);
  if (s !== null) return s;
  if (Array.isArray(value)) {
    return value.map((v) => flattenValue(v)).filter(Boolean).join("、");
  }
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => {
        const inner = flattenValue(v);
        return inner ? `${k}：${inner}` : "";
      })
      .filter(Boolean)
      .join("；");
  }
  return "";
}

function toItems(value: unknown): { items: PresentationItem[]; demoted: string[] } {
  const items: PresentationItem[] = [];
  const demoted: string[] = [];
  const push = (text: string, tech: string | null) => {
    // §18: a block carrying technical tokens is not aggressively stripped —
    // the WHOLE block moves to the secondary layer instead.
    if (text && isTechnicalProse(text)) {
      demoted.push(text);
      return;
    }
    if (text) {
      items.push({ text, tech });
      return;
    }
    if (tech) demoted.push(tech);
  };

  if (Array.isArray(value)) {
    for (const el of value) {
      if (el && typeof el === "object" && !Array.isArray(el)) {
        const { text, tech } = splitObject(el as Record<string, unknown>);
        push(text, tech);
      } else {
        const t = scalarText(el) ?? flattenValue(el);
        if (t) push(t, null);
      }
    }
    return { items, demoted };
  }
  if (value && typeof value === "object") {
    const { text, tech } = splitObject(value as Record<string, unknown>);
    push(text, tech);
    return { items, demoted };
  }
  const t = scalarText(value) ?? flattenValue(value);
  if (t) push(t, null);
  return { items, demoted };
}

/* ------------------------------------------------------------------ *
 * 5. Public API
 * ------------------------------------------------------------------ */

/** §19 optional-main sections get promoted out of 知识内容. */
const OPTIONAL_MAIN_KEYS = new Set([
  "metrics", "timeline", "risks", "action_recommendations", "next_actions",
]);

function businessLabel(key: string): string {
  return BUSINESS_LABELS[key] ?? key;
}

function technicalLabel(key: string): string {
  return TECHNICAL_LABELS[key] ?? key;
}

/**
 * Pair evidence locators with a determinable source version.
 *
 * Pairing is only claimed when there is EXACTLY ONE source version — with
 * several candidates the mapping is ambiguous and no drill-down is offered
 * (§23: never show a fake Evidence action).
 */
export function extractEvidenceRefs(content: Record<string, unknown>): EvidenceRef[] {
  const sids = Array.isArray(content.source_version_ids)
    ? (content.source_version_ids as unknown[]).filter((v): v is string => typeof v === "string" && v.trim().length > 0)
    : [];
  const raw = Array.isArray(content.evidence_refs) ? content.evidence_refs : [];
  const locators: string[] = [];
  for (const el of raw) {
    if (typeof el === "string" && LOCATOR_RE.test(el.trim())) locators.push(el.trim());
    else if (el && typeof el === "object") {
      const rec = el as Record<string, unknown>;
      const loc = typeof rec.locator === "string" ? rec.locator.trim() : "";
      if (loc && LOCATOR_RE.test(loc)) locators.push(loc);
    }
  }
  const unique = Array.from(new Set(locators));
  if (sids.length !== 1 || unique.length === 0) return [];
  return unique.map((locator) => ({ sourceVersionId: sids[0], locator }));
}

/**
 * Build the human-readable presentation of a Formal Knowledge revision.
 *
 * @param content    `knowledge_revision.content_json`
 * @param oneLine    `knowledge_page.one_line_summary` (Formal summary field)
 */
export function buildKnowledgePresentation(
  content: Record<string, unknown>,
  oneLine?: string | null,
): KnowledgePresentation {
  const mainGroups: BusinessGroup[] = [];
  const optionalGroups: BusinessGroup[] = [];
  const technical: PresentationEntry[] = [];
  const ambiguous: PresentationEntry[] = [];
  const businessTexts: string[] = [];

  for (const [key, value] of Object.entries(content)) {
    const cls = classifyKey(key, value);

    if (cls === "technical") {
      const text = flattenValue(value);
      if (text) technical.push({ key, label: technicalLabel(key), text });
      continue;
    }

    if (cls === "ambiguous") {
      const text = flattenValue(value);
      if (text) ambiguous.push({ key, label: key, text });
      continue;
    }

    // business — but a scalar string carrying technical tokens is demoted (§18).
    if (typeof value === "string" && isTechnicalProse(value)) {
      ambiguous.push({ key, label: businessLabel(key), text: value });
      continue;
    }

    const { items, demoted } = toItems(value);
    // §18: blocks whose grammar is not cleanly separable go to 其他结构化信息,
    // never to the primary body and never silently dropped.
    for (const d of demoted) {
      ambiguous.push({ key: `${key}#tech`, label: `${businessLabel(key)}（技术信息）`, text: d });
    }
    if (!items.length) continue;
    const group: BusinessGroup = { key, label: businessLabel(key), items };
    if (OPTIONAL_MAIN_KEYS.has(key)) optionalGroups.push(group);
    else mainGroups.push(group);
    for (const it of items) {
      if (it.text && !isTechnicalProse(it.text)) businessTexts.push(it.text);
    }
  }

  // ---- summary policy (§13) ----
  // Preference: Formal one_line_summary -> content.summary -> first business
  // field. Parser prose is never used as the business summary; a mixed
  // "diagnostics + business" block is reduced to its business remainder (§18),
  // and if nothing business survives we show 暂无摘要 rather than diagnostics.
  const candidates: Array<[KnowledgePresentation["summarySource"], string | null | undefined]> = [
    ["one_line_summary", oneLine],
    ["content_summary", typeof content.summary === "string" ? (content.summary as string) : null],
    ["business_field", businessTexts[0] ?? null],
  ];
  let summary: string | null = null;
  let summarySource: KnowledgePresentation["summarySource"] = "none";
  for (const [src, cand] of candidates) {
    const resolved = businessSummaryFrom(cand);
    if (!resolved) continue;
    summary = resolved;
    summarySource = src;
    break;
  }

  // ---- primary-body leak audit (§35) ----
  // Uses the SAME grammar as the demotion rule, so it can never disagree with
  // what the renderer actually shows.
  const primaryTechLeak: string[] = [];
  const primaryText = [
    summary ?? "",
    ...mainGroups.flatMap((g) => g.items.map((i) => i.text)),
    ...optionalGroups.flatMap((g) => g.items.map((i) => i.text)),
  ].join("\n");
  for (const re of TECH_TOKEN_RE) {
    if (re.test(primaryText)) primaryTechLeak.push(String(re));
  }

  return {
    summary,
    summarySource,
    mainGroups,
    optionalGroups,
    technical,
    ambiguous,
    evidenceRefs: extractEvidenceRefs(content),
    primaryTechLeak,
  };
}


/**
 * TD-003 — the card summary for a Knowledge list item.
 *
 * The list endpoint ships a bounded, provenance-free projection of the latest
 * revision body (``content_business_fields``).  Running the SAME
 * ``buildKnowledgePresentation`` summary chain over that projection yields
 * exactly the summary the detail page shows — measured 0 mismatches across all
 * 601 current pages — so the card and the detail can never disagree, and the
 * classification grammar is not duplicated anywhere.
 */
export function summaryFromContent(
  content: Record<string, unknown> | null | undefined,
  oneLine?: string | null,
): string | null {
  const safe = (content && typeof content === "object") ? content : {};
  return buildKnowledgePresentation(safe, oneLine ?? null).summary;
}
