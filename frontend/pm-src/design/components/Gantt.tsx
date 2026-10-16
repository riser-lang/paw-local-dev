import { useMemo, useState, type ReactNode } from "react";
import type { ChangeEntry, ISODate, Milestone, PmCallbacks, PortfolioRow, ProjectEvent, TimeUnit } from "../types";
import { dayNumber, dayOfTimestamp, makeScale, makeWindow, shortDate, shortDateTime, type TimeWindow } from "../time";
import { HATCH, Ink, PEN, roughLine, roughLinear, roughRect, roughCircle } from "../rough";
import { ProjectStatusMark, SourceDetail, useWidth } from "./common";
import { EVENT_META, EventGlyph, EventLegend } from "./icons";

const HEAD = 38;      // axis header height
const BAR_H = 16;

export function UnitToggle({ unit, onChange }: { unit: TimeUnit; onChange: (u: TimeUnit) => void }) {
  return (
    <div className="pm-toggle" role="group" aria-label="时间单位">
      <button type="button" aria-pressed={unit === "week"} onClick={() => onChange("week")}>按周</button>
      <button type="button" aria-pressed={unit === "month"} onClick={() => onChange("month")}>按月</button>
    </div>
  );
}

interface FrameRow {
  key: string;
  height: number;
  label: ReactNode;
  side: ReactNode;
  /** Draw the row's chart content. y = row top; w = chart width; x = day → px. */
  draw: (y: number, w: number, x: (day: number) => number) => ReactNode;
}

interface Pop { x: number; y: number; title: string; date: string; kind: string; href: string | null; body?: ReactNode }

/** Three columns: label | hand-drawn chart | one-line status. One SVG spans all rows,
    so the grid, the "today" line and every row share one time axis. */
function GanttFrame({ win, today, rows, labelHead, sideHead, seed, pop, setPop, cb, ariaLabel }: {
  win: TimeWindow; today: ISODate; rows: FrameRow[]; labelHead: string; sideHead: string; seed: string;
  pop: Pop | null; setPop: (p: Pop | null) => void; cb?: PmCallbacks; ariaLabel: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const height = HEAD + rows.reduce((s, r) => s + r.height, 0);
  const x = makeScale(win, Math.max(1, width));
  const t = dayNumber(today);
  const todayX = x(t + 0.5);
  const showToday = t >= win.start && t < win.end;

  const tops: number[] = [];
  rows.reduce((acc, r) => { tops.push(acc); return acc + r.height; }, HEAD);

  const todayInk = useMemo(
    () => (width && showToday ? roughLine(`${seed}-today-${today}-${win.unit}`, todayX, HEAD - 4, todayX, height - 2, { ...PEN.clay, strokeWidth: 1.3 }) : []),
    [width, showToday, todayX, height, seed, today, win.unit],
  );

  return (
    <div className="pm-gantt-frame">
      <div className="pm-gantt-col pm-gantt-labels">
        <div className="pm-gantt-headcell" style={{ height: HEAD }}>{labelHead}</div>
        {rows.map((r) => <div key={r.key} className="pm-gantt-cell" style={{ height: r.height }}>{r.label}</div>)}
      </div>
      <div className="pm-gantt-col pm-gantt-chart" ref={ref}>
        {width > 0 && (
          <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
            {/* paper ruling: week / month columns */}
            {win.ticks.map((tick, i) => {
              const tx = x(tick.day);
              const next = i + 1 < win.ticks.length ? x(win.ticks[i + 1].day) : width;
              const current = t >= tick.day && (i + 1 >= win.ticks.length || t < win.ticks[i + 1].day);
              return (
                <g key={tick.day}>
                  {i > 0 && <line x1={tx} x2={tx} y1={HEAD - 6} y2={height} className="pm-rule" />}
                  <text x={(tx + next) / 2} y={HEAD - 14} textAnchor="middle"
                    className={`pm-tick ${current ? "is-current" : ""} ${tick.major ? "is-major" : ""}`}>{tick.label}</text>
                </g>
              );
            })}
            <line x1={0} x2={width} y1={HEAD - 0.5} y2={HEAD - 0.5} className="pm-rule-strong" />
            {rows.map((r, i) => (
              <g key={r.key}>
                {i > 0 && <line x1={0} x2={width} y1={tops[i]} y2={tops[i]} className="pm-rule-row" />}
                {r.draw(tops[i], width, x)}
              </g>
            ))}
            {showToday && (
              <g className="pm-today" aria-label={`今天 ${shortDate(today)}`}>
                <Ink paths={todayInk} dash="5 5" />
                <text x={todayX + 4} y={HEAD - 26} className="pm-hand pm-today-label">今天</text>
              </g>
            )}
          </svg>
        )}
        {pop && (
          <div className="pm-pop-card is-floating" role="dialog" aria-label={pop.title}
            style={{ left: Math.min(Math.max(8, pop.x - 140), Math.max(8, width - 288)), top: pop.y + 14 }}
            onMouseLeave={() => setPop(null)}>
            <span className="pm-eyebrow">{pop.kind} · {pop.date}</span>
            <strong>{pop.title}</strong>
            {pop.body}
            <span className="pm-pop-actions">
              {pop.href && cb?.onNavigate && (
                <button type="button" className="pm-btn pm-btn-small" onClick={() => cb.onNavigate?.(pop.href as string)}>打开</button>
              )}
              <button type="button" className="pm-btn pm-btn-small pm-btn-quiet" onClick={() => setPop(null)}>关闭</button>
            </span>
          </div>
        )}
      </div>
      <div className="pm-gantt-col pm-gantt-side">
        <div className="pm-gantt-headcell" style={{ height: HEAD }}>{sideHead}</div>
        {rows.map((r) => <div key={r.key} className="pm-gantt-cell" style={{ height: r.height }}>{r.side}</div>)}
      </div>
    </div>
  );
}

/** Clickable event glyph inside the SVG; keyboard reachable. */
function EventMark({ ev, cx, cy, onOpen }: { ev: ProjectEvent; cx: number; cy: number; onOpen: (ev: ProjectEvent, x: number, y: number) => void }) {
  const meta = EVENT_META[ev.type];
  return (
    <g className="pm-event" transform={`translate(${cx},${cy})`} role="button" tabIndex={0}
      aria-label={`${meta.label}：${ev.title}，${shortDate(ev.date)}`}
      onClick={() => onOpen(ev, cx, cy)}
      onMouseEnter={() => onOpen(ev, cx, cy)}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(ev, cx, cy); } }}>
      <circle r={10} className="pm-event-hit" />
      <EventGlyph type={ev.type} size={13} />
    </g>
  );
}

/* =============================================================== portfolio */

export function PortfolioGantt({ today, rows, unit, onUnitChange, cb }: {
  today: ISODate; rows: PortfolioRow[]; unit: TimeUnit; onUnitChange: (u: TimeUnit) => void; cb?: PmCallbacks;
}) {
  const win = useMemo(() => makeWindow(today, unit), [today, unit]);
  const [pop, setPop] = useState<Pop | null>(null);

  const openEvent = (ev: ProjectEvent, x: number, y: number) => setPop({
    x, y, title: ev.title, date: shortDate(ev.date), kind: EVENT_META[ev.type].label, href: ev.href,
    body: ev.source ? <SourceDetail source={ev.source} cb={cb} /> : undefined,
  });

  const frameRows: FrameRow[] = rows.map((p) => ({
    key: p.id,
    height: 58,
    label: (
      <div className="pm-row-label">
        {p.href && cb?.onNavigate
          ? <button type="button" className="pm-row-name" onClick={() => cb.onNavigate?.(p.href as string)}>{p.name}</button>
          : <span className="pm-row-name">{p.name}</span>}
        <ProjectStatusMark status={p.status} />
      </div>
    ),
    side: <p className="pm-row-oneliner">{p.one_liner ?? "暂无现状说明"}</p>,
    draw: (y, w, x) => {
      const mid = y + 58 / 2 + 4;
      const by = mid - BAR_H / 2;
      if (p.status === "empty" || (!p.start && !p.end && !p.events.length)) {
        return <text x={12} y={mid + 4} className="pm-chart-note">还没有周期和里程碑，系统会从新资料中提取</text>;
      }
      const parts: ReactNode[] = [];
      if (p.start && p.end) {
        const s = dayNumber(p.start);
        const e = dayNumber(p.end) + 1;
        const x0 = Math.max(0, x(s));
        const x1 = Math.min(w, x(e));
        if (x1 > x0) {
          if (p.progress !== null && p.progress > 0) {
            const px = Math.min(x1, x(s + (e - s) * Math.min(1, p.progress)));
            if (px > x0) parts.push(<Ink key="prog" paths={roughRect(`${p.id}-prog-${win.unit}`, x0, by, px - x0, BAR_H, { stroke: "none", ...HATCH.progress, roughness: 1.2 })} />);
          }
          const pen = p.status === "done" ? PEN.inkLight : PEN.ink;
          parts.push(<Ink key="frame" paths={roughRect(`${p.id}-bar-${win.unit}`, x0, by, x1 - x0, BAR_H, { ...pen })} />);
          if (x(s) < 0) parts.push(<text key="l" x={2} y={by - 3} className="pm-chart-note">‹ 更早开始</text>);
          if (x(e) > w) parts.push(<text key="r" x={w - 2} y={by - 3} textAnchor="end" className="pm-chart-note">延续到 {shortDate(p.end)} ›</text>);
          if (p.progress !== null) {
            parts.push(<text key="pct" x={Math.min(x1 + 6, w - 30)} y={mid + 4} className="pm-chart-pct">{Math.round(p.progress * 100)}%</text>);
          }
        } else {
          parts.push(<text key="out" x={x(s) > w ? w - 4 : 4} y={mid + 4} textAnchor={x(s) > w ? "end" : "start"} className="pm-chart-note">{x(s) > w ? `${shortDate(p.start)} 开始 ›` : `‹ ${shortDate(p.end)} 已结束`}</text>);
        }
      } else {
        parts.push(<Ink key="tbd" paths={roughRect(`${p.id}-tbd-${win.unit}`, 4, by, w - 8, BAR_H, { ...PEN.inkLight })} dash="3 5" />);
        parts.push(<text key="tbdt" x={w / 2} y={mid + 4} textAnchor="middle" className="pm-chart-note">周期待补</text>);
      }
      // Events on nearby days would overlap: nudge to the right, keep a tick at the true date.
      const visible = p.events
        .map((ev) => ({ ev, d: dayNumber(ev.date) }))
        .filter(({ d }) => d >= win.start && d < win.end)
        .sort((a, b) => a.d - b.d);
      let lastX = -Infinity;
      for (const { ev, d } of visible) {
        const trueX = x(d + 0.5);
        const cx = Math.max(trueX, lastX + 20);
        lastX = cx;
        if (cx - trueX > 1) {
          parts.push(<path key={`${ev.id}-lead`} d={`M${trueX} ${by - 1} L${trueX} ${by - 5} L${cx} ${by - 5}`} className="pm-event-lead" />);
        }
        parts.push(<EventMark key={ev.id} ev={ev} cx={cx} cy={by - 13} onOpen={openEvent} />);
      }
      return <g>{parts}</g>;
    },
  }));

  const outside = rows.reduce((n, p) => n + p.events.filter((ev) => { const d = dayNumber(ev.date); return d < win.start || d >= win.end; }).length, 0);

  return (
    <div className="pm-gantt">
      <div className="pm-gantt-toolbar">
        <EventLegend />
        <UnitToggle unit={unit} onChange={(u) => { setPop(null); onUnitChange(u); }} />
      </div>
      <GanttFrame win={win} today={today} rows={frameRows} labelHead="项目" sideHead="现状" seed="portfolio"
        pop={pop} setPop={setPop} cb={cb} ariaLabel={`项目总览甘特图，今天 ${shortDate(today)}`} />
      <p className="pm-gantt-foot">
        斜线 = 已完成比例 · 虚线 = 今天（{shortDate(today)}）
        {outside > 0 ? ` · 另有 ${outside} 个事件在显示范围之外` : ""}
      </p>
    </div>
  );
}

/* =============================================================== milestones */

export function milestoneOrder(list: Milestone[]): Milestone[] {
  const key = (m: Milestone) => m.plan_start ?? m.plan_end ?? m.actual_start ?? m.actual_end ?? "9999-99-99";
  return [...list].sort((a, b) => key(a).localeCompare(key(b)) || a.title.localeCompare(b.title));
}

/** Delay in days (null when not delayed or not computable without inventing dates). */
export function delayDays(m: Milestone, today: ISODate): number | null {
  if (!m.plan_end) return null;
  const planEnd = dayNumber(m.plan_end);
  const ref = m.actual_end ? dayNumber(m.actual_end) : (m.status === "done" ? null : dayNumber(today));
  if (ref === null) return null;
  return ref > planEnd ? ref - planEnd : null;
}

export function dateRange(a: ISODate | null, b: ISODate | null): string {
  if (!a && !b) return "日期待补";
  if (a && b && a !== b) return `${shortDate(a)}–${shortDate(b)}`;
  return shortDate(b ?? a);
}

const MS_STATUS: Record<Milestone["status"], { symbol: string; label: string; tone: string }> = {
  done: { symbol: "✓", label: "已完成", tone: "ok" },
  in_progress: { symbol: "●", label: "进行中", tone: "ok" },
  not_started: { symbol: "○", label: "未开始", tone: "muted" },
  delayed: { symbol: "▲", label: "延期", tone: "clay" },
};

export function MilestoneStatus({ m }: { m: Milestone }) {
  const s = MS_STATUS[m.status] ?? MS_STATUS.not_started;
  return <span className={`pm-status pm-tone-${s.tone}`}><span aria-hidden="true">{s.symbol}</span> {s.label}</span>;
}

export function MilestoneGantt({ today, milestones, changes, unit, onUnitChange, cb, focusChange, onFocusChange }: {
  today: ISODate; milestones: Milestone[]; changes: ChangeEntry[]; unit: TimeUnit; onUnitChange: (u: TimeUnit) => void;
  cb?: PmCallbacks; focusChange?: string | null; onFocusChange?: (id: string | null) => void;
}) {
  const win = useMemo(() => makeWindow(today, unit), [today, unit]);
  const [pop, setPop] = useState<Pop | null>(null);
  const ordered = useMemo(() => milestoneOrder(milestones), [milestones]);
  const t = dayNumber(today);
  const numbered = useMemo(() => changeNumbers(changes), [changes]);

  const rows: FrameRow[] = ordered.map((m) => {
    const delay = delayDays(m, today);
    return {
      key: m.id,
      height: 50,
      label: <div className="pm-row-label"><span className="pm-row-name is-plain" title={m.title}>{m.title}</span><MilestoneStatus m={m} /></div>,
      side: (
        <div className="pm-row-dates">
          <span>计划 {dateRange(m.plan_start, m.plan_end)}</span>
          <span>实际 {m.actual_start || m.actual_end ? dateRange(m.actual_start, m.actual_end ?? null) + (m.actual_end ? "" : " 起") : "未记录"}</span>
          {delay ? <span className="pm-clay">延期 {delay} 天</span> : null}
        </div>
      ),
      draw: (y, w, x) => {
        const mid = y + 25 + 3;
        const by = mid - 9;
        const parts: ReactNode[] = [];
        const ps = m.plan_start ? dayNumber(m.plan_start) : null;
        const pe = m.plan_end ? dayNumber(m.plan_end) + 1 : null;
        if (ps === null && pe === null) {
          parts.push(<Ink key="tbd" paths={roughRect(`${m.id}-tbd`, 4, by, w - 8, 18, { ...PEN.inkLight })} dash="3 5" />);
          parts.push(<text key="tbdt" x={w / 2} y={mid + 4} textAnchor="middle" className="pm-hand pm-tbd">日期待补</text>);
          return <g>{parts}</g>;
        }
        const clampX = (d: number) => Math.min(w, Math.max(0, x(d)));
        if (ps !== null && pe !== null) {
          // actual (filled) under the plan frame, then delay in clay
          if (m.actual_start) {
            const as = dayNumber(m.actual_start);
            const ae = m.actual_end ? dayNumber(m.actual_end) + 1 : t + 1;
            const a0 = clampX(as);
            const a1 = clampX(Math.min(ae, pe));
            if (a1 > a0) parts.push(<Ink key="act" paths={roughRect(`${m.id}-act`, a0, by + 2, a1 - a0, 14, { stroke: "none", ...HATCH.progress })} />);
          }
          const x0 = clampX(ps);
          const x1 = clampX(pe);
          if (x1 > x0) parts.push(<Ink key="plan" paths={roughRect(`${m.id}-plan`, x0, by, x1 - x0, 18, { ...PEN.ink })} />);
          else if (!delayDays(m, today)) {
            const after = x(ps) >= w;
            parts.push(<text key="out" x={after ? w - 4 : 4} y={mid + 4} textAnchor={after ? "end" : "start"} className="pm-chart-note">
              {after ? `${shortDate(m.plan_start)} 开始 ›` : `‹ 计划 ${shortDate(m.plan_end)} 结束`}</text>);
          }
        } else {
          // single-date milestone: a diamond at the planned date
          const d = (pe ?? (ps as number) + 1) - 1;
          if (d >= win.start && d < win.end) {
            const cx = x(d + 0.5);
            parts.push(<Ink key="dia" paths={roughLinear(`${m.id}-dia`, [[cx, mid - 8], [cx + 7, mid], [cx, mid + 8], [cx - 7, mid], [cx, mid - 8]], { ...PEN.ink })} />);
          }
        }
        const delay = delayDays(m, today);
        if (delay && pe !== null) {
          const d0 = clampX(pe);
          const d1 = clampX(pe + delay);
          if (d1 > d0) {
            parts.push(<Ink key="dl" paths={roughRect(`${m.id}-delay`, d0, by, d1 - d0, 18, { ...PEN.clay, strokeWidth: 1.1, ...HATCH.delay })} />);
            parts.push(<text key="dlt" x={Math.min(d1 + 5, w - 4)} y={by - 3} textAnchor={d1 + 60 > w ? "end" : "start"} className="pm-hand pm-clay-text">延期 {delay} 天</text>);
          }
        }
        if (m.status === "done" && m.actual_end) {
          const d = dayNumber(m.actual_end);
          if (d >= win.start && d < win.end) {
            parts.push(<g key="ok" transform={`translate(${x(d + 0.5)},${by - 8})`}><EventGlyph type="milestone_done" size={12} /></g>);
          }
        }
        return <g>{parts}</g>;
      },
    };
  });

  // Lane aligned to the same axis: what changed, when (numbers match the list).
  rows.push({
    key: "__changes",
    height: 46,
    label: <div className="pm-row-label"><span className="pm-row-name is-plain">最近变化</span><span className="pm-meta">与下方列表编号对应</span></div>,
    side: <span className="pm-meta">{numbered.length ? `共 ${numbered.length} 条` : "暂无变化"}</span>,
    draw: (y, w, x) => {
      const cy = y + 25;
      const parts: ReactNode[] = [<Ink key="axis" paths={roughLine("changes-axis-" + win.unit, 4, cy, w - 4, cy, { ...PEN.inkLight })} />];
      let lastX = -Infinity;
      for (const c of numbered) {
        const d = dayOfTimestamp(c.change.at);
        if (d === null || d < win.start || d >= win.end) continue;
        // chronological order → nudge right when dots would overlap; tick marks the true day
        const trueX = x(d + 0.5);
        const cx = Math.max(trueX, lastX + 18);
        lastX = cx;
        parts.push(<line key={`${c.change.id}-tick`} x1={trueX} x2={trueX} y1={cy - 12} y2={cy - 8} className="pm-event-lead" />);
        const active = focusChange === c.change.id;
        parts.push(
          <g key={c.change.id} className={`pm-change-dot ${active ? "is-active" : ""}`} role="button" tabIndex={0}
            aria-label={`变化 ${c.n}：${c.change.title}`}
            onClick={() => onFocusChange?.(c.change.id)}
            onKeyDown={(e) => { if (e.key === "Enter") onFocusChange?.(c.change.id); }}>
            <Ink paths={roughCircle(`chg-${c.change.id}`, cx, cy, 15, active ? { ...PEN.clay, fill: "#F3E2D9", fillStyle: "solid" } : { ...PEN.sage, fill: "#FCFAF6", fillStyle: "solid" })} />
            <text x={cx} y={cy + 3.5} textAnchor="middle" className="pm-change-num">{c.n}</text>
          </g>,
        );
      }
      return <g>{parts}</g>;
    },
  });

  return (
    <div className="pm-gantt">
      <div className="pm-gantt-toolbar">
        <ul className="pm-legend" aria-label="图例">
          <li><span className="pm-swatch is-plan" aria-hidden="true" /> 计划</li>
          <li><span className="pm-swatch is-actual" aria-hidden="true" /> 实际</li>
          <li><span className="pm-swatch is-delay" aria-hidden="true" /> 延期</li>
          <li><span className="pm-swatch is-tbd" aria-hidden="true" /> 日期待补</li>
        </ul>
        <UnitToggle unit={unit} onChange={(u) => { setPop(null); onUnitChange(u); }} />
      </div>
      <GanttFrame win={win} today={today} rows={rows} labelHead="里程碑" sideHead="日期" seed="milestones"
        pop={pop} setPop={setPop} cb={cb} ariaLabel={`进度与里程碑甘特图，今天 ${shortDate(today)}`} />
    </div>
  );
}

/** Changes numbered oldest → newest (1 = oldest), shared by chart and list. */
export function changeNumbers(changes: ChangeEntry[]) {
  return [...changes].sort((a, b) => a.at.localeCompare(b.at)).map((change, i) => ({ n: i + 1, change }));
}

export { shortDateTime };
