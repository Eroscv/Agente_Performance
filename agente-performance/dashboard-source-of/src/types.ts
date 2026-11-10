export interface ShopifyDay {
  data: string;
  receita: number;
  pedidos: number;
  novos_clientes: number;
  recompradores: number;
  ticket_medio: number;
  total_desconto: number;
  frete: number;
  pedidos_gt300: number;
  gross_sales: number;
  sessoes: number | null;
  sessoes_compra: number | null;
  taxa_conversao: number | null;
}

export type Canal = "meta" | "google" | "direto" | "outros";

export interface AdsDay {
  data: string;
  canal: Canal;
  investimento: number;
  pedidos?: number;
  receita?: number;
  novos_clientes?: number;
}

export interface CampanhaDiaria {
  data: string;
  plataforma: "meta" | "google";
  campanha: string;
  inv: number;
  compras: number;
  receita: number;
}

export interface CriativoDiaria {
  data: string;
  campanha: string;
  adset: string;
  ad: string;
  inv: number;
  compras: number;
  receita: number;
  cliques: number;
}

export interface TopCampanha {
  plataforma: "meta" | "google";
  campanha: string;
  inv: number;
  compras: number;
  receita: number;
}

export interface TopCriativo {
  campanha: string;
  adset: string;
  ad: string;
  inv: number;
  compras: number;
  receita: number;
  cliques: number;
  inicio: string;
}

export interface AbandonedDay {
  data: string;
  checkouts: number;
  subtotal: number;
  recuperados: number;
}

export interface AbandonedOrigemDay extends AbandonedDay {
  origem: string;
}

export interface LastSync {
  pedidos_diario: number;
  ads_diario: number;
  shopify_diario: number;
}

export interface DateRange {
  from: Date;
  to: Date;
}

export interface CanalTotals {
  investimento: number;
  pedidos: number;
  receita: number;
  novos_clientes: number;
  roas: number | null;
  cpa: number | null;
  cac: number | null;
}

export type CanalTotalsMap = Partial<Record<Canal, CanalTotals>>;

export interface ShopifyTotals {
  receita: number;
  pedidos: number;
  novos_clientes: number;
  recompradores: number;
  total_desconto: number;
  frete: number;
  pedidos_gt300: number;
  gross_sales: number;
  sessoes: number;
  sessoes_compra: number;
  ticket_medio: number | null;
  taxa_conversao: number | null;
  pct_desconto: number | null;
}

export interface AbandonedTotals {
  checkouts: number;
  subtotal: number;
  recuperados: number;
  taxa_recuperacao: number | null;
}

export type TabKey =
  | "overview"
  | "loja"
  | "meta"
  | "google"
  | "canais"
  | "recuperacao"
  | "criativos"
  | "recompra"
  | "sync";

export type PeriodKey =
  | "last_week_closed"
  | "this_week"
  | "last7"
  | "last14"
  | "last30"
  | "month_current"
  | "month_prev";

export interface WeeklyPoint {
  label: string;
  novos: number;
  cac: number | null;
  ticket: number | null;
  conv: number | null;
}

export interface BarLinePoint {
  label: string;
  bar: number;
  line: number | null;
}

export interface AbandonedOrigemTotals {
  origem: string;
  checkouts: number;
  subtotal: number;
  recuperados: number;
  taxa_recuperacao: number | null;
}

export interface EstadoDay {
  data: string;
  estado: string;
  pedidos: number;
  receita: number;
}

export interface SkuDay {
  data: string;
  sku: string;
  produto: string | null;
  unidades: number;
  pedidos: number;
  receita: number;
}

export interface OrigemDay {
  data: string;
  origem: string;
  pedidos: number;
  receita: number;
}

export interface RecompraMes {
  mes: string;
  pedidos: number;
  novos_clientes: number;
  recompradores: number;
  receita: number;
}

export interface EstadoTotals {
  estado: string;
  pedidos: number;
  receita: number;
}

export interface SkuTotals {
  sku: string;
  produto: string | null;
  unidades: number;
  pedidos: number;
  receita: number;
}

export interface OrigemTotals {
  origem: string;
  pedidos: number;
  receita: number;
}
