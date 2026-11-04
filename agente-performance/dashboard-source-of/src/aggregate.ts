import { ABANDONED, ADS, CAMPANHAS_DIARIO, CRIATIVOS_DIARIO, SHOPIFY } from "./data";
import { addDays, inRange, mondayOf, parseISO, TODAY } from "./dates";
import type {
  AbandonedTotals,
  Canal,
  CanalTotals,
  CanalTotalsMap,
  ShopifyTotals,
  TopCampanha,
  TopCriativo,
  WeeklyPoint,
} from "./types";

const todayD = parseISO(TODAY);

export function sumShopify(from: Date, to: Date): ShopifyTotals {
  const rows = SHOPIFY.filter((r) => inRange(r.data, from, to));
  let receita = 0,
    pedidos = 0,
    novos_clientes = 0,
    recompradores = 0,
    total_desconto = 0,
    frete = 0,
    pedidos_gt300 = 0,
    gross_sales = 0,
    sessoes = 0,
    sessoes_compra = 0;
  let hasSessions = false;

  for (const r of rows) {
    receita += r.receita;
    pedidos += r.pedidos;
    novos_clientes += r.novos_clientes;
    recompradores += r.recompradores;
    total_desconto += r.total_desconto;
    frete += r.frete;
    pedidos_gt300 += r.pedidos_gt300;
    gross_sales += r.gross_sales;
    if (r.sessoes != null && r.sessoes_compra != null) {
      sessoes += r.sessoes;
      sessoes_compra += r.sessoes_compra;
      hasSessions = true;
    }
  }

  return {
    receita,
    pedidos,
    novos_clientes,
    recompradores,
    total_desconto,
    frete,
    pedidos_gt300,
    gross_sales,
    sessoes,
    sessoes_compra,
    ticket_medio: pedidos ? receita / pedidos : null,
    taxa_conversao: hasSessions && sessoes > 0 ? sessoes_compra / sessoes : null,
    pct_desconto: gross_sales ? total_desconto / gross_sales : null,
  };
}

export function sumAdsAllCanais(from: Date, to: Date): CanalTotalsMap {
  const byCanal: Record<string, { investimento: number; pedidos: number; receita: number; novos_clientes: number }> = {};
  for (const r of ADS) {
    if (!inRange(r.data, from, to)) continue;
    const c = (byCanal[r.canal] ??= { investimento: 0, pedidos: 0, receita: 0, novos_clientes: 0 });
    c.investimento += r.investimento || 0;
    c.pedidos += r.pedidos || 0;
    c.receita += r.receita || 0;
    c.novos_clientes += r.novos_clientes || 0;
  }
  const out: CanalTotalsMap = {};
  (Object.keys(byCanal) as Canal[]).forEach((canal) => {
    const c = byCanal[canal];
    const totals: CanalTotals = {
      ...c,
      roas: c.investimento ? c.receita / c.investimento : null,
      cpa: c.pedidos ? c.investimento / c.pedidos : null,
    };
    out[canal] = totals;
  });
  return out;
}

export function investTotalAds(from: Date, to: Date): number {
  let inv = 0;
  for (const r of ADS) {
    if ((r.canal === "meta" || r.canal === "google") && inRange(r.data, from, to)) inv += r.investimento || 0;
  }
  return inv;
}

export function sumAbandoned(from: Date, to: Date): AbandonedTotals {
  let checkouts = 0,
    subtotal = 0,
    recuperados = 0;
  for (const r of ABANDONED) {
    if (!inRange(r.data, from, to)) continue;
    checkouts += r.checkouts;
    subtotal += r.subtotal;
    recuperados += r.recuperados;
  }
  return { checkouts, subtotal, recuperados, taxa_recuperacao: checkouts ? recuperados / checkouts : null };
}

export function aggCampanhas(from: Date, to: Date): TopCampanha[] {
  const byKey = new Map<string, TopCampanha>();
  for (const r of CAMPANHAS_DIARIO) {
    if (!inRange(r.data, from, to)) continue;
    const key = r.plataforma + "|" + r.campanha;
    const c = byKey.get(key) ?? { plataforma: r.plataforma, campanha: r.campanha, inv: 0, compras: 0, receita: 0 };
    c.inv += r.inv;
    c.compras += r.compras;
    c.receita += r.receita;
    byKey.set(key, c);
  }
  return [...byKey.values()];
}

export function aggCriativos(from: Date, to: Date): TopCriativo[] {
  const byAd = new Map<string, TopCriativo>();
  for (const r of CRIATIVOS_DIARIO) {
    if (!inRange(r.data, from, to)) continue;
    const c = byAd.get(r.ad) ?? { campanha: r.campanha, adset: r.adset, ad: r.ad, inv: 0, compras: 0, receita: 0, cliques: 0 };
    c.inv += r.inv;
    c.compras += r.compras;
    c.receita += r.receita;
    c.cliques += r.cliques;
    byAd.set(r.ad, c);
  }
  return [...byAd.values()];
}

export function weeklySeries(to: string, weeks: number): WeeklyPoint[] {
  const anchor = mondayOf(parseISO(to));
  const out: WeeklyPoint[] = [];
  for (let i = weeks - 1; i >= 0; i--) {
    const from = addDays(anchor, -7 * i);
    const rawEnd = addDays(from, 6);
    const end = rawEnd < todayD ? rawEnd : todayD;
    const sh = sumShopify(from, end);
    const ad = investTotalAds(from, end);
    out.push({
      label: `${String(from.getUTCDate()).padStart(2, "0")}/${String(from.getUTCMonth() + 1).padStart(2, "0")}`,
      novos: sh.novos_clientes,
      cac: sh.novos_clientes ? ad / sh.novos_clientes : null,
      ticket: sh.ticket_medio,
      conv: sh.taxa_conversao,
    });
  }
  return out;
}
