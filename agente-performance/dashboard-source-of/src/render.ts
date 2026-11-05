import { aggCampanhas, aggCriativos, investTotalAds, sumAbandoned, sumAdsAllCanais, sumShopify, weeklySeries } from "./aggregate";
import { lineChart } from "./charts";
import { LAST_SYNC } from "./data";
import { fmtISO, PERIOD_LABELS, PERIODS, prevPeriodOf, TODAY } from "./dates";
import { deltaCellHTML, deltaHTML, fmtBRL, fmtBRL2, fmtDateBR, fmtDateTime, fmtInt, fmtPct, fmtRoas } from "./format";
import type { Canal, CanalTotalsMap, DateRange, PeriodKey, ShopifyTotals, TabKey } from "./types";

export const TABS: [TabKey, string][] = [
  ["overview", "Visão geral"],
  ["loja", "Loja"],
  ["meta", "Meta Ads"],
  ["google", "Google Ads"],
  ["canais", "Canais e Produtos"],
  ["recuperacao", "Recuperação e Orgânico"],
  ["criativos", "Criativos"],
  ["sync", "Atualização dos dados"],
];

interface State {
  period: PeriodKey | null;
  custom: DateRange;
  tab: TabKey;
}

export const state: State = {
  period: "last_week_closed",
  custom: PERIODS.last_week_closed,
  tab: "loja",
};

function el<T extends HTMLElement = HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Missing #${id}`);
  return node as T;
}

function kpiCard(label: string, val: string, cmpHtml?: string): string {
  return `<div class="kpi"><div class="lbl">${label}</div><div class="val nums">${val}</div><div class="cmp-slot">${
    cmpHtml || '<span class="cmp">— vs. período anterior</span>'
  }</div></div>`;
}

function tableRow(
  label: string,
  cur: number | null,
  prev: number | null,
  fmt: (v: number | null) => string,
  invert = false
): string {
  return `<tr><td>${label}</td><td class="num">${fmt(prev)}</td><td class="num">${fmt(cur)}</td><td class="num">${deltaCellHTML(
    cur,
    prev,
    invert
  )}</td></tr>`;
}

interface Period {
  from: Date;
  to: Date;
  sh: ShopifyTotals;
}

function renderOverview(cur: Period, prev: Period, ads: CanalTotalsMap): string {
  const investTotal = investTotalAds(cur.from, cur.to);
  const investPrev = investTotalAds(prev.from, prev.to);
  const mer = investTotal ? cur.sh.receita / investTotal : null;
  const merPrev = investPrev ? prev.sh.receita / investPrev : null;

  const canalRows = (["meta", "google"] as Canal[])
    .map((c) => {
      const a = ads[c] || { investimento: 0, pedidos: 0, receita: 0, roas: null };
      return `<tr><td>${c === "meta" ? "Meta Ads" : "Google Ads"}</td><td class="num">${fmtBRL(
        a.investimento
      )}</td><td class="num">${fmtInt(a.pedidos)}</td><td class="num">${fmtBRL(a.receita)}</td><td class="num">${fmtRoas(
        a.roas
      )}</td></tr>`;
    })
    .join("");

  return `
  <h1>Visão geral</h1>
  <p class="lede">Panorama consolidado de Shopify + mídia paga (Meta e Google) para a Source Of no período selecionado.</p>
  <div class="kpis">
    ${kpiCard("Receita Shopify", fmtBRL(cur.sh.receita), deltaHTML(cur.sh.receita, prev.sh.receita))}
    ${kpiCard("Investimento em mídia", fmtBRL(investTotal), deltaHTML(investTotal, investPrev, true))}
    ${kpiCard("MER (receita/invest.)", mer != null ? mer.toFixed(2) + "x" : "—", deltaHTML(mer, merPrev))}
    ${kpiCard("Novos clientes", fmtInt(cur.sh.novos_clientes), deltaHTML(cur.sh.novos_clientes, prev.sh.novos_clientes))}
  </div>
  <div class="card">
    <h3>Canais pagos no período</h3>
    <div class="sub">Meta Ads e Google Ads, investimento e receita atribuída.</div>
    <div class="tablewrap"><table><thead><tr><th>Canal</th><th class="num">Investimento</th><th class="num">Pedidos</th><th class="num">Receita</th><th class="num">ROAS</th></tr></thead><tbody>
      ${canalRows}
    </tbody></table></div>
  </div>`;
}

function renderLoja(cur: Period, prev: Period): string {
  const chartData = weeklySeries(fmtISO(cur.to), 9);
  const investCur = investTotalAds(cur.from, cur.to);
  const investPrev = investTotalAds(prev.from, prev.to);

  return `
  <h1>Loja</h1>
  <p class="lede">Desempenho do Shopify no período: receita, conversão, ticket e aquisição de clientes.</p>
  <div class="kpis">
    ${kpiCard("Receita Shopify", fmtBRL(cur.sh.receita), deltaHTML(cur.sh.receita, prev.sh.receita))}
    ${kpiCard("% Conversão", fmtPct(cur.sh.taxa_conversao), deltaHTML(cur.sh.taxa_conversao, prev.sh.taxa_conversao))}
    ${kpiCard("Ticket médio", fmtBRL2(cur.sh.ticket_medio), deltaHTML(cur.sh.ticket_medio, prev.sh.ticket_medio))}
    ${kpiCard("Novos clientes", fmtInt(cur.sh.novos_clientes), deltaHTML(cur.sh.novos_clientes, prev.sh.novos_clientes))}
  </div>
  <div class="card">
    <h3>Loja · período</h3>
    <div class="sub">Período atual comparado ao anterior de mesma duração.</div>
    <div class="tablewrap"><table>
      <thead><tr><th>Métrica</th><th class="num">Anterior</th><th class="num">Atual</th><th class="num">Δ%</th></tr></thead>
      <tbody>
        ${tableRow("Investimento total (mídia)", investCur, investPrev, fmtBRL, true)}
        ${tableRow("% Conversão", cur.sh.taxa_conversao, prev.sh.taxa_conversao, fmtPct)}
        ${tableRow("Ticket médio", cur.sh.ticket_medio, prev.sh.ticket_medio, fmtBRL2)}
        ${tableRow("Pedidos", cur.sh.pedidos, prev.sh.pedidos, fmtInt)}
        ${tableRow("Novos clientes", cur.sh.novos_clientes, prev.sh.novos_clientes, fmtInt)}
        ${tableRow(
          "CAC (invest./novo cliente)",
          cur.sh.novos_clientes ? investCur / cur.sh.novos_clientes : null,
          prev.sh.novos_clientes ? investPrev / prev.sh.novos_clientes : null,
          fmtBRL2,
          true
        )}
        ${tableRow("% Desconto", cur.sh.pct_desconto, prev.sh.pct_desconto, fmtPct, true)}
        ${tableRow("Pedidos acima de R$ 300", cur.sh.pedidos_gt300, prev.sh.pedidos_gt300, fmtInt)}
        ${tableRow("Frete cobrado", cur.sh.frete, prev.sh.frete, fmtBRL2)}
        ${tableRow("Receita Shopify", cur.sh.receita, prev.sh.receita, fmtBRL)}
        ${tableRow(
          "MER",
          investCur ? cur.sh.receita / investCur : null,
          investPrev ? prev.sh.receita / investPrev : null,
          (v) => (v != null ? v.toFixed(2) + "x" : "—")
        )}
      </tbody>
    </table></div>
  </div>
  <div class="grid2">
    <div class="card chartcard">
      <h3>Clientes e CAC</h3>
      <div class="sub">Por semana: quantos clientes novos e a que custo.</div>
      ${lineChart(chartData, "novos", "cac", "var(--accent)", "var(--ok)", (v) => fmtInt(v), (v) => fmtBRL2(v))}
      <div class="legend"><span><i style="background:var(--accent)"></i>Novos clientes</span><span><i style="background:var(--ok)"></i>CAC</span></div>
    </div>
    <div class="card chartcard">
      <h3>Ticket e Conversão</h3>
      <div class="sub">Por semana: explica as variações de receita.</div>
      ${lineChart(chartData, "ticket", "conv", "var(--accent-ink)", "var(--bad)", (v) => fmtBRL2(v), (v) => fmtPct(v))}
      <div class="legend"><span><i style="background:var(--accent-ink)"></i>Ticket médio</span><span><i style="background:var(--bad)"></i>% Conversão</span></div>
    </div>
  </div>`;
}

function renderCanal(
  canalKey: "meta" | "google",
  label: string,
  cur: Period,
  ads: CanalTotalsMap,
  adsPrev: CanalTotalsMap
): string {
  const a = ads[canalKey] || { investimento: 0, pedidos: 0, receita: 0, novos_clientes: 0, roas: null, cpa: null };
  const ap = adsPrev[canalKey] || { investimento: 0, pedidos: 0, receita: 0, novos_clientes: 0, roas: null, cpa: null };
  const camp = aggCampanhas(cur.from, cur.to)
    .filter((c) => c.plataforma === canalKey)
    .sort((x, y) => y.inv - x.inv);

  return `
  <h1>${label}</h1>
  <p class="lede">Investimento, retorno e campanhas de ${label} no período selecionado.</p>
  <div class="kpis">
    ${kpiCard("Investimento", fmtBRL(a.investimento), deltaHTML(a.investimento, ap.investimento, true))}
    ${kpiCard("Receita atribuída", fmtBRL(a.receita), deltaHTML(a.receita, ap.receita))}
    ${kpiCard("ROAS", fmtRoas(a.roas), deltaHTML(a.roas, ap.roas))}
    ${kpiCard("CPA", a.cpa != null ? fmtBRL2(a.cpa) : "—", deltaHTML(a.cpa, ap.cpa, true))}
  </div>
  <div class="card">
    <h3>${label} · período</h3>
    <div class="sub">Período atual comparado ao anterior de mesma duração.</div>
    <div class="tablewrap"><table>
      <thead><tr><th>Métrica</th><th class="num">Anterior</th><th class="num">Atual</th><th class="num">Δ%</th></tr></thead>
      <tbody>
        ${tableRow("Investimento", a.investimento, ap.investimento, fmtBRL, true)}
        ${tableRow("Pedidos", a.pedidos, ap.pedidos, fmtInt)}
        ${tableRow("Receita", a.receita, ap.receita, fmtBRL)}
        ${tableRow("Novos clientes", a.novos_clientes, ap.novos_clientes, fmtInt)}
        ${tableRow("ROAS", a.roas, ap.roas, (v) => (v != null ? v.toFixed(2) + "x" : "—"))}
      </tbody>
    </table></div>
  </div>
  <div class="card">
    <h3>Campanhas</h3>
    <div class="sub">No período selecionado, ordenado por investimento.</div>
    <div class="tablewrap"><table>
      <thead><tr><th>Campanha</th><th class="num">Invest.</th><th class="num">Compras</th><th class="num">Receita</th><th class="num">ROAS</th></tr></thead>
      <tbody>${camp
        .map(
          (c) =>
            `<tr><td>${c.campanha}</td><td class="num">${fmtBRL(c.inv)}</td><td class="num">${fmtInt(
              c.compras
            )}</td><td class="num">${fmtBRL(c.receita)}</td><td class="num">${c.inv ? (c.receita / c.inv).toFixed(2) + "x" : "—"}</td></tr>`
        )
        .join("")}</tbody>
    </table></div>
  </div>`;
}

function renderCanaisProdutos(cur: Period, ads: CanalTotalsMap, adsPrev: CanalTotalsMap): string {
  const canais: Canal[] = ["meta", "google", "direto", "outros"];
  const canalLabel: Record<Canal, string> = {
    meta: "Meta Ads",
    google: "Google Ads",
    direto: "Direto",
    outros: "Outros / Orgânico",
  };
  let totalReceita = 0;
  canais.forEach((c) => (totalReceita += ads[c]?.receita || 0));

  const rows = canais
    .map((c) => {
      const a = ads[c] || { pedidos: 0, receita: 0 };
      const ap = adsPrev[c] || { receita: 0 };
      const share = totalReceita ? a.receita / totalReceita : 0;
      return `<tr><td>${canalLabel[c]}</td><td class="num">${fmtInt(a.pedidos)}</td><td class="num">${fmtBRL(
        a.receita
      )}</td><td class="num">${fmtPct(share)}</td><td class="num">${deltaCellHTML(a.receita, ap.receita)}</td></tr>`;
    })
    .join("");

  const topCriativosRows = aggCriativos(cur.from, cur.to)
    .sort((a, b) => b.inv - a.inv)
    .slice(0, 5)
    .map((c) => `<tr><td>${c.ad}</td><td class="num">${fmtBRL(c.inv)}</td><td class="num">${c.inv ? (c.receita / c.inv).toFixed(2) + "x" : "—"}</td></tr>`)
    .join("");

  return `
  <h1>Canais e Produtos</h1>
  <p class="lede">Participação de cada canal de aquisição na receita do período.</p>
  <div class="card">
    <h3>Receita por canal</h3>
    <div class="sub">Classificação por UTM / origem do pedido no Shopify.</div>
    <div class="tablewrap"><table>
      <thead><tr><th>Canal</th><th class="num">Pedidos</th><th class="num">Receita</th><th class="num">% do total</th><th class="num">Δ% receita</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
  </div>
  <div class="card">
    <h3>Criativos de maior investimento (Meta) — últimos 30 dias</h3>
    <div class="sub">Atalho para a aba Criativos, mostrando os 5 principais.</div>
    <div class="tablewrap"><table>
      <thead><tr><th>Anúncio</th><th class="num">Invest.</th><th class="num">ROAS</th></tr></thead>
      <tbody>${topCriativosRows}</tbody>
    </table></div>
  </div>`;
}

function renderRecuperacao(cur: DateRange, prev: DateRange, ads: CanalTotalsMap, adsPrev: CanalTotalsMap): string {
  const ab = sumAbandoned(cur.from, cur.to);
  const abp = sumAbandoned(prev.from, prev.to);
  const organico = {
    pedidos: (ads.direto?.pedidos || 0) + (ads.outros?.pedidos || 0),
    receita: (ads.direto?.receita || 0) + (ads.outros?.receita || 0),
  };
  const organicoPrev = {
    pedidos: (adsPrev.direto?.pedidos || 0) + (adsPrev.outros?.pedidos || 0),
    receita: (adsPrev.direto?.receita || 0) + (adsPrev.outros?.receita || 0),
  };

  return `
  <h1>Recuperação e Orgânico</h1>
  <p class="lede">Checkouts abandonados e recuperados, mais o desempenho dos pedidos de tráfego direto/orgânico.</p>
  <div class="kpis">
    ${kpiCard("Checkouts abandonados", fmtInt(ab.checkouts), deltaHTML(ab.checkouts, abp.checkouts, true))}
    ${kpiCard("Valor em aberto", fmtBRL(ab.subtotal), deltaHTML(ab.subtotal, abp.subtotal, true))}
    ${kpiCard("Recuperados", fmtInt(ab.recuperados), deltaHTML(ab.recuperados, abp.recuperados))}
    ${kpiCard("Taxa de recuperação", fmtPct(ab.taxa_recuperacao), deltaHTML(ab.taxa_recuperacao, abp.taxa_recuperacao))}
  </div>
  <div class="card">
    <h3>Direto + Orgânico (sem mídia paga)</h3>
    <div class="sub">Pedidos atribuídos a canal direto ou sem UTM identificável.</div>
    <div class="tablewrap"><table>
      <thead><tr><th>Métrica</th><th class="num">Anterior</th><th class="num">Atual</th><th class="num">Δ%</th></tr></thead>
      <tbody>
        ${tableRow("Pedidos", organico.pedidos, organicoPrev.pedidos, fmtInt)}
        ${tableRow("Receita", organico.receita, organicoPrev.receita, fmtBRL)}
      </tbody>
    </table></div>
  </div>`;
}

function renderCriativos(cur: Period): string {
  const sorted = aggCriativos(cur.from, cur.to).sort((a, b) => b.inv - a.inv);
  const rows = sorted
    .map(
      (c) =>
        `<tr><td>${c.ad}</td><td>${c.campanha}<br><span style="color:var(--fg-muted);font-size:11.5px">${
          c.adset
        }</span></td><td class="num">${fmtBRL(c.inv)}</td><td class="num">${fmtInt(c.compras)}</td><td class="num">${fmtBRL(
          c.receita
        )}</td><td class="num">${(c.receita / c.inv).toFixed(2)}x</td><td class="num">${fmtInt(c.cliques)}</td></tr>`
    )
    .join("");

  return `
  <h1>Criativos</h1>
  <p class="lede">Anúncios Meta de maior investimento no período selecionado, com retorno por peça.</p>
  <div class="card">
    <div class="tablewrap"><table>
      <thead><tr><th>Anúncio</th><th>Campanha / Conjunto</th><th class="num">Invest.</th><th class="num">Compras</th><th class="num">Receita</th><th class="num">ROAS</th><th class="num">Cliques</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
  </div>`;
}

function renderSync(): string {
  const now = Date.now() / 1000;
  function row(name: string, ts: number): string {
    const hrs = (now - ts) / 3600;
    const badge = hrs < 30 ? '<span class="badge ok">em dia</span>' : '<span class="badge warn">atrasado</span>';
    return `<tr><td>${name}</td><td>${fmtDateTime(ts)}</td><td class="num">${hrs.toFixed(1)}h atrás</td><td>${badge}</td></tr>`;
  }
  return `
  <h1>Atualização dos dados</h1>
  <p class="lede">Última gravação de cada fonte no BigQuery (guara-db). Sincronização automática a cada 6 horas via cron local.</p>
  <div class="card">
    <div class="tablewrap"><table>
      <thead><tr><th>Fonte</th><th>Última carga</th><th class="num">Há</th><th>Status</th></tr></thead>
      <tbody>
        ${row("Shopify (receita diária)", LAST_SYNC.shopify_diario)}
        ${row("Pedidos com UTM (ROAS pago / CAC)", LAST_SYNC.pedidos_diario)}
        ${row("Mídia (Meta + Google Ads)", LAST_SYNC.ads_diario)}
      </tbody>
    </table></div>
  </div>`;
}

export function render(): void {
  const range = state.custom;
  const prevRange = prevPeriodOf(range);
  const sh = sumShopify(range.from, range.to);
  const shPrev = sumShopify(prevRange.from, prevRange.to);
  const ads = sumAdsAllCanais(range.from, range.to);
  const adsPrev = sumAdsAllCanais(prevRange.from, prevRange.to);
  const cur: Period = { from: range.from, to: range.to, sh };
  const prev: Period = { from: prevRange.from, to: prevRange.to, sh: shPrev };

  el("metaLine").innerHTML =
    `<b>${fmtDateBR(fmtISO(range.from))}</b> a <b>${fmtDateBR(fmtISO(range.to))}</b> — comparado a ` +
    `<b>${fmtDateBR(fmtISO(prevRange.from))}</b> a <b>${fmtDateBR(fmtISO(prevRange.to))}</b> · mídia e Shopify até ` +
    `<b>${fmtDateBR(TODAY)}</b> · última carga no BigQuery há ${((Date.now() / 1000 - LAST_SYNC.pedidos_diario) / 3600).toFixed(1)}h`;

  let html: string;
  switch (state.tab) {
    case "overview":
      html = renderOverview(cur, prev, ads);
      break;
    case "loja":
      html = renderLoja(cur, prev);
      break;
    case "meta":
      html = renderCanal("meta", "Meta Ads", cur, ads, adsPrev);
      break;
    case "google":
      html = renderCanal("google", "Google Ads", cur, ads, adsPrev);
      break;
    case "canais":
      html = renderCanaisProdutos(cur, ads, adsPrev);
      break;
    case "recuperacao":
      html = renderRecuperacao(range, prevRange, ads, adsPrev);
      break;
    case "criativos":
      html = renderCriativos(cur);
      break;
    case "sync":
      html = renderSync();
      break;
  }
  el("content").innerHTML = html;

  el<HTMLInputElement>("dateFrom").value = fmtISO(range.from);
  el<HTMLInputElement>("dateTo").value = fmtISO(range.to);
}

export function wireControls(): void {
  const tabsEl = el("tabs");
  TABS.forEach(([key, label]) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.dataset.key = key;
    if (key === state.tab) b.classList.add("active");
    b.onclick = () => {
      state.tab = key;
      document.querySelectorAll<HTMLButtonElement>("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
      render();
    };
    tabsEl.appendChild(b);
  });

  const periodsEl = el("periods");
  PERIOD_LABELS.forEach(([key, label]) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.dataset.key = key;
    if (key === state.period) b.classList.add("active");
    b.onclick = () => {
      state.period = key;
      state.custom = PERIODS[key];
      document.querySelectorAll<HTMLButtonElement>("#periods button").forEach((x) => x.classList.toggle("active", x === b));
      render();
    };
    periodsEl.appendChild(b);
  });

  el<HTMLInputElement>("dateFrom").addEventListener("change", (e) => {
    const v = (e.target as HTMLInputElement).value;
    if (!v) return;
    state.period = null;
    state.custom = { from: new Date(v + "T00:00:00Z"), to: state.custom.to };
    document.querySelectorAll<HTMLButtonElement>("#periods button").forEach((x) => x.classList.remove("active"));
    render();
  });
  el<HTMLInputElement>("dateTo").addEventListener("change", (e) => {
    const v = (e.target as HTMLInputElement).value;
    if (!v) return;
    state.period = null;
    state.custom = { from: state.custom.from, to: new Date(v + "T00:00:00Z") };
    document.querySelectorAll<HTMLButtonElement>("#periods button").forEach((x) => x.classList.remove("active"));
    render();
  });
}
