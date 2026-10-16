/* Phone layout (≤640px): the Gantt becomes a vertical hand-drawn timeline.
   Time runs top → bottom, the "今天" marker sits between past and future,
   undated items gather in a dashed "日期待补" box at the end.             */
import { useMemo, useState, type ReactNode } from "react";
import type { ChangeEntry, ISODate, Milestone, PmCallbacks, PortfolioRow, ProjectEvent, ProjectEventType } from "../types";
import { dayNumber, dayOfTimestamp, makeWindow, shortDate, shortDateTime } from "../time";
import { HATCH, Ink, PEN, roughCircle, roughLine, roughRect } from "../rough";
import { ProjectStatusMark, SourceDetail, useWidth } from "./common";
import { EVENT_META, EventGlyph } from "./icons";
import { changeNumbers, dateRange, delayDays, milestoneOrder, MilestoneStatus } from "./Gantt";

type Node = "dot" | "open" | "done" | "delay" | "change" | ProjectEventType;

export interface VItem {
  id: string;
  /** Day number (see time.ts); null = date unknown → goes to "日期待补" */
  day: number | null;
  node: Node;
  dateText: string;
  /** Short text inside the node (used for change numbers) */
  nodeLabel?: string;
  content: ReactNode;
}

/** A vertical rough stroke, stretched to its row height. */
function Spine({ seed, dashed }: { seed: string; dashed?: boolean }) {
  const paths = useMemo(() => roughLine(`spine-${seed}`, 6, 0, 6, 100, { ...PEN.inkLight, roughness: 0.7 }), [seed]);
  return (
    <svg className="pm-vt-spine" viewBox="0 0 12 100" preserveAspectRatio="none" aria-hidden="true">
      <g style={{ vectorEffect: "non-scaling-stroke" }}>
        {paths.map((p, i) => (
          <path key={i} d={p.d} stroke={p.stroke} strokeWidth={1} fill="none" vectorEffect="non-scaling-stroke"
            strokeDasharray={dashed ? "3 4" : undefined} />
        ))}
      </g>
    </svg>
  );
}

function NodeMark({ node, seed, label }: { node: Node; seed: string; label?: string }) {
  if (node === "new_source" || node === "conflict" || node === "decision" || node === "milestone_done") {
    return <svg className="pm-vt-node" width="20" height="20" viewBox="-10 -10 20 20" aria-hidden="true"><circle r="9" fill="#FCFAF6" /><EventGlyph type={node} size={13} /></svg>;
  }
  const pen = node === "delay" ? PEN.clay : node === "change" ? PEN.sage : PEN.ink;
  const fill =
    node === "done" ? { fill: "#8C8A7E", fillStyle: "solid" }
      : node === "delay" ? { ...HATCH.delay, hachureGap: 2.6 }
        : { fill: "#FCFAF6", fillStyle: "solid" };
  const d = node === "change" ? 17 : 12;
  return (
    <svg className="pm-vt-node" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
      <Ink paths={roughCircle(`node-${seed}`, 10, 10, d, { ...pen, ...fill })} />
      {node === "change" && label ? <text x="10" y="13.5" textAnchor="middle" className="pm-change-num">{label}</text> : null}
    </svg>
  );
}

export function VTimeline({ today, items, seed, emptyText, undatedTitle = "日期待补" }: {
  today: ISODate; items: VItem[]; seed: string; emptyText: string; undatedTitle?: string;
}) {
  const t = dayNumber(today);
  const dated = items.filter((i) => i.day !== null).sort((a, b) => (a.day as number) - (b.day as number));
  const undated = items.filter((i) => i.day === null);
  if (!items.length) return <p className="pm-vt-empty">{emptyText}</p>;
  const cut = dated.findIndex((i) => (i.day as number) > t);
  const before = cut === -1 ? dated : dated.slice(0, cut);
  const after = cut === -1 ? [] : dated.slice(cut);
  const row = (i: VItem, phase: "past" | "future") => (
    <li key={i.id} className={`pm-vt-item is-${phase}`}>
      <span className="pm-vt-date">{i.dateText}</span>
      <span className="pm-vt-rail"><Spine seed={`${seed}-${i.id}`} dashed={phase === "future"} /><NodeMark node={i.node} seed={`${seed}-${i.id}`} label={i.nodeLabel} /></span>
      <div className="pm-vt-content">{i.content}</div>
    </li>
  );
  return (
    <div className="pm-vt">
      <ol className="pm-vt-list">
        {before.map((i) => row(i, "past"))}
        <li className="pm-vt-today" aria-label={`今天 ${shortDate(today)}`}>
          <span className="pm-vt-date pm-hand">今天</span>
          <span className="pm-vt-rail"><TodayTick seed={`${seed}-today-${today}`} /></span>
          <span className="pm-vt-today-label">{shortDate(today)}</span>
        </li>
        {after.map((i) => row(i, "future"))}
      </ol>
      {undated.length > 0 && (
        <div className="pm-vt-undated">
          <DashedBox seed={`${seed}-undated`} />
          <span className="pm-hand pm-vt-undated-title">{undatedTitle}</span>
          <ul>{undated.map((i) => <li key={i.id}>{i.content}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

function TodayTick({ seed }: { seed: string }) {
  const paths = useMemo(() => roughLine(seed, 0, 6, 220, 6, { ...PEN.clay, strokeWidth: 1.3 }), [seed]);
  return <svg className="pm-vt-today-line" width="220" height="12" viewBox="0 0 220 12" aria-hidden="true"><Ink paths={paths} dash="5 5" /></svg>;
}

/** Dashed rough frame that fills its (relative) parent. */
function DashedBox({ seed }: { seed: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [h, setH] = useState(0);
  return (
    <div className="pm-vt-dashbox" ref={(el) => {
      (ref as { current: HTMLDivElement | null }).current = el;
      if (el && el.offsetHeight !== h) setH(el.offsetHeight);
    }}>
      {width > 0 && h > 0 && (
        <svg width={width} height={h} aria-hidden="true">
          <Ink paths={roughRect(`${seed}-${width}-${h}`, 1.5, 1.5, width - 3, h - 3, { ...PEN.inkLight })} dash="3 5" />
        </svg>
      )}
    </div>
  );
}

/** Thin hand-drawn progress strip (outline = whole period, hatch = completed). */
export function ProgressStrip({ id, progress }: { id: string; progress: number | null }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const h = 12;
  return (
    <div className="pm-strip" ref={ref} aria-hidden="true">
      {width > 0 && (
        <svg width={width} height={h + 4}>
          {progress !== null && progress > 0 && (
            <Ink paths={roughRect(`${id}-sp-${width}`, 2, 2, Math.max(1, (width - 4) * Math.min(1, progress)), h, { stroke: "none", ...HATCH.progress })} />
          )}
          <Ink paths={roughRect(`${id}-sf-${width}`, 2, 2, width - 4, h, { ...(progress === null ? PEN.inkLight : PEN.ink) })} dash={progress === null ? "3 5" : undefined} />
        </svg>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ portfolio (phone) */

export function PortfolioTimeline({ today, rows, cb }: { today: ISODate; rows: PortfolioRow[]; cb?: PmCallbacks }) {
  const win = useMemo(() => makeWindow(today, "week"), [today]);
  const [openId, setOpenId] = useState<string | null>(null);
  const items: VItem[] = [];
  for (const p of rows) {
    const push = (it: VItem) => { if (it.day === null || (it.day >= win.start && it.day < win.end)) items.push(it); };
    if (p.start) push({ id: `${p.id}-start`, day: dayNumber(p.start), node: "open", dateText: shortDate(p.start), content: <span className="pm-vt-line"><strong>{p.name}</strong> 开始</span> });
    if (p.end) push({ id: `${p.id}-end`, day: dayNumber(p.end), node: "dot", dateText: shortDate(p.end), content: <span className="pm-vt-line"><strong>{p.name}</strong> 计划结束</span> });
    for (const ev of p.events) {
      push({
        id: ev.id, day: dayNumber(ev.date), node: ev.type, dateText: shortDate(ev.date),
        content: <EventLine ev={ev} project={p.name} open={openId === ev.id} onToggle={() => setOpenId(openId === ev.id ? null : ev.id)} cb={cb} />,
      });
    }
  }
  return (
    <div className="pm-portfolio-phone">
      <ul className="pm-project-cards">
        {rows.map((p) => (
          <li key={p.id} className="pm-project-card">
            <div className="pm-project-card-head">
              {p.href && cb?.onNavigate
                ? <button type="button" className="pm-row-name" onClick={() => cb.onNavigate?.(p.href as string)}>{p.name}</button>
                : <span className="pm-row-name">{p.name}</span>}
              <ProjectStatusMark status={p.status} />
            </div>
            <p className="pm-row-oneliner">{p.one_liner ?? "暂无现状说明"}</p>
            {p.status === "empty" ? (
              <p className="pm-meta">还没有周期和里程碑，系统会从新资料中提取</p>
            ) : (
              <div className="pm-project-card-strip">
                <ProgressStrip id={p.id} progress={p.progress} />
                <span className="pm-meta">
                  {p.progress !== null ? `${Math.round(p.progress * 100)}% · ` : "进度未知 · "}
                  {p.start || p.end ? `${shortDate(p.start)} – ${shortDate(p.end)}` : "周期待补"}
                </span>
              </div>
            )}
          </li>
        ))}
      </ul>
      <h3 className="pm-subhead">近期时间线 <span className="pm-meta">前 4 周 · 后 8 周</span></h3>
      <VTimeline today={today} items={items} seed="pf" emptyText="这段时间没有事件。" />
    </div>
  );
}

function EventLine({ ev, project, open, onToggle, cb }: { ev: ProjectEvent; project?: string; open: boolean; onToggle: () => void; cb?: PmCallbacks }) {
  return (
    <div className="pm-vt-event">
      <button type="button" className="pm-vt-event-btn" aria-expanded={open} onClick={onToggle}>
        <span className="pm-eyebrow">{EVENT_META[ev.type].label}{project ? ` · ${project}` : ""}</span>
        <span>{ev.title}</span>
      </button>
      {open && (
        <div className="pm-vt-detail">
          {ev.source ? <SourceDetail source={ev.source} cb={cb} /> : <span className="pm-meta">没有附带来源</span>}
          {ev.href && cb?.onNavigate ? <button type="button" className="pm-btn pm-btn-small" onClick={() => cb.onNavigate?.(ev.href as string)}>打开</button> : null}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ milestones (phone) */

export function MilestoneTimeline({ today, milestones, changes, cb, focusChange, onFocusChange }: {
  today: ISODate; milestones: Milestone[]; changes: ChangeEntry[]; cb?: PmCallbacks;
  focusChange?: string | null; onFocusChange?: (id: string | null) => void;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const items: VItem[] = [];
  for (const m of milestoneOrder(milestones)) {
    const delay = delayDays(m, today);
    const anchor = m.plan_end ?? m.plan_start;
    const node: Node = m.status === "done" ? "done" : delay ? "delay" : m.status === "in_progress" ? "dot" : "open";
    items.push({
      id: m.id,
      day: anchor ? dayNumber(anchor) : null,
      node,
      dateText: anchor ? shortDate(anchor) : "日期待补",
      content: (
        <div className="pm-vt-ms">
          <button type="button" className="pm-vt-event-btn" aria-expanded={openId === m.id} onClick={() => setOpenId(openId === m.id ? null : m.id)}>
            <span className="pm-vt-ms-title">{m.title}</span>
            <MilestoneStatus m={m} />
          </button>
          <span className="pm-meta">
            计划 {dateRange(m.plan_start, m.plan_end)}
            {m.actual_end ? ` · 实际完成 ${shortDate(m.actual_end)}` : m.actual_start ? ` · 实际 ${shortDate(m.actual_start)} 起` : ""}
          </span>
          {delay ? <span className="pm-clay pm-hand">延期 {delay} 天</span> : null}
          {openId === m.id && (
            <div className="pm-vt-detail">
              {m.note ? <span>{m.note}</span> : null}
              {m.source ? <SourceDetail source={m.source} cb={cb} /> : <span className="pm-meta">没有附带来源</span>}
            </div>
          )}
        </div>
      ),
    });
  }
  for (const { n, change } of changeNumbers(changes)) {
    const d = dayOfTimestamp(change.at);
    items.push({
      id: `chg-${change.id}`, day: d, node: "change", nodeLabel: String(n), dateText: shortDateTime(change.at).split(" ")[0],
      content: (
        <button type="button" className={`pm-vt-change ${focusChange === change.id ? "is-active" : ""}`} onClick={() => onFocusChange?.(change.id)}>
          <span className="pm-eyebrow">变化 {n}{change.reverted ? " · 已撤销" : ""}</span>
          <span>{change.title}</span>
        </button>
      ),
    });
  }
  return <VTimeline today={today} items={items} seed="ms" emptyText="该项目还没有里程碑，系统会从新资料中提取。" />;
}
