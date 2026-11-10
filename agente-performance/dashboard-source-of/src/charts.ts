import type { BarLinePoint, WeeklyPoint } from "./types";

type NumericKey = "novos" | "cac" | "ticket" | "conv";

export function lineChart(
  series: WeeklyPoint[],
  keyA: NumericKey,
  keyB: NumericKey,
  colorA: string,
  colorB: string,
  fmtA: (v: number) => string,
  fmtB: (v: number) => string
): string {
  const W = 560,
    H = 190,
    padL = 42,
    padR = 42,
    padT = 14,
    padB = 24;
  const xs = series.map((_, i) => padL + (i * (W - padL - padR)) / (series.length - 1));
  const aVals = series.map((d) => d[keyA]).filter((v): v is number => v != null);
  const bVals = series.map((d) => d[keyB]).filter((v): v is number => v != null);
  const aMax = Math.max(...aVals, 1),
    aMin = 0;
  const bMax = Math.max(...bVals, 1),
    bMin = 0;
  const yA = (v: number) => H - padB - ((v - aMin) / (aMax - aMin || 1)) * (H - padT - padB);
  const yB = (v: number) => H - padB - ((v - bMin) / (bMax - bMin || 1)) * (H - padT - padB);

  function pathOf(key: NumericKey, yfn: (v: number) => number): string {
    let d = "";
    let started = false;
    series.forEach((pt, i) => {
      const v = pt[key];
      if (v == null) return;
      d += `${started ? "L" : "M"}${xs[i].toFixed(1)} ${yfn(v).toFixed(1)} `;
      started = true;
    });
    return d.trim();
  }

  const pa = pathOf(keyA, yA);
  const pb = pathOf(keyB, yB);
  const gridY = [0, 0.5, 1].map((t) => padT + t * (H - padT - padB));

  let dots = "";
  series.forEach((pt, i) => {
    const v = pt[keyA];
    if (v != null) dots += `<circle cx="${xs[i].toFixed(1)}" cy="${yA(v).toFixed(1)}" r="2.6" fill="${colorA}"/>`;
  });

  const step = Math.max(1, Math.ceil(series.length / 6));
  const labels = series
    .map((pt, i) =>
      i % step === 0 || i === series.length - 1
        ? `<text x="${xs[i].toFixed(1)}" y="${H - 6}" font-size="9.5" fill="var(--fg-muted)" text-anchor="middle" font-family="var(--font-mono)">${pt.label}</text>`
        : ""
    )
    .join("");

  const lastA = [...series].reverse().find((d) => d[keyA] != null);
  const lastB = [...series].reverse().find((d) => d[keyB] != null);

  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    ${gridY.map((y) => `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="var(--border)" stroke-width="1"/>`).join("")}
    <path d="${pb}" fill="none" stroke="${colorB}" stroke-width="2" stroke-dasharray="4 3" opacity="0.9"/>
    <path d="${pa}" fill="none" stroke="${colorA}" stroke-width="2.4"/>
    ${dots}
    ${labels}
    ${lastA ? `<text x="${W - padR + 4}" y="${yA(lastA[keyA]!).toFixed(1)}" font-size="10" fill="${colorA}" font-family="var(--font-mono)" font-weight="600">${fmtA(lastA[keyA]!)}</text>` : ""}
    ${lastB ? `<text x="${W - padR + 4}" y="${(yB(lastB[keyB]!) + 11).toFixed(1)}" font-size="10" fill="${colorB}" font-family="var(--font-mono)" font-weight="600">${fmtB(lastB[keyB]!)}</text>` : ""}
  </svg>`;
}

/** Barras (eixo esquerdo) + linha (eixo direito) por dia, sem bibliotecas. */
export function barLineChart(
  series: BarLinePoint[],
  colorBar: string,
  colorLine: string,
  fmtBar: (v: number) => string,
  fmtLine: (v: number) => string
): string {
  if (!series.length) return '<div class="sub">Sem dados no período.</div>';
  const W = 560,
    H = 200,
    padL = 48,
    padR = 48,
    padT = 14,
    padB = 24;
  const innerW = W - padL - padR;
  const slot = innerW / series.length;
  const barW = Math.max(2, Math.min(18, slot * 0.7));
  const xc = (i: number) => padL + slot * (i + 0.5);
  const barMax = Math.max(...series.map((d) => d.bar), 1);
  const lineVals = series.map((d) => d.line).filter((v): v is number => v != null);
  const lineMax = Math.max(...lineVals, 1);
  const yBar = (v: number) => H - padB - (v / barMax) * (H - padT - padB);
  const yLine = (v: number) => H - padB - (v / lineMax) * (H - padT - padB);

  const bars = series
    .map((d, i) => `<rect x="${(xc(i) - barW / 2).toFixed(1)}" y="${yBar(d.bar).toFixed(1)}" width="${barW.toFixed(1)}" height="${(H - padB - yBar(d.bar)).toFixed(1)}" fill="${colorBar}" opacity="0.35"><title>${d.label}: ${fmtBar(d.bar)}${d.line != null ? " · " + fmtLine(d.line) : ""}</title></rect>`)
    .join("");

  let path = "";
  let started = false;
  series.forEach((d, i) => {
    if (d.line == null) return;
    path += `${started ? "L" : "M"}${xc(i).toFixed(1)} ${yLine(d.line).toFixed(1)} `;
    started = true;
  });
  const dots = series
    .map((d, i) => (d.line != null ? `<circle cx="${xc(i).toFixed(1)}" cy="${yLine(d.line).toFixed(1)}" r="2.2" fill="${colorLine}"/>` : ""))
    .join("");

  const step = Math.max(1, Math.ceil(series.length / 8));
  const labels = series
    .map((d, i) =>
      i % step === 0 || i === series.length - 1
        ? `<text x="${xc(i).toFixed(1)}" y="${H - 6}" font-size="9.5" fill="var(--fg-muted)" text-anchor="middle" font-family="var(--font-mono)">${d.label}</text>`
        : ""
    )
    .join("");
  const grid = [0, 0.5, 1]
    .map((t) => {
      const y = padT + t * (H - padT - padB);
      const barTick = barMax * (1 - t);
      const lineTick = lineMax * (1 - t);
      return `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="var(--border)" stroke-width="1"/>
        <text x="${padL - 4}" y="${(y + 3).toFixed(1)}" font-size="9.5" fill="${colorBar}" text-anchor="end" font-family="var(--font-mono)">${fmtBar(barTick)}</text>
        <text x="${W - padR + 4}" y="${(y + 3).toFixed(1)}" font-size="9.5" fill="${colorLine}" font-family="var(--font-mono)">${fmtLine(lineTick)}</text>`;
    })
    .join("");

  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    ${grid}
    ${bars}
    <path d="${path.trim()}" fill="none" stroke="${colorLine}" stroke-width="2.2"/>
    ${dots}
    ${labels}
  </svg>`;
}
