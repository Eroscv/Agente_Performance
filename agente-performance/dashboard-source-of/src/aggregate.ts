import {
  ABANDONED,
  ABANDONED_ORIGEM,
  ADS,
  CAMPANHAS_DIARIO,
  CRIATIVOS_DIARIO,
  PEDIDOS_ESTADO,
  PEDIDOS_ORIGEM,
  PEDIDOS_SKU,
  SHOPIFY,
} from "./data";
import { addDays, inRange, mondayOf, parseISO, TODAY } from "./dates";
import type {
  AbandonedOrigemTotals,
  AbandonedTotals,
  BarLinePoint,
  Canal,
  CanalTotals,
  CanalTotalsMap,
  EstadoTotals,
  OrigemTotals,
  SkuTotals,
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
      cac: c.novos_clientes ? c.investimento / c.novos_clientes : null,
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

// Primeiro dia com veiculação de cada anúncio em todo o histórico carregado (a carga não traz a data de criação).
const INICIO_CRIATIVO = new Map<string, string>();
for (const r of CRIATIVOS_DIARIO) {
  const atual = INICIO_CRIATIVO.get(r.ad);
  if (!atual || r.data < atual) INICIO_CRIATIVO.set(r.ad, r.data);
}
export const PRIMEIRO_DIA_DOS_DADOS = CRIATIVOS_DIARIO[0].data;

export function aggCriativos(from: Date, to: Date): TopCriativo[] {
  const byAd = new Map<string, TopCriativo>();
  for (const r of CRIATIVOS_DIARIO) {
    if (!inRange(r.data, from, to)) continue;
    const c = byAd.get(r.ad) ?? {
      campanha: r.campanha,
      adset: r.adset,
      ad: r.ad,
      inv: 0,
      compras: 0,
      receita: 0,
      cliques: 0,
      inicio: INICIO_CRIATIVO.get(r.ad) ?? r.data,
    };
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

export function sumAbandonedPorOrigem(from: Date, to: Date): AbandonedOrigemTotals[] {
  const byOrigem = new Map<string, AbandonedOrigemTotals>();
  for (const r of ABANDONED_ORIGEM) {
    if (!inRange(r.data, from, to)) continue;
    const o = byOrigem.get(r.origem) ?? { origem: r.origem, checkouts: 0, subtotal: 0, recuperados: 0, taxa_recuperacao: null };
    o.checkouts += r.checkouts;
    o.subtotal += r.subtotal;
    o.recuperados += r.recuperados;
    byOrigem.set(r.origem, o);
  }
  return [...byOrigem.values()]
    .map((o) => ({ ...o, taxa_recuperacao: o.checkouts ? o.recuperados / o.checkouts : null }))
    .sort((a, b) => b.checkouts - a.checkouts);
}

export function abandonedDiario(from: Date, to: Date): BarLinePoint[] {
  return ABANDONED.filter((r) => inRange(r.data, from, to)).map((r) => ({
    label: r.data.slice(8) + "/" + r.data.slice(5, 7),
    bar: r.checkouts,
    line: r.checkouts ? r.recuperados / r.checkouts : null,
  }));
}

/** Investimento e ROAS por dia; `canais` filtra os canais de mídia somados. */
export function investRoasDiario(from: Date, to: Date, canais: Canal[]): BarLinePoint[] {
  const byDay = new Map<string, { inv: number; receita: number }>();
  for (const r of ADS) {
    if (!canais.includes(r.canal) || !inRange(r.data, from, to)) continue;
    const d = byDay.get(r.data) ?? { inv: 0, receita: 0 };
    d.inv += r.investimento || 0;
    d.receita += r.receita || 0;
    byDay.set(r.data, d);
  }
  return [...byDay.entries()]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([data, d]) => ({
      label: data.slice(8) + "/" + data.slice(5, 7),
      bar: d.inv,
      line: d.inv ? d.receita / d.inv : null,
    }));
}

export function aggEstados(from: Date, to: Date): EstadoTotals[] {
  const by = new Map<string, EstadoTotals>();
  for (const r of PEDIDOS_ESTADO) {
    if (!inRange(r.data, from, to)) continue;
    const e = by.get(r.estado) ?? { estado: r.estado, pedidos: 0, receita: 0 };
    e.pedidos += r.pedidos;
    e.receita += r.receita;
    by.set(r.estado, e);
  }
  return [...by.values()].sort((a, b) => b.pedidos - a.pedidos);
}

export function aggSkus(from: Date, to: Date): SkuTotals[] {
  const by = new Map<string, SkuTotals>();
  for (const r of PEDIDOS_SKU) {
    if (!inRange(r.data, from, to)) continue;
    const s = by.get(r.sku) ?? { sku: r.sku, produto: r.produto, unidades: 0, pedidos: 0, receita: 0 };
    s.unidades += r.unidades;
    s.pedidos += r.pedidos;
    s.receita += r.receita;
    by.set(r.sku, s);
  }
  return [...by.values()].sort((a, b) => b.receita - a.receita);
}

/** Pedidos por utm_source no período, filtrados pelas origens em `casa`. */
export function aggOrigens(from: Date, to: Date, casa: RegExp): OrigemTotals[] {
  const by = new Map<string, OrigemTotals>();
  for (const r of PEDIDOS_ORIGEM) {
    if (!casa.test(r.origem) || !inRange(r.data, from, to)) continue;
    const o = by.get(r.origem) ?? { origem: r.origem, pedidos: 0, receita: 0 };
    o.pedidos += r.pedidos;
    o.receita += r.receita;
    by.set(r.origem, o);
  }
  return [...by.values()].sort((a, b) => b.pedidos - a.pedidos);
}
