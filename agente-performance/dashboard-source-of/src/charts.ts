import type { WeeklyPoint } from "./types";

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
