import { useMemo, useState } from "react";
import type { DecisionItem, Impact, PmCallbacks } from "../types";
import { shortDateTime } from "../time";
import { EmptyState, SourceDetail } from "./common";

const IMPACT: Record<Impact, { rank: number; label: string }> = {
  high: { rank: 0, label: "影响：大" },
  medium: { rank: 1, label: "影响：中" },
  low: { rank: 2, label: "影响：小" },
};

/** Sorted by impact (high → low), then by due date, then oldest first. */
export function sortDecisions(items: DecisionItem[]): DecisionItem[] {
  return [...items].sort((a, b) =>
    (IMPACT[a.impact]?.rank ?? 3) - (IMPACT[b.impact]?.rank ?? 3)
    || (a.due_at ?? "9999").localeCompare(b.due_at ?? "9999")
    || a.created_at.localeCompare(b.created_at));
}

export function DecisionCard({ item, selected, onSelect, cb, busy }: {
  item: DecisionItem; selected: boolean; onSelect: (on: boolean) => void; cb?: PmCallbacks; busy: boolean;
}) {
  const [showSources, setShowSources] = useState(false);
  return (
    <article className={`pm-decision pm-impact-${item.impact}`} aria-label={item.question} data-decision-id={item.id}>
      <div className="pm-decision-head">
        <label className="pm-check">
          <input type="checkbox" checked={selected} onChange={(e) => onSelect(e.target.checked)} />
          <span className="pm-visually-hidden">选择此项以批量处理</span>
        </label>
        <div className="pm-decision-title">
          <span className="pm-eyebrow">需要你决定{item.project_name ? ` · ${item.project_name}` : ""}</span>
          <h3>{item.question}</h3>
        </div>
        <span className={`pm-impact pm-impact-tag-${item.impact}`}>
          <span aria-hidden="true">{item.impact === "high" ? "▲" : item.impact === "medium" ? "◆" : "·"}</span>
          {IMPACT[item.impact]?.label ?? item.impact}
        </span>
      </div>
      {item.excerpt ? <p className="pm-decision-excerpt">原文：{item.excerpt}</p> : null}
      <dl className="pm-decision-outcomes">
        <div>
          <dt>选「{item.yes_label || "是"}」</dt>
          <dd>{item.if_yes}</dd>
        </div>
        <div>
          <dt>选「{item.no_label || "否"}」</dt>
          <dd>{item.if_no}</dd>
        </div>
      </dl>
      {item.impact_note ? <p className="pm-decision-note">{item.impact_note}</p> : null}
      <div className="pm-decision-foot">
        <div className="pm-decision-actions">
          <button type="button" className="pm-btn pm-btn-primary" disabled={busy}
            onClick={() => cb?.onDecide?.([item.id], "yes")}>{item.yes_label || "是"}</button>
          <button type="button" className="pm-btn" disabled={busy}
            onClick={() => cb?.onDecide?.([item.id], "no")}>{item.no_label || "否"}</button>
          <button type="button" className="pm-btn pm-btn-quiet" disabled={busy}
            onClick={() => cb?.onDecide?.([item.id], "later")}>稍后</button>
        </div>
        <div className="pm-decision-meta">
          {item.sources.length > 0 && (
            <button type="button" className="pm-linkish" aria-expanded={showSources}
              onClick={() => setShowSources((v) => !v)}>
              依据 {item.sources.length} 条
            </button>
          )}
          <span className="pm-meta">{item.due_at ? `需在 ${shortDateTime(item.due_at)} 前决定` : `提出于 ${shortDateTime(item.created_at)}`}</span>
        </div>
      </div>
      {showSources && (
        <div className="pm-decision-sources">
          {item.sources.map((s) => <SourceDetail key={s.id} source={s} cb={cb} />)}
        </div>
      )}
    </article>
  );
}

/** Card inbox with batch handling. */
export function DecisionInbox({ items, cb }: { items: DecisionItem[]; cb?: PmCallbacks }) {
  const sorted = useMemo(() => sortDecisions(items), [items]);
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  if (!sorted.length) {
    return <EmptyState seed="decisions-empty" text="现在没有需要你决定的事项。新的问题出现时会排在这里。" />;
  }
  const live = picked.filter((id) => sorted.some((d) => d.id === id));
  const batch = async (choice: "yes" | "no" | "later") => {
    if (!live.length || !cb?.onDecide) return;
    setBusy(true);
    try { await cb.onDecide(live, choice); setPicked([]); } finally { setBusy(false); }
  };
  return (
    <div className="pm-inbox">
      <div className={`pm-batch ${live.length ? "is-active" : ""}`} aria-live="polite">
        <label className="pm-check">
          <input type="checkbox" checked={live.length === sorted.length}
            onChange={(e) => setPicked(e.target.checked ? sorted.map((d) => d.id) : [])} />
          <span>{live.length ? `已选 ${live.length} 项` : `全部 ${sorted.length} 项 · 按影响排序`}</span>
        </label>
        {live.length > 0 && (
          <div className="pm-batch-actions">
            <button type="button" className="pm-btn pm-btn-primary" disabled={busy} onClick={() => batch("yes")}>全部选「是」</button>
            <button type="button" className="pm-btn" disabled={busy} onClick={() => batch("no")}>全部选「否」</button>
            <button type="button" className="pm-btn pm-btn-quiet" disabled={busy} onClick={() => batch("later")}>全部稍后</button>
          </div>
        )}
      </div>
      {sorted.map((d) => (
        <DecisionCard key={d.id} item={d} cb={cb} busy={busy}
          selected={live.includes(d.id)}
          onSelect={(on) => setPicked((cur) => on ? [...cur, d.id] : cur.filter((x) => x !== d.id))} />
      ))}
    </div>
  );
}
