import type { AiSuggestion, PmCallbacks, RoutingReceipt } from "../types";
import { shortDateTime } from "../time";
import { EmptyState, SourceDetail, DetailPopover } from "./common";
import { EventIcon } from "./icons";

/** "AI 建议" — visibly not a fact: tinted ground, label, and its basis. */
export function AiSuggestionList({ items, cb }: { items: AiSuggestion[]; cb?: PmCallbacks }) {
  if (!items.length) return <EmptyState seed="ai-empty" text="暂时没有新的建议。" />;
  return (
    <ul className="pm-ai-list">
      {items.map((s) => (
        <li key={s.id} className="pm-ai">
          <span className="pm-ai-label">AI 建议{s.project_name ? ` · ${s.project_name}` : ""}</span>
          <p>{s.text}</p>
          <div className="pm-ai-basis">
            <span className="pm-meta">依据</span>
            {s.basis.map((b) => (
              <DetailPopover key={b.id} label={`依据：${b.title}`} trigger={<span className="pm-basis-chip">{b.title}</span>}>
                <SourceDetail source={b} cb={cb} />
              </DetailPopover>
            ))}
          </div>
        </li>
      ))}
    </ul>
  );
}

const RECEIPT_STATE: Record<RoutingReceipt["state"], { symbol: string; label: string }> = {
  routed: { symbol: "✓", label: "已归入" },
  pending: { symbol: "…", label: "待归类" },
  needs_decision: { symbol: "✋", label: "需要你决定归属" },
};

/** Where did my note / file go? */
export function ReceiptCard({ receipt, cb }: { receipt: RoutingReceipt; cb?: PmCallbacks }) {
  const st = RECEIPT_STATE[receipt.state] ?? RECEIPT_STATE.pending;
  return (
    <div className={`pm-receipt pm-receipt-${receipt.state}`}>
      <span className="pm-receipt-mark" aria-hidden="true">
        {receipt.state === "needs_decision" ? <EventIcon type="decision" size={13} /> : st.symbol}
      </span>
      <div className="pm-receipt-body">
        <span className="pm-eyebrow">{receipt.kind === "capture" ? "随手记" : "新文件"} · {shortDateTime(receipt.received_at)}</span>
        <strong>{receipt.title}</strong>
        <span>
          {st.label}
          {receipt.project_name ? (
            receipt.href && cb?.onNavigate
              ? <> <button type="button" className="pm-linkish" onClick={() => cb.onNavigate?.(receipt.href as string)}>{receipt.project_name}</button></>
              : <> {receipt.project_name}</>
          ) : null}
          {receipt.updated_sections.length > 0 ? <>，更新了 {receipt.updated_sections.map((s) => `「${s}」`).join("")}</> : null}
        </span>
        {receipt.note ? <span className="pm-meta">{receipt.note}</span> : null}
      </div>
    </div>
  );
}

export function ReceiptList({ items, cb }: { items: RoutingReceipt[]; cb?: PmCallbacks }) {
  if (!items.length) return null;
  return <div className="pm-receipts">{items.map((r) => <ReceiptCard key={r.id} receipt={r} cb={cb} />)}</div>;
}
