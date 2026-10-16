/* 项目页「活的项目档案」 — sections, their order and titles come from vm.sections. */
import { useState, type ReactNode } from "react";
import type { PmCallbacks, ProjectViewModel, SectionConfig, SectionKind, TimeUnit } from "../types";
import { shortDateTime } from "../time";
import { EmptyState, ProjectStatusMark, usePhone } from "../components/common";
import { MilestoneGantt } from "../components/Gantt";
import { MilestoneTimeline } from "../components/VerticalTimeline";
import { ChangeList, ConflictList, EntryList, EntryItem, MetricList, SectionCard, SourceList } from "../components/Sections";
import { ReceiptList, AiSuggestionList } from "../components/AiSuggestions";
import { ProjectHealth } from "../components/ProjectHealth";

export const DEFAULT_EMPTY: Record<SectionKind, string> = {
  summary: "还没有现状说明，系统会从新资料中提取。",
  metrics: "还没有已确认指标，目标与指标需要你确认。",
  milestones: "该项目还没有里程碑，系统会从新资料中提取。",
  gaps: "暂未发现缺口。",
  next_steps: "还没有下一步安排。",
  risks: "暂无风险与冲突。",
  changes: "最近没有变化。",
  sources: "还没有关联的资料。",
  list: "暂无内容。",
};

const HALF: SectionKind[] = ["gaps", "next_steps", "list", "sources"];

export function ProjectDossier({ vm, cb, initialUnit = "week", backHref, backLabel = "今日驾驶舱", modelConfigured = false }: {
  vm: ProjectViewModel; cb?: PmCallbacks; initialUnit?: TimeUnit; backHref?: string | null; backLabel?: string; modelConfigured?: boolean;
}) {
  const phone = usePhone();
  const [unit, setUnit] = useState<TimeUnit>(initialUnit);
  const [focus, setFocus] = useState<string | null>(null);
  const p = vm.project;
  const openConflicts = vm.conflicts.filter((c) => !c.resolved).length;

  const focusChange = (id: string | null) => {
    setFocus(id);
    if (id) document.getElementById(`pm-change-${id}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };

  const render = (s: SectionConfig): ReactNode => {
    const empty = s.empty_text || DEFAULT_EMPTY[s.kind] || DEFAULT_EMPTY.list;
    const entries = vm.entries[s.key] ?? [];
    switch (s.kind) {
      case "summary":
        if (!entries.length && !p.one_liner) return <EmptyState seed="summary-empty" text={empty} />;
        return entries.length
          ? <ul className="pm-entries is-summary">{entries.map((e) => <EntryItem key={e.id} entry={e} cb={cb} changes={vm.changes} />)}</ul>
          : <p className="pm-summary-text">{p.one_liner}</p>;
      case "metrics":
        return <>{entries.length ? <EntryList entries={entries} cb={cb} changes={vm.changes} emptyText={empty} seed={`e-${s.key}`} /> : null}<MetricList metrics={vm.metrics} cb={cb} emptyText={empty} /></>;
      case "milestones":
        return <>{entries.length ? <EntryList entries={entries} cb={cb} changes={vm.changes} emptyText={empty} seed={`e-${s.key}`} /> : null}{!vm.milestones.length ? <EmptyState seed="ms-empty" text={empty} /> : phone
          ? <MilestoneTimeline today={vm.today} milestones={vm.milestones} changes={vm.changes} cb={cb} focusChange={focus} onFocusChange={focusChange} />
          : <MilestoneGantt today={vm.today} milestones={vm.milestones} changes={vm.changes} unit={unit} onUnitChange={setUnit} cb={cb} focusChange={focus} onFocusChange={focusChange} />}</>;
      case "risks":
        if (!vm.conflicts.length && !entries.length) return <EmptyState seed="risks-empty" text={empty} />;
        return (
          <>
            {vm.conflicts.length ? <ConflictList conflicts={vm.conflicts} cb={cb} emptyText="" /> : null}
            {entries.length ? <EntryList entries={entries} cb={cb} changes={vm.changes} emptyText={empty} seed={`e-${s.key}`} /> : null}
          </>
        );
      case "changes":
        return <>{entries.length ? <EntryList entries={entries} cb={cb} changes={vm.changes} emptyText={empty} seed={`e-${s.key}`} /> : null}<ChangeList changes={vm.changes} cb={cb} focus={focus} onFocus={setFocus} emptyText={empty} /></>;
      case "sources":
        return <>{entries.length ? <EntryList entries={entries} cb={cb} changes={vm.changes} emptyText={empty} seed={`e-${s.key}`} /> : null}<SourceList sources={vm.sources} cb={cb} emptyText={empty} /></>;
      default:
        return <EntryList entries={entries} cb={cb} changes={vm.changes} emptyText={empty} seed={`e-${s.key}`} />;
    }
  };

  return (
    <div className="pm-root pm-page pm-project-page" data-pm-layout={phone ? 'phone' : 'desktop'}>
      <header className="pm-page-head is-project">
        <div className="pm-page-title">
          {backHref && cb?.onNavigate ? (
            <button type="button" className="pm-back" onClick={() => cb.onNavigate?.(backHref)}>← {backLabel}</button>
          ) : null}
          <span className="pm-eyebrow">项目档案</span>
          <h1>{p.name}</h1>
          <div className="pm-project-meta">
            <ProjectStatusMark status={p.status} />
            {openConflicts ? <span className="pm-status pm-tone-warn">▲ {openConflicts} 处冲突待裁决</span> : null}
            <span className="pm-meta">{p.updated_at ? `档案更新于 ${shortDateTime(p.updated_at)}` : "尚未更新"}</span>
          </div>
        </div>
      </header>

      {vm.receipts.length ? (
        <div className="pm-block pm-receipts-inline" aria-label="刚归入的资料">
          <ReceiptList items={vm.receipts} cb={cb} />
        </div>
      ) : null}

      {p.status === "empty" ? (
        <p className="pm-notice">这个项目刚建立，还没有内容。新资料归入后，系统会逐栏补充；补充前不会显示任何推测的日期或进度。</p>
      ) : null}

      {!modelConfigured ? <p className="pm-notice">需要填写 Key 才能生成新结论；已有正式记录和来源可直接查看。</p> : null}
      <section className="pm-block" aria-label="AI 建议">
        <h2>AI 建议</h2><p className="pm-meta">基于资料的推断；转为候选并核准后，才会成为正式待办。</p>
        {cb?.onGenerateSuggestions ? <button className="pm-btn" disabled={!modelConfigured} onClick={() => cb.onGenerateSuggestions?.(p.id)}>重新生成建议</button> : null}
        <AiSuggestionList items={vm.ai_suggestions ?? []} cb={cb}/>
      </section>
      <ProjectHealth checks={vm.health_checks??[]} gaps={vm.knowledge_gaps??[]} cb={cb} projectId={p.id}/>
      <div className="pm-sections">
        {vm.sections.map((s) => {
          const span = s.span ?? (HALF.includes(s.kind) ? "half" : "full");
          const attention = s.kind === "risks" && openConflicts > 0;
          const extra = s.kind === "changes" && vm.changes.length
            ? <span className="pm-meta">编号与进度图中的圆点对应</span>
            : s.kind === "risks" && openConflicts ? <span className="pm-status pm-tone-warn">▲ 需要裁决</span> : null;
          return (
            <div key={s.key} className={`pm-section-slot is-${span}`}>
              <SectionCard id={s.key} title={s.title} extra={extra} tone={attention ? "attention" : "default"}>
                {render(s)}
              </SectionCard>
            </div>
          );
        })}
      </div>

      <footer className="pm-page-foot">数据生成于 {shortDateTime(vm.generated_at)}</footer>
    </div>
  );
}
