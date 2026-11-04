import { SHOPIFY } from "./data";
import type { DateRange, PeriodKey } from "./types";

export const TODAY = SHOPIFY[SHOPIFY.length - 1].data; // "2026-11-04"

export function parseISO(s: string): Date {
  return new Date(s + "T00:00:00Z");
}
export function fmtISO(d: Date): string {
  return d.toISOString().slice(0, 10);
}
export function addDays(d: Date, n: number): Date {
  const x = new Date(d);
  x.setUTCDate(x.getUTCDate() + n);
  return x;
}
export function mondayOf(d: Date): Date {
  const x = new Date(d);
  const day = (x.getUTCDay() + 6) % 7;
  return addDays(x, -day);
}
export function firstOfMonth(d: Date): Date {
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1));
}
export function lastOfMonth(d: Date): Date {
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0));
}
export function inRange(dateStr: string, from: Date, to: Date): boolean {
  return dateStr >= fmtISO(from) && dateStr <= fmtISO(to);
}
export function spanDays(from: Date, to: Date): number {
  return Math.round((to.getTime() - from.getTime()) / 86400000) + 1;
}

const todayD = parseISO(TODAY);

export const PERIODS: Record<PeriodKey, DateRange> = {
  last_week_closed: (() => {
    const to = addDays(mondayOf(todayD), -1);
    const from = addDays(to, -6);
    return { from, to };
  })(),
  this_week: { from: mondayOf(todayD), to: todayD },
  last7: { from: addDays(todayD, -6), to: todayD },
  last14: { from: addDays(todayD, -13), to: todayD },
  last30: { from: addDays(todayD, -29), to: todayD },
  month_current: { from: firstOfMonth(todayD), to: todayD },
  month_prev: (() => {
    const prevAnchor = firstOfMonth(addDays(firstOfMonth(todayD), -1));
    return { from: prevAnchor, to: lastOfMonth(prevAnchor) };
  })(),
};

export const PERIOD_LABELS: [PeriodKey, string][] = [
  ["last_week_closed", "Última semana fechada"],
  ["this_week", "Semana atual"],
  ["last7", "Últimos 7 dias"],
  ["last14", "Últimos 14 dias"],
  ["last30", "Últimos 30 dias"],
  ["month_current", "Mês atual"],
  ["month_prev", "Mês anterior"],
];

export function prevPeriodOf(p: DateRange): DateRange {
  const n = spanDays(p.from, p.to);
  const to = addDays(p.from, -1);
  const from = addDays(to, -(n - 1));
  return { from, to };
}
