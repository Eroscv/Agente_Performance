"""
tests/test_card_helpers_e_validacao.py
Testes para card_helpers.py, card_validacao.py e card_status (filtros).
25+ casos.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import date, timedelta

from card_helpers import parse_prazo, match_usuario, match_cliente, match_frente
from card_validacao import validar_card, sugerir_area


# ── parse_prazo ───────────────────────────────────────────────────────────────

def test_01_prazo_iso():
    assert parse_prazo("2026-11-15") == "2026-11-15"

def test_02_prazo_br_barra():
    assert parse_prazo("15/11/2026") == "2026-11-15"

def test_03_prazo_br_sem_ano():
    resultado = parse_prazo("15/11")
    assert resultado is not None
    assert resultado.endswith("-11-15")

def test_04_prazo_amanha():
    esperado = (date.today() + timedelta(days=1)).isoformat()
    assert parse_prazo("amanhã") == esperado

def test_05_prazo_amanha_sem_acento():
    esperado = (date.today() + timedelta(days=1)).isoformat()
    assert parse_prazo("amanha") == esperado

def test_06_prazo_mais_n():
    esperado = (date.today() + timedelta(days=7)).isoformat()
    assert parse_prazo("+7") == esperado

def test_07_prazo_mais_n_sem_sinal():
    # "10" pode ser interpretado como +10 dias
    resultado = parse_prazo("10")
    # Aceita None ou data válida — não deve explodir
    assert resultado is None or len(resultado) == 10

def test_08_prazo_invalido_retorna_none():
    assert parse_prazo("ontem") is None
    assert parse_prazo("xxx") is None
    assert parse_prazo("") is None

def test_09_prazo_hoje():
    esperado = date.today().isoformat()
    assert parse_prazo("hoje") == esperado

# ── match_usuario ─────────────────────────────────────────────────────────────

def test_10_match_usuario_exato():
    resultado = match_usuario("Bianca")
    assert resultado is not None

def test_11_match_usuario_case_insensitive():
    assert match_usuario("bianca") == match_usuario("BIANCA")

def test_12_match_usuario_parcial():
    resultado = match_usuario("Bian")
    assert resultado is not None and "Bianca" in resultado

def test_13_match_usuario_inexistente():
    assert match_usuario("Zylmundo") is None

def test_14_match_usuario_vazio():
    assert match_usuario("") is None

# ── match_cliente / match_frente ──────────────────────────────────────────────

def test_15_match_cliente_source():
    resultado = match_cliente("source of")
    assert resultado is not None

def test_16_match_cliente_case_insensitive():
    r1 = match_cliente("Source OF")
    r2 = match_cliente("source of")
    assert r1 == r2

def test_17_match_cliente_inexistente():
    assert match_cliente("Empresa Desconhecida XYZ") is None

def test_18_match_frente_ads():
    resultado = match_frente("ADS")
    assert resultado is not None

def test_19_match_frente_social():
    resultado = match_frente("social")
    assert resultado is not None

def test_20_match_frente_inexistente():
    assert match_frente("Frente Alienígena") is None

# ── card_validacao ────────────────────────────────────────────────────────────

def test_21_validacao_titulo_ok():
    tarefa = {"titulo": "Criar campanha Black Friday", "responsavel_nome": "Bianca"}
    ok, erros = validar_card(tarefa, "Performance", "Reunião Semanal")
    assert ok is True
    assert all("Título" not in e for e in erros)

def test_22_validacao_titulo_vazio():
    tarefa = {"titulo": "", "responsavel_nome": "Bianca"}
    ok, erros = validar_card(tarefa, "Performance")
    assert ok is False
    assert any("vazio" in e.lower() or "curto" in e.lower() for e in erros)

def test_23_validacao_titulo_placeholder():
    for placeholder in ["tarefa 1", "to-do", "task", "item 3", "pendência 2"]:
        tarefa = {"titulo": placeholder, "responsavel_nome": "Bianca"}
        ok, _ = validar_card(tarefa, "Performance")
        assert ok is False, f"Deveria rejeitar placeholder: '{placeholder}'"

def test_24_validacao_titulo_muito_longo():
    titulo_longo = "A" * 250
    tarefa = {"titulo": titulo_longo, "responsavel_nome": "Bianca"}
    ok, erros = validar_card(tarefa, "Performance")
    assert ok is False
    assert any("longo" in e.lower() for e in erros)

def test_25_validacao_area_inconsistente_gera_aviso():
    # ROAS no título sugere Performance, mas área é 3P — gera aviso mas não bloqueia
    tarefa = {"titulo": "Otimizar ROAS campanha Google", "responsavel_nome": "Bianca"}
    ok, msgs = validar_card(tarefa, "3P")
    assert ok is True  # não bloqueia
    assert any("área" in m.lower() or "area" in m.lower() for m in msgs)

def test_26_validacao_responsavel_desconhecido_gera_aviso():
    tarefa = {"titulo": "Criar relatório mensal", "responsavel_nome": "Zylmundo Foguete"}
    ok, msgs = validar_card(tarefa, "Gestão")
    assert ok is True  # não bloqueia
    assert any("responsável" in m.lower() or "responsavel" in m.lower() for m in msgs)

def test_27_sugerir_area_performance():
    assert sugerir_area("Otimizar ROAS meta ads campanha") == "Performance"

def test_28_sugerir_area_3p():
    assert sugerir_area("Criar arte para reels de lançamento") == "3P"

def test_29_sugerir_area_ambigua_retorna_none():
    # Título que não tem palavras-chave — sem sugestão
    resultado = sugerir_area("Reunião de alinhamento geral")
    assert resultado is None

def test_30_validacao_titulo_curto_5_chars():
    tarefa = {"titulo": "Abcd", "responsavel_nome": "Bianca"}  # 4 chars
    ok, _ = validar_card(tarefa, "Performance")
    assert ok is False

def test_31_validacao_titulo_exatamente_5_chars():
    tarefa = {"titulo": "Abcde", "responsavel_nome": "Bianca"}  # 5 chars — limite
    ok, _ = validar_card(tarefa, "Performance")
    assert ok is True


# ── Testes adicionais: nome parcial + status ──────────────────────────────────

def test_32_parse_arg_nome_parcial_com_status():
    from card_status import _parse_arg, _STATUS_VALIDOS
    from card_helpers import match_usuario

    # "Bian em andamento" → nome_parcial="Bian", status="Em andamento"
    nome, status = _parse_arg("Bian em andamento")
    assert status == "Em andamento", f"status={status}"
    # match_usuario deve resolver "Bian" → "Bianca Garbin"
    resolvido = match_usuario(nome)
    assert resolvido is not None, f"match_usuario('{nome}') retornou None"
    assert "Bianca" in resolvido, f"Esperado Bianca, got {resolvido}"

def test_33_parse_arg_nome_parcial_a_fazer():
    from card_status import _parse_arg
    from card_helpers import match_usuario
    nome, status = _parse_arg("Guilherme a fazer")
    assert status == "A fazer"
    resolvido = match_usuario(nome)
    assert resolvido is not None
    assert "Guilherme" in resolvido

def test_34_parse_arg_nome_parcial_bloqueado():
    from card_status import _parse_arg
    from card_helpers import match_usuario
    nome, status = _parse_arg("Isab bloqueado")
    assert status == "Bloqueado"
    resolvido = match_usuario(nome)
    assert resolvido is not None
    assert "Isabela" in resolvido

def test_35_revisor_igual_responsavel_nao_aceito():
    """Testa que revisor == responsável é bloqueado no fluxo."""
    import asyncio
    import sys
    sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__file__), '..'))
    from card_constants import get_session, clear_session, _sessions
    from unittest.mock import AsyncMock

    async def run():
        from card_flow import cmd_criar_card, handle_message_criar_card
        msgs = []
        async def say(**kw): msgs.append(kw.get("text", ""))
        ack = AsyncMock()
        clear_session("U_REV")
        await cmd_criar_card(ack, {"user_id": "U_REV", "channel_id": "C001", "thread_ts": None}, say)
        # Preenche até revisor
        for t in ["Source OF", "ADS", "Campanha teste", "Bianca"]:
            await handle_message_criar_card("U_REV", t, say, "C001")
        # Tenta colocar Bianca como revisor também
        await handle_message_criar_card("U_REV", "Bianca", say, "C001")
        step = _sessions.get("U_REV", {}).get("step", "")
        # Deve manter step=revisor (não avançar)
        assert step == "revisor", f"step={step} — deveria ser 'revisor'"
        assert any("revisor" in m.lower() or "diferente" in m.lower() or "já é" in m.lower()
                   for m in msgs), f"Aviso não encontrado nas msgs: {msgs[-3:]}"

    asyncio.run(run())

