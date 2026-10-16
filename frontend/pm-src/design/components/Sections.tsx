/* Project dossier building blocks: section card, entries with source/history/undo,
   metrics with a hand-drawn trend, changes list, side-by-side conflicts.      */
import { useMemo, useState, type ReactNode } from "react";
import type { ChangeEntry, Conflict, Metric, PmCallbacks, SectionEntry, SourceRef } from "../types";
import { shortDate, shortDateTime } from "../time";
import { Ink, PEN, roughCircle, roughLinear, roughLine } from "../rough";
import { EmptyState, SourceDetail, SourceLink } from "./common";
import { changeNumbers } from "./Gantt";

/** A paper card with a serif title. */
export function SectionCard({ id, title, extra, children, tone }: {
  id: string; title: string; extra?: ReactNode; children: ReactNode; tone?: "default" | "attention";
}) {
  return (
    <section className={`pm-card ${tone === "attention" ? "is-attention" : ""}`} aria-labelledby={`pm-sec-${id}`}>
      <header className="pm-card-head">
        <h2 id={`pm-sec-${id}`}>{title}</h2>
        {extra ? <div className="pm-card-extra">{extra}</div> : null}
      </header>
      <div className="pm-card-body">{children}</div>
    </section>
  );
}

const TAG_SYMBOL: Record<string, string> = { neutral: "·", warn: "▲", danger: "■", ok: "●" };

/** One conclusion. Expand → sources and history; "撤销" when the last change is revertible. */
export function EntryItem({ entry, cb, changes }: { entry: SectionEntry; cb?: PmCallbacks; changes?: ChangeEntry[] }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const change = entry.revert_change_id ? changes?.find((c) => c.id === entry.revert_change_id) : undefined;
  const canRevert = !!entry.revert_change_id && (change ? change.revertible && !change.reverted : true);
  const revert = async () => {
    if (!entry.revert_change_id || !cb?.onRevert) return;
    setBusy(true);
    try { await cb.onRevert(entry.revert_change_id); } finally { setBusy(false); }
  };
  return (
    <li className={`pm-entry ${entry.kind === "suggestion" ? "is-suggestion" : ""}`}>
      {entry.kind === "suggestion" ? <span className="pm-ai-label">AI 建议 · 不是事实</span> : null}
      <div className="pm-entry-main">
        {entry.tag ? <span className={`pm-tag pm-tag-${entry.tag.tone}`}><span aria-hidden="true">{TAG_SYMBOL[entry.tag.tone]}</span> {entry.tag.text}</span> : null}
        <p>{entry.text}</p>
      </div>
      <div className="pm-entry-foot">
        <button type="button" className="pm-linkish" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? "收起" : `来源 ${entry.sources.length}${entry.history?.length ? ` · 历史 ${entry.history.length}` : ""}`}
        </button>
        <span className="pm-meta">{entry.updated_at ? `更新于 ${shortDateTime(entry.updated_at)}` : "更新时间未知"}</span>
        {canRevert && cb?.onRevert ? (
          <button type="button" className="pm-btn pm-btn-small pm-btn-quiet" disabled={busy} onClick={revert}>撤销</button>
        ) : null}
      </div>
      {open && (
        <div className="pm-entry-more">
          {entry.sources.length ? entry.sources.map((s) => <SourceDetail key={s.id} source={s} cb={cb} />) : <span className="pm-meta">没有附带来源</span>}
          {entry.history?.length ? (
            <ol className="pm-history" aria-label="历史版本">
              {entry.history.map((h, i) => (
                <li key={i}>
                  <span className="pm-meta">{shortDateTime(h.at)}</span>
                  <span className="pm-history-text">{h.text}</span>
                  {h.source ? <SourceLink source={h.source} cb={cb} /> : null}
                </li>
              ))}
            </ol>
          ) : null}
        </div>
      )}
    </li>
  );
}

export function EntryList({ entries, cb, changes, emptyText, seed }: {
  entries: SectionEntry[]; cb?: PmCallbacks; changes?: ChangeEntry[]; emptyText: string; seed: string;
}) {
  if (!entries.length) return <EmptyState seed={seed} text={emptyText} />;
  // facts first, suggestions after — never interleaved
  const facts = entries.filter((e) => e.kind !== "suggestion");
  const sugg = entries.filter((e) => e.kind === "suggestion");
  return (
    <ul className="pm-entries">
      {facts.map((e) => <EntryItem key={e.id} entry={e} cb={cb} changes={changes} />)}
      {sugg.map((e) => <EntryItem key={e.id} entry={e} cb={cb} changes={changes} />)}
    </ul>
  );
}

/* ------------------------------------------------------------ metrics */

function fmt(v: number, unit: string | null) {
  const s = Math.abs(v) >= 1000 ? v.toLocaleString("zh-CN") : String(Math.round(v * 100) / 100);
  return unit ? (unit === "%" ? `${s}%` : `${s} ${unit}`) : s;
}

/** Hand-drawn sparkline; the target is a faint dashed rule. */
function Trend({ metric }: { metric: Metric }) {
  const W = 132;
  const H = 40;
  const paths = useMemo(() => {
    const pts = metric.trend;
    if (pts.length < 2) return null;
    const vals = pts.map((p) => p.value).concat(metric.target ? [metric.target.value] : []);
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const span = hi - lo || 1;
    const y = (v: number) => H - 6 - ((v - lo) / span) * (H - 12);
    const x = (i: number) => 4 + (i / (pts.length - 1)) * (W - 8);
    const line = roughLinear(`trend-${metric.id}`, pts.map((p, i) => [x(i), y(p.value)]), { ...PEN.sage, strokeWidth: 1.3 });
    const last = roughCircle(`trend-end-${metric.id}`, x(pts.length - 1), y(pts[pts.length - 1].value), 6, { ...PEN.ink, fill: "#5C5750", fillStyle: "solid" });
    const target = metric.target ? roughLine(`trend-t-${metric.id}`, 2, y(metric.target.value), W - 2, y(metric.target.value), { ...PEN.inkLight }) : [];
    return { line, last, target };
  }, [metric]);
  if (!paths) return <span className="pm-meta pm-trend-none">趋势数据不足</span>;
  return (
    <svg className="pm-trend" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img"
      aria-label={`趋势：${metric.trend.map((p) => `${p.period} ${fmt(p.value, metric.unit)}`).join("，")}`}>
      <Ink paths={paths.target} dash="3 4" />
      <Ink paths={paths.line} />
      <Ink paths={paths.last} />
    </svg>
  );
}

function direction(m: Metric): { text: string; tone: string } | null {
  const t = m.trend;
  if (t.length < 2) return null;
  const d = t[t.length - 1].value - t[t.length - 2].value;
  if (d === 0) return { text: "→ 持平", tone: "muted" };
  const up = d > 0;
  const good = m.higher_is_better === null ? null : up === m.higher_is_better;
  return { text: `${up ? "↑ 上升" : "↓ 下降"} ${fmt(Math.abs(d), m.unit)}`, tone: good === null ? "muted" : good ? "ok" : "warn" };
}

export function MetricList({ metrics, cb, emptyText }: { metrics: Metric[]; cb?: PmCallbacks; emptyText: string }) {
  if (!metrics.length) return <EmptyState seed="metrics-empty" text={emptyText} />;
  return (
    <ul className="pm-metrics">
      {metrics.map((m) => {
        const dir = direction(m);
        return (
          <li key={m.id} className="pm-metric">
            <div className="pm-metric-name">{m.name}</div>
            <div className="pm-metric-values">
              <div>
                <span className="pm-meta">当前</span>
                <strong>{m.current ? fmt(m.current.value, m.unit) : "待补"}</strong>
                <span className="pm-meta">{m.current?.period ?? (m.current?.as_of ? shortDate(m.current.as_of) : "")}</span>
              </div>
              <div>
                <span className="pm-meta">目标</span>
                <strong className="is-target">{m.target ? fmt(m.target.value, m.unit) : "待补"}</strong>
                <span className="pm-meta">{m.target?.period ?? ""}</span>
              </div>
            </div>
            <div className="pm-metric-trend">
              <Trend metric={m} />
              {dir ? <span className={`pm-status pm-tone-${dir.tone}`}>{dir.text}</span> : null}
            </div>
            {m.source ? <div className="pm-metric-source"><SourceLink source={m.source} cb={cb} /></div> : null}
          </li>
        );
      })}
    </ul>
  );
}

/* ------------------------------------------------------------ changes */

export function ChangeList({ changes, cb, focus, onFocus, emptyText }: {
  changes: ChangeEntry[]; cb?: PmCallbacks; focus?: string | null; onFocus?: (id: string | null) => void; emptyText: string;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  if (!changes.length) return <EmptyState seed="changes-empty" text={emptyText} />;
  const list = changeNumbers(changes).reverse(); // newest on top, numbers stay chronological
  return (
    <ol className="pm-changes">
      {list.map(({ n, change: c }) => (
        <li key={c.id} id={`pm-change-${c.id}`} className={`pm-change ${focus === c.id ? "is-focus" : ""} ${c.reverted ? "is-reverted" : ""}`}
          onMouseEnter={() => onFocus?.(c.id)}>
          <span className="pm-change-badge" aria-label={`编号 ${n}`}>{n}</span>
          <div className="pm-change-body">
            <div className="pm-change-title">
              <strong>{c.title}</strong>
              <span className="pm-meta">{shortDateTime(c.at)}</span>
            </div>
            {(c.before !== null || c.after !== null) && (
              <div className="pm-diff">
                <span className="pm-diff-before"><span className="pm-meta">原来</span>{c.before ?? "（空）"}</span>
                <span className="pm-diff-arrow" aria-hidden="true">→</span>
                <span className="pm-diff-after"><span className="pm-meta">现在</span>{c.after ?? "（空）"}</span>
              </div>
            )}
            <div className="pm-change-foot">
              {c.source ? <SourceLink source={c.source} cb={cb} /> : <span className="pm-meta">来源未知</span>}
              {c.reverted ? <span className="pm-meta">已撤销</span>
                : c.revertible && cb?.onRevert ? (
                  <button type="button" className="pm-btn pm-btn-small pm-btn-quiet" disabled={busy === c.id}
                    onClick={async () => { setBusy(c.id); try { await cb.onRevert?.(c.id); } finally { setBusy(null); } }}>撤销</button>
                ) : null}
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}

/* ------------------------------------------------------------ conflicts */

export function ConflictCompare({ conflict, cb }: { conflict: Conflict; cb?: PmCallbacks }) {
  const [busy, setBusy] = useState(false);
  const options = conflict.options?.length ? conflict.options : [
    { key: "left", label: `采用「${conflict.left.label}」` },
    { key: "right", label: `采用「${conflict.right.label}」` },
    { key: "later", label: "稍后再说" },
  ];
  const resolve = async (key: string) => {
    if (!cb?.onResolveConflict) return;
    setBusy(true);
    try { await cb.onResolveConflict(conflict.id, key); } finally { setBusy(false); }
  };
  const side = (s: Conflict["left"], which: string) => (
    <div className={`pm-conflict-side is-${which} ${conflict.resolved?.choice === which ? "is-chosen" : ""}`}>
      <span className="pm-eyebrow">{s.label}</span>
      <p>{s.text}</p>
      <SourceDetail source={s.source} cb={cb} />
    </div>
  );
  return (
    <article className={`pm-conflict ${conflict.resolved ? "is-resolved" : ""}`} aria-label={`冲突：${conflict.topic}`}>
      <header className="pm-conflict-head">
        <span className="pm-conflict-mark" aria-hidden="true">
          <svg width="18" height="18" viewBox="-9 -9 18 18"><ConflictIcon /></svg>
        </span>
        <h3>{conflict.topic}</h3>
        <span className={`pm-status ${conflict.resolved ? "pm-tone-ok" : "pm-tone-warn"}`}>{conflict.resolved ? `✓ 已裁决 · ${shortDateTime(conflict.resolved.at)}` : "▲ 两个来源说法不一致"}</span>
      </header>
      <div className="pm-conflict-grid">
        {side(conflict.left, "left")}
        <div className="pm-conflict-vs" aria-hidden="true"><span>对照</span></div>
        {side(conflict.right, "right")}
      </div>
      {!conflict.resolved && (
        <div className="pm-conflict-actions">
          <span className="pm-meta">以哪一边为准？</span>
          {options.map((o, i) => (
            <button key={o.key} type="button" disabled={busy}
              className={`pm-btn ${i === 0 ? "" : ""} ${o.key === "later" ? "pm-btn-quiet" : ""}`}
              onClick={() => resolve(o.key)}>{o.label}</button>
          ))}
        </div>
      )}
    </article>
  );
}

function ConflictIcon() {
  return (
    <g fill="none" stroke="#A47A42" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M0 -6.4 L6.6 5.6 L-6.6 5.6 Z" fill="#F6EEDF" />
      <path d="M0 -2 L0 1.6" />
      <path d="M0 3.7 L0 3.8" strokeWidth={2} />
    </g>
  );
}

export function ConflictList({ conflicts, cb, emptyText }: { conflicts: Conflict[]; cb?: PmCallbacks; emptyText: string }) {
  if (!conflicts.length) return <EmptyState seed="conflicts-empty" text={emptyText} />;
  const open = conflicts.filter((c) => !c.resolved);
  const done = conflicts.filter((c) => c.resolved);
  return <div className="pm-conflicts">{[...open, ...done].map((c) => <ConflictCompare key={c.id} conflict={c} cb={cb} />)}</div>;
}

/* ------------------------------------------------------------ sources */

export function SourceList({ sources, cb, emptyText }: { sources: SourceRef[]; cb?: PmCallbacks; emptyText: string }) {
  if (!sources.length) return <EmptyState seed="sources-empty" text={emptyText} />;
  const sorted = [...sources].sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
  return (
    <ul className="pm-sources">
      {sorted.map((s) => (
        <li key={s.id}>
          <SourceLink source={s} cb={cb} />
          <span className="pm-meta">{s.updated_at ? shortDateTime(s.updated_at) : "时间未知"}</span>
        </li>
      ))}
    </ul>
  );
}
