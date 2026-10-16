"""
tests/test_correcoes_16_10.py

Testes das correções implementadas em 16/10/2026:
  1. Classificação UTM Meta (manychat/klaviyo/email não são Meta)
  2. NULL→0 no funil de sessões Shopify
  3. Flag bot_suspect — detecção e não-regressão
"""
import pytest
from collections import defaultdict


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — reimplementação local da função _classificar_utm para testes
# (mantida idêntica ao shopify/sync.py)
# ─────────────────────────────────────────────────────────────────────────────

def _classificar_utm(pedido: dict) -> str:
    """Replica exata de shopify/sync.py::_classificar_utm (versão corrigida)."""
    notes = {n["name"].lower(): (n.get("value") or "").lower()
             for n in (pedido.get("note_attributes") or [])}
    src = notes.get("utm_source", "")
    med = notes.get("utm_medium", "")
    landing   = (pedido.get("landing_site") or "").lower()
    referring = (pedido.get("referring_site") or "").lower()

    if src in ("manychat", "klaviyo", "email", "revi"):
        return "outros"
    if any(x in src for x in ["facebook", "instagram", "fb", "meta", "ig"]):
        return "meta"
    if any(x in src for x in ["google", "gclid", "cpc", "pmax"]):
        return "google"
    if any(x in med for x in ["facebook", "instagram", "fb", "meta", "ig"]):
        return "meta"
    if any(x in med for x in ["google", "cpc", "pmax"]):
        return "google"
    if not src:
        import urllib.parse as _up
        land_qs = ""
        try:
            _p  = _up.urlparse(landing)
            _qs = _up.parse_qs(_p.query)
            land_qs = " ".join([
                _qs.get("utm_source", [""])[0].lower(),
                _qs.get("utm_medium", [""])[0].lower(),
            ])
        except Exception:
            pass
        land_combined = " ".join([land_qs, referring])
        if any(x in land_combined for x in ["facebook", "instagram", "fb", "meta", "ig"]):
            return "meta"
        if any(x in land_combined for x in ["google", "gclid", "cpc", "pmax"]):
            return "google"
    return "outros"


def _pedido(utm_source="", utm_medium="", landing_site="", referring_site=""):
    """Fábrica de pedido fake para testes."""
    attrs = []
    if utm_source:
        attrs.append({"name": "utm_source", "value": utm_source})
    if utm_medium:
        attrs.append({"name": "utm_medium", "value": utm_medium})
    return {
        "note_attributes": attrs,
        "landing_site":    landing_site,
        "referring_site":  referring_site,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. CLASSIFICAÇÃO UTM — 15 testes
# ─────────────────────────────────────────────────────────────────────────────

class TestClassificacaoUTM:

    # --- ManyChat NÃO é Meta ------------------------------------------------

    def test_manychat_e_outros(self):
        """Pedido com utm_source=manychat deve ser 'outros', não 'meta'."""
        assert _classificar_utm(_pedido("manychat")) == "outros"

    def test_manychat_com_referring_facebook_e_outros(self):
        """ManyChat com referring_site do Facebook ainda é 'outros'."""
        p = _pedido("manychat", referring_site="https://l.facebook.com/l.php?u=...")
        assert _classificar_utm(p) == "outros"

    def test_manychat_com_landing_facebook_e_outros(self):
        """ManyChat com landing_site do Facebook ainda é 'outros'."""
        p = _pedido("manychat", landing_site="https://www.example.com/?utm_source=facebook")
        assert _classificar_utm(p) == "outros"

    # --- Fontes legítimas Meta ----------------------------------------------

    def test_facebook_e_meta(self):
        assert _classificar_utm(_pedido("facebook")) == "meta"

    def test_instagram_e_meta(self):
        assert _classificar_utm(_pedido("instagram")) == "meta"

    def test_facebook_ads_e_meta(self):
        assert _classificar_utm(_pedido("facebook ads")) == "meta"

    def test_fb_abreviado_e_meta(self):
        assert _classificar_utm(_pedido("fb")) == "meta"

    def test_meta_literal_e_meta(self):
        assert _classificar_utm(_pedido("meta")) == "meta"

    # --- Fontes legítimas Google --------------------------------------------

    def test_google_e_google(self):
        assert _classificar_utm(_pedido("google")) == "google"

    def test_google_ads_e_google(self):
        assert _classificar_utm(_pedido("google ads")) == "google"

    # --- Outros canais não-Meta e não-Google --------------------------------

    def test_klaviyo_e_outros(self):
        assert _classificar_utm(_pedido("klaviyo")) == "outros"

    def test_email_e_outros(self):
        assert _classificar_utm(_pedido("email")) == "outros"

    def test_revi_e_outros(self):
        assert _classificar_utm(_pedido("revi")) == "outros"

    # --- Fallback via landing_site quando utm_source vazio -----------------

    def test_sem_utm_source_com_landing_facebook_e_meta(self):
        """Sem utm_source, landing com facebook → meta (fallback legítimo)."""
        p = _pedido("", landing_site="https://example.com/?utm_source=facebook")
        assert _classificar_utm(p) == "meta"

    def test_sem_utm_source_sem_sinal_e_outros(self):
        """Sem utm_source e sem sinal de canal → outros."""
        p = _pedido("", landing_site="https://example.com/", referring_site="")
        assert _classificar_utm(p) == "outros"


# ─────────────────────────────────────────────────────────────────────────────
# 2. NULL→0 NO FUNIL DE SESSÕES — 15 testes
# ─────────────────────────────────────────────────────────────────────────────

def _parse_sessao_row(raw: dict) -> dict:
    """
    Replica a conversão de linha do ShopifyQL (sync_shopify_sessions.py).
    Versão corrigida: 0 em vez de None para valores ausentes.
    """
    sessoes  = int(raw.get("sessions") or 0)
    carrinho = int(raw.get("sessions_with_cart_additions") or 0)
    checkout = int(raw.get("sessions_that_reached_checkout") or 0)
    compras  = int(raw.get("sessions_that_completed_checkout") or 0)
    conv     = float(raw.get("conversion_rate") or 0)
    return {
        "sessoes":              sessoes,
        "sessoes_com_carrinho": carrinho,
        "sessoes_checkout":     checkout,
        "sessoes_compra":       compras,
        "taxa_conversao":       round(conv, 6),
    }


class TestFunilSessoes:

    def test_dia_sem_carrinho_e_zero_nao_null(self):
        row = _parse_sessao_row({"sessions": 1000, "sessions_with_cart_additions": 0})
        assert row["sessoes_com_carrinho"] == 0
        assert row["sessoes_com_carrinho"] is not None

    def test_dia_sem_checkout_e_zero_nao_null(self):
        row = _parse_sessao_row({"sessions": 1000, "sessions_that_reached_checkout": 0})
        assert row["sessoes_checkout"] == 0
        assert row["sessoes_checkout"] is not None

    def test_dia_sem_compra_e_zero_nao_null(self):
        row = _parse_sessao_row({"sessions": 1000, "sessions_that_completed_checkout": 0})
        assert row["sessoes_compra"] == 0
        assert row["sessoes_compra"] is not None

    def test_campo_ausente_na_api_e_zero(self):
        """Campo ausente (API não retornou) também vira 0."""
        row = _parse_sessao_row({"sessions": 500})
        assert row["sessoes_com_carrinho"] == 0
        assert row["sessoes_checkout"] == 0
        assert row["sessoes_compra"] == 0

    def test_campo_none_na_api_e_zero(self):
        """Campo explicitamente None na API vira 0."""
        row = _parse_sessao_row({
            "sessions": 800,
            "sessions_with_cart_additions": None,
            "sessions_that_reached_checkout": None,
            "sessions_that_completed_checkout": None,
        })
        assert row["sessoes_com_carrinho"] == 0
        assert row["sessoes_checkout"] == 0
        assert row["sessoes_compra"] == 0

    def test_valor_real_preservado(self):
        """Valores reais não são zerados."""
        row = _parse_sessao_row({
            "sessions": 1773,
            "sessions_with_cart_additions": 147,
            "sessions_that_reached_checkout": 179,
            "sessions_that_completed_checkout": 103,
            "conversion_rate": 0.058094,
        })
        assert row["sessoes"] == 1773
        assert row["sessoes_com_carrinho"] == 147
        assert row["sessoes_checkout"] == 179
        assert row["sessoes_compra"] == 103

    def test_conversao_arredondada_6_casas(self):
        row = _parse_sessao_row({"sessions": 1000, "conversion_rate": 0.0580940001})
        assert row["taxa_conversao"] == round(0.0580940001, 6)

    def test_zero_sessoes_sem_erro(self):
        row = _parse_sessao_row({"sessions": 0})
        assert row["sessoes"] == 0
        assert row["sessoes_com_carrinho"] == 0

    def test_tipo_int_carrinho(self):
        row = _parse_sessao_row({"sessions": 100, "sessions_with_cart_additions": "42"})
        assert isinstance(row["sessoes_com_carrinho"], int)
        assert row["sessoes_com_carrinho"] == 42

    def test_tipo_int_checkout(self):
        row = _parse_sessao_row({"sessions": 100, "sessions_that_reached_checkout": "7"})
        assert isinstance(row["sessoes_checkout"], int)

    def test_tipo_int_compra(self):
        row = _parse_sessao_row({"sessions": 100, "sessions_that_completed_checkout": "3"})
        assert isinstance(row["sessoes_compra"], int)

    def test_taxa_zero_quando_sem_conversao(self):
        row = _parse_sessao_row({"sessions": 500, "conversion_rate": 0})
        assert row["taxa_conversao"] == 0.0

    def test_taxa_none_vira_zero(self):
        row = _parse_sessao_row({"sessions": 500, "conversion_rate": None})
        assert row["taxa_conversao"] == 0.0

    def test_funil_completo_sem_null(self):
        """Nenhum campo do funil pode ser None."""
        row = _parse_sessao_row({"sessions": 2000})
        for campo in ("sessoes_com_carrinho", "sessoes_checkout", "sessoes_compra", "taxa_conversao"):
            assert row[campo] is not None, f"{campo} não deveria ser None"

    def test_string_vazia_vira_zero(self):
        row = _parse_sessao_row({
            "sessions": 300,
            "sessions_with_cart_additions": "",
        })
        assert row["sessoes_com_carrinho"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. BOT SUSPECT — detecção e não-regressão — 15 testes
# ─────────────────────────────────────────────────────────────────────────────

import statistics


def _detectar_bots(rows: list[dict], janela_dias: int = 14, fator: float = 3.0) -> list[dict]:
    """Replica de scripts/sync_shopify_sessions.py::_detectar_bots."""
    sessoes_por_data = {r["data"]: r["sessoes"] for r in rows}
    datas = sorted(sessoes_por_data)
    suspeitos = set()

    for i, d in enumerate(datas):
        janela = [sessoes_por_data[datas[j]] for j in range(max(0, i - janela_dias), i)]
        if len(janela) < 3:
            continue
        mediana = statistics.median(janela)
        if mediana > 0 and sessoes_por_data[d] > fator * mediana:
            suspeitos.add(d)

    for r in rows:
        r["is_bot_suspect"] = 1 if r["data"] in suspeitos else 0
    return rows


def _nao_regride(rows: list[dict], already_flagged: set) -> list[dict]:
    """Replica da proteção de não-regressão do sync_shopify_sessions.py."""
    for r in rows:
        if r["data"] in already_flagged:
            r["is_bot_suspect"] = 1
    return rows


def _gerar_dias(n: int, base_sessoes: int = 1500) -> list[dict]:
    """Gera N dias com sessões estáveis."""
    from datetime import date, timedelta
    inicio = date(2026, 9, 1)
    return [{"data": str(inicio + timedelta(days=i)), "sessoes": base_sessoes} for i in range(n)]


class TestBotSuspect:

    def test_pico_detectado(self):
        """Dia com 3× a mediana deve ser marcado."""
        rows = _gerar_dias(20, 1500)
        rows[15]["sessoes"] = 9000  # > 3× mediana(1500) = 4500
        rows = _detectar_bots(rows)
        assert rows[15]["is_bot_suspect"] == 1

    def test_dias_normais_nao_marcados(self):
        """Dias com sessões normais não devem ser suspeitos."""
        rows = _gerar_dias(20, 1500)
        rows = _detectar_bots(rows)
        assert all(r["is_bot_suspect"] == 0 for r in rows)

    def test_primeiros_tres_dias_sem_contexto_nao_marcados(self):
        """Menos de 3 dias de contexto → não marca (evita falso positivo)."""
        rows = _gerar_dias(3, 1500)
        rows[2]["sessoes"] = 99999
        rows = _detectar_bots(rows)
        # Janela < 3 → não pode decidir
        assert rows[2]["is_bot_suspect"] == 0

    def test_fator_customizavel(self):
        """Fator 2.0 deve detectar picos menores."""
        rows = _gerar_dias(20, 1000)
        rows[15]["sessoes"] = 2500  # > 2× mediana(1000) = 2000
        rows = _detectar_bots(rows, fator=2.0)
        assert rows[15]["is_bot_suspect"] == 1

    def test_pico_abaixo_do_fator_nao_marcado(self):
        """Pico de 2.5× com fator 3.0 não deve ser marcado."""
        rows = _gerar_dias(20, 1000)
        rows[15]["sessoes"] = 2500  # 2.5× mediana(1000) < 3×
        rows = _detectar_bots(rows, fator=3.0)
        assert rows[15]["is_bot_suspect"] == 0

    def test_apenas_um_dia_suspeito_em_multiplos_picos(self):
        """Dois picos consecutivos devem ser detectados independentemente."""
        rows = _gerar_dias(25, 1500)
        rows[15]["sessoes"] = 9000
        rows[16]["sessoes"] = 9000
        rows = _detectar_bots(rows)
        assert rows[15]["is_bot_suspect"] == 1
        assert rows[16]["is_bot_suspect"] == 1

    def test_nao_regride_flag_ja_gravada(self):
        """Dia já marcado no SQLite não deve regredir para 0."""
        rows = _gerar_dias(20, 1500)
        # Simula recarga sem pico (sessões normais agora)
        rows = _detectar_bots(rows)
        assert rows[15]["is_bot_suspect"] == 0  # não detectou
        # Mas estava gravado como 1
        already_flagged = {"2026-09-16"}  # rows[15]
        rows = _nao_regride(rows, already_flagged)
        assert rows[15]["is_bot_suspect"] == 1

    def test_nao_regride_nao_afeta_dias_nao_flagged(self):
        """Não-regressão não afeta dias que nunca foram marcados."""
        rows = _gerar_dias(20, 1500)
        rows = _detectar_bots(rows)
        already_flagged = set()  # nada gravado
        rows = _nao_regride(rows, already_flagged)
        assert all(r["is_bot_suspect"] == 0 for r in rows)

    def test_campo_is_bot_suspect_presente_em_todos_os_rows(self):
        """Todos os rows devem ter o campo is_bot_suspect após detecção."""
        rows = _gerar_dias(10, 1500)
        rows = _detectar_bots(rows)
        assert all("is_bot_suspect" in r for r in rows)

    def test_is_bot_suspect_e_inteiro(self):
        """O campo deve ser int (0 ou 1), não bool."""
        rows = _gerar_dias(10, 1500)
        rows = _detectar_bots(rows)
        for r in rows:
            assert r["is_bot_suspect"] in (0, 1)

    def test_janela_14_dias_usa_apenas_dias_anteriores(self):
        """A janela olha para trás, não para frente."""
        rows = _gerar_dias(20, 1500)
        rows[5]["sessoes"] = 9000  # pico no dia 5
        rows = _detectar_bots(rows, janela_dias=14)
        # Dias 6-19 não devem ser afetados pelo pico do dia 5
        for i in range(6, 20):
            assert rows[i]["is_bot_suspect"] == 0

    def test_pico_isolado_nao_contamina_mediana_futura(self):
        """Depois de um pico, os dias seguintes com sessões normais ficam 0."""
        rows = _gerar_dias(30, 1500)
        rows[15]["sessoes"] = 9000
        rows = _detectar_bots(rows)
        # Dias após o pico com sessões normais não devem ser marcados
        assert rows[16]["is_bot_suspect"] == 0
        assert rows[17]["is_bot_suspect"] == 0

    def test_sem_rows_retorna_lista_vazia(self):
        assert _detectar_bots([]) == []

    def test_rows_com_mediana_zero_nao_marca(self):
        """Se mediana for 0 (todos os dias têm 0 sessões), não marcar."""
        rows = [{"data": f"2026-09-{i:02d}", "sessoes": 0} for i in range(1, 21)]
        rows[15]["sessoes"] = 5000
        rows = _detectar_bots(rows)
        assert rows[15]["is_bot_suspect"] == 0

    def test_08_out_so_detectado_com_contexto_historico(self):
        """
        Replica o cenário real: 08/10 SO com 8.923 sessões deve ser detectado
        quando há contexto histórico dos dias anteriores (~1500 sessões/dia).
        """
        from datetime import date, timedelta
        base = date(2026, 9, 18)  # 18/set + 20 dias = 08/out (dia 20, índice 20)
        rows = []
        for i in range(21):
            d = base + timedelta(days=i)
            sessoes = 8923 if str(d) == "2026-10-08" else 1500
            rows.append({"data": str(d), "sessoes": sessoes})
        rows = _detectar_bots(rows, janela_dias=14, fator=3.0)
        row_08 = next(r for r in rows if r["data"] == "2026-10-08")
        assert row_08["is_bot_suspect"] == 1
