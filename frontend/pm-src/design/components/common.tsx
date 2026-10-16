import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import type { HealthState, PmCallbacks, ProjectStatus, SourceRef } from "../types";
import { shortDateTime } from "../time";
import { Ink, roughCircle, roughLine, PEN } from "../rough";

/* ------------------------------------------------------------ hooks */

/** true when the viewport is ≤ 640px (phone layout). */
export function usePhone(): boolean {
  const query = "(max-width: 640px)";
  const [phone, setPhone] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const mql = window.matchMedia(query);
    const on = () => setPhone(mql.matches);
    on();
    mql.addEventListener("change", on);
    return () => mql.removeEventListener("change", on);
  }, []);
  return phone;
}

/** Measured content width of an element (for pixel-exact rough drawings). */
export function useWidth<T extends HTMLElement>(): [RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const update = () => setWidth(Math.max(0, Math.round(el.getBoundingClientRect().width)));
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

/* ------------------------------------------------------------ status marks
   Colour is never the only carrier: every state has a symbol AND a word.   */

export const PROJECT_STATUS: Record<ProjectStatus, { symbol: string; label: string; tone: string }> = {
  active: { symbol: "●", label: "进行中", tone: "ok" },
  at_risk: { symbol: "▲", label: "有风险", tone: "warn" },
  blocked: { symbol: "■", label: "受阻", tone: "danger" },
  done: { symbol: "✓", label: "已完成", tone: "muted" },
  paused: { symbol: "‖", label: "暂停", tone: "muted" },
  empty: { symbol: "○", label: "待建立", tone: "muted" },
};

export function ProjectStatusMark({ status }: { status: ProjectStatus }) {
  const s = PROJECT_STATUS[status] ?? PROJECT_STATUS.active;
  return (
    <span className={`pm-status pm-tone-${s.tone}`}>
      <span aria-hidden="true">{s.symbol}</span> {s.label}
    </span>
  );
}

export const HEALTH: Record<HealthState, { symbol: string; label: string }> = {
  ok: { symbol: "●", label: "正常" },
  warn: { symbol: "▲", label: "注意" },
  error: { symbol: "✕", label: "异常" },
  unknown: { symbol: "○", label: "未知" },
};

/* ------------------------------------------------------------ detail popover
   Desktop: hover or focus opens it. Phone / touch: tap toggles it.          */

export function DetailPopover({ trigger, children, label, className }: {
  trigger: ReactNode; children: ReactNode; label: string; className?: string;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const box = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    const away = (e: PointerEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  const hover = typeof window !== "undefined" && window.matchMedia("(hover: hover)").matches;
  return (
    <span
      ref={box}
      className={`pm-pop ${className ?? ""}`}
      onMouseEnter={hover ? () => setOpen(true) : undefined}
      onMouseLeave={hover ? () => setOpen(false) : undefined}
    >
      <button type="button" className="pm-pop-trigger" aria-expanded={open} aria-controls={id}
        aria-label={label} onClick={() => setOpen((v) => !v)}>
        {trigger}
      </button>
      {open && (
        <span id={id} role="dialog" aria-label={label} className="pm-pop-card">
          {children}
        </span>
      )}
    </span>
  );
}

/* ------------------------------------------------------------ sources */

const KIND_LABEL: Record<SourceRef["kind"], string> = {
  wps_doc: "文档", capture: "随手记", knowledge: "知识", metric: "指标", chat: "对话", todo: "待办", other: "来源",
};

export function SourceLink({ source, cb }: { source: SourceRef; cb?: PmCallbacks }) {
  const kind = KIND_LABEL[source.kind] ?? "来源";
  // titles like "随手记 · xxx" already carry their kind — don't say it twice
  const label = source.title.startsWith(kind) ? source.title : `${kind} · ${source.title}`;
  if (source.href && cb?.onNavigate) {
    return (
      <button type="button" className="pm-source" onClick={() => cb.onNavigate?.(source.href as string)}>
        {label}
      </button>
    );
  }
  return <span className="pm-source is-static">{label}</span>;
}

export function SourceDetail({ source, cb }: { source: SourceRef; cb?: PmCallbacks }) {
  return (
    <span className="pm-source-detail">
      <SourceLink source={source} cb={cb} />
      {source.excerpt ? <span className="pm-excerpt">“{source.excerpt}”</span> : null}
      <span className="pm-meta">更新于 {shortDateTime(source.updated_at)}</span>
    </span>
  );
}

/* ------------------------------------------------------------ empty state */

export function EmptyState({ text, seed }: { text: string; seed: string }) {
  const paths = [
    ...roughCircle(`${seed}-c`, 22, 22, 30, { ...PEN.inkLight }),
    ...roughLine(`${seed}-l`, 13, 23, 31, 23, { ...PEN.inkLight }),
  ];
  return (
    <div className="pm-empty">
      <svg width="44" height="44" viewBox="0 0 44 44" aria-hidden="true"><Ink paths={paths} /></svg>
      <p>{text}</p>
    </div>
  );
}

/** Section heading in the editorial serif, with an optional right-side slot. */
export function SectionHeading({ title, extra, id }: { title: string; extra?: ReactNode; id?: string }) {
  return (
    <div className="pm-section-heading">
      <h2 id={id}>{title}</h2>
      {extra ? <div className="pm-section-extra">{extra}</div> : null}
    </div>
  );
}
