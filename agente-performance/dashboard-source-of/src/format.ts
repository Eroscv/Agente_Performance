const BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const BRL2 = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const INT = new Intl.NumberFormat("pt-BR");

export function fmtBRL(v: number | null | undefined): string {
  return v == null ? "—" : BRL.format(v);
}
export function fmtBRL2(v: number | null | undefined): string {
  return v == null ? "—" : BRL2.format(v);
}
export function fmtInt(v: number | null | undefined): string {
  return v == null ? "—" : INT.format(Math.round(v));
}
export function fmtPct(v: number | null | undefined, digits = 1): string {
  return v == null ? "—" : (v * 100).toFixed(digits).replace(".", ",") + "%";
}
export function fmtRoas(v: number | null | undefined): string {
  return v == null ? "—" : v.toFixed(2) + "x";
}
export function fmtDateBR(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
}
export function fmtDateTime(ts: number): string {
  const d = new Date(ts * 1000);
  return `${fmtDateBR(d.toISOString().slice(0, 10))} ${String(d.getUTCHours()).padStart(2, "0")}:${String(
    d.getUTCMinutes()
  ).padStart(2, "0")}`;
}

export function deltaHTML(cur: number | null, prev: number | null, invert = false): string {
  if (prev == null || cur == null || prev === 0) {
    return '<span class="cmp">— vs. período anterior</span>';
  }
  const pctv = (cur - prev) / Math.abs(prev);
  const good = invert ? pctv < 0 : pctv > 0;
  const cls = Math.abs(pctv) < 0.001 ? "" : good ? "up" : "down";
  const sign = pctv > 0 ? "+" : "";
  return `<span class="cmp ${cls}">${sign}${(pctv * 100).toFixed(1).replace(".", ",")}% vs. período anterior</span>`;
}

export function deltaCellHTML(cur: number | null, prev: number | null, invert = false): string {
  if (prev == null || cur == null) return '<span class="delta flat">—</span>';
  if (prev === 0) {
    return cur === 0 ? '<span class="delta flat">0,0%</span>' : '<span class="delta up">novo</span>';
  }
  const pctv = (cur - prev) / Math.abs(prev);
  const good = invert ? pctv < 0 : pctv > 0;
  const cls = Math.abs(pctv) < 0.001 ? "flat" : good ? "up" : "down";
  const sign = pctv > 0 ? "+" : "";
  return `<span class="delta ${cls}">${sign}${(pctv * 100).toFixed(1).replace(".", ",")}%</span>`;
}
