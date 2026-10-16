import type { SystemStatusItem } from "../types";
import { shortDateTime } from "../time";
import { DetailPopover, HEALTH } from "./common";

/** Top-right system strip: dot + word + last success time. Nothing louder. */
export function SystemStatus({ items, compact = false }: { items: SystemStatusItem[]; compact?: boolean }) {
  if (!items.length) return null;
  return (
    <ul className={`pm-sys ${compact ? "is-compact" : ""}`} aria-label="系统状态">
      {items.map((item) => {
        const h = HEALTH[item.state] ?? HEALTH.unknown;
        return (
          <li key={item.key} className={`pm-sys-item pm-health-${item.state}`}>
            <DetailPopover
              label={`${item.label}：${h.label}`}
              trigger={
                <>
                  <span className="pm-sys-dot" aria-hidden="true">{h.symbol}</span>
                  <span className="pm-sys-label">{item.label}</span>
                  <span className="pm-sys-state">{h.label}</span>
                </>
              }
            >
              <strong>{item.label} · {h.label}</strong>
              <span>{item.summary}</span>
              {item.detail ? <span className="pm-meta">{item.detail}</span> : null}
              <span className="pm-meta">最后成功：{item.last_success_at ? shortDateTime(item.last_success_at) : "暂无记录"}</span>
            </DetailPopover>
            {!compact && (
              <span className="pm-sys-time" aria-hidden="true">
                {item.last_success_at ? shortDateTime(item.last_success_at) : "—"}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
