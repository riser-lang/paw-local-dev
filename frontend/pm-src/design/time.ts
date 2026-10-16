/* Calendar arithmetic in whole days (UTC-anchored, no DST drift).
   All view-model dates are Beijing calendar days "YYYY-MM-DD".                */
import type { ISODate, TimeUnit } from "./types";

const DAY = 86400000;

export function dayNumber(date: ISODate): number {
  const [y, m, d] = date.split("-").map(Number);
  return Math.floor(Date.UTC(y, m - 1, d) / DAY);
}

export function fromDayNumber(n: number): ISODate {
  const t = new Date(n * DAY);
  return `${t.getUTCFullYear()}-${String(t.getUTCMonth() + 1).padStart(2, "0")}-${String(t.getUTCDate()).padStart(2, "0")}`;
}

export function isISODate(value: unknown): value is ISODate {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
}

/** Monday on or before the given day. */
export function weekStart(n: number): number {
  const dow = (new Date(n * DAY).getUTCDay() + 6) % 7; // 0 = Monday
  return n - dow;
}

export interface TimeWindow {
  unit: TimeUnit;
  start: number;          // first visible day (inclusive)
  end: number;            // first day after the window (exclusive)
  ticks: { day: number; label: string; major: boolean }[];
}

/** Default window: past 4 weeks + current week + next 8 weeks; month mode: 2 back, 4 ahead. */
export function makeWindow(today: ISODate, unit: TimeUnit): TimeWindow {
  const t = dayNumber(today);
  if (unit === "week") {
    const w0 = weekStart(t);
    const start = w0 - 4 * 7;
    const end = w0 + 9 * 7;
    const ticks = [];
    for (let d = start; d < end; d += 7) {
      const iso = fromDayNumber(d);
      const [, m, day] = iso.split("-").map(Number);
      ticks.push({ day: d, label: `${m}/${day}`, major: day <= 7 });
    }
    return { unit, start, end, ticks };
  }
  const td = new Date(t * DAY);
  const y = td.getUTCFullYear();
  const m = td.getUTCMonth();
  const first = (yy: number, mm: number) => Math.floor(Date.UTC(yy, mm, 1) / DAY);
  const start = first(y, m - 2);
  const end = first(y, m + 4);
  const ticks = [];
  for (let k = -2; k < 4; k += 1) {
    const d = first(y, m + k);
    const date = new Date(d * DAY);
    ticks.push({ day: d, label: `${date.getUTCMonth() + 1}月`, major: date.getUTCMonth() === 0 });
  }
  return { unit, start, end, ticks };
}

export function makeScale(win: TimeWindow, width: number) {
  const span = win.end - win.start;
  return (day: number) => ((day - win.start) / span) * width;
}

/** "10月15日" */
export function shortDate(date: ISODate | null): string {
  if (!date) return "日期待补";
  const [, m, d] = date.split("-").map(Number);
  return `${m}月${d}日`;
}

/** "10/15 08:30" in Beijing time from an ISO timestamp. */
export function shortDateTime(ts: string | null): string {
  if (!ts) return "—";
  const t = new Date(ts);
  if (Number.isNaN(t.getTime())) return "—";
  const bj = new Date(t.getTime() + 8 * 3600000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${bj.getUTCMonth() + 1}/${bj.getUTCDate()} ${pad(bj.getUTCHours())}:${pad(bj.getUTCMinutes())}`;
}

/** Beijing calendar day of an ISO timestamp. */
export function dayOfTimestamp(ts: string): number | null {
  const t = new Date(ts);
  if (Number.isNaN(t.getTime())) return null;
  return Math.floor((t.getTime() + 8 * 3600000) / DAY);
}
