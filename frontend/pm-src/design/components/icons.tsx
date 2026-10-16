/* Event icons drawn as line art (no emoji — emoji turn colourful on phones).
   Each icon also has a text label; the shape is never the only signal.     */
import type { ProjectEventType } from "../types";

export const EVENT_META: Record<ProjectEventType, { label: string; color: string; glyph: string }> = {
  new_source: { label: "新资料", color: "#6F6A61", glyph: "◆" },
  conflict: { label: "冲突", color: "#A47A42", glyph: "⚠" },
  decision: { label: "需要决定", color: "#B66F51", glyph: "✋" },
  milestone_done: { label: "里程碑完成", color: "#648166", glyph: "✓" },
};

const S = { fill: "none", strokeWidth: 1.5, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

/** Icon centred on (0,0), about 14px across. */
export function EventGlyph({ type, size = 14 }: { type: ProjectEventType; size?: number }) {
  const c = EVENT_META[type]?.color ?? "#6F6A61";
  const k = size / 14;
  return (
    <g transform={`scale(${k})`} aria-hidden="true">
      {type === "new_source" && <path d="M0 -6.2 L5.6 0 L0 6.2 L-5.6 0 Z" stroke={c} {...S} fill="#EAE4DA" />}
      {type === "conflict" && (
        <>
          <path d="M0 -6.4 L6.6 5.6 L-6.6 5.6 Z" stroke={c} {...S} fill="#F6EEDF" />
          <path d="M0 -2 L0 1.6" stroke={c} {...S} />
          <path d="M0 3.7 L0 3.8" stroke={c} {...S} strokeWidth={2} />
        </>
      )}
      {type === "decision" && (
        <path
          d="M-3.6 6.2 C-5.4 4.4 -6.2 2.4 -5.6 0.4 L-4.6 -2.2 M-3.2 1 L-3.2 -4.6 M-1 0.6 L-1 -6.2 M1.2 0.6 L1.2 -5.4 M3.4 1.2 L3.4 -3.6 M3.4 1.2 C3.6 3.8 2.8 5.4 1 6.2 L-3.6 6.2"
          stroke={c} {...S} />
      )}
      {type === "milestone_done" && <path d="M-5.2 0.4 L-1.6 4.2 L5.4 -4.6" stroke={c} {...S} strokeWidth={1.8} />}
    </g>
  );
}

export function EventIcon({ type, size = 14 }: { type: ProjectEventType; size?: number }) {
  return (
    <svg className="pm-icon" width={size + 4} height={size + 4} viewBox={`${-(size + 4) / 2} ${-(size + 4) / 2} ${size + 4} ${size + 4}`} aria-hidden="true">
      <EventGlyph type={type} size={size} />
    </svg>
  );
}

export function EventLegend() {
  return (
    <ul className="pm-legend" aria-label="图例">
      {(Object.keys(EVENT_META) as ProjectEventType[]).map((t) => (
        <li key={t}><EventIcon type={t} size={12} /> {EVENT_META[t].label}</li>
      ))}
    </ul>
  );
}
