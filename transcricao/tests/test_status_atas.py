"""
tests/test_status_atas.py
Testes para slack_status_atas.py — /status-atas, /reprocessar-ata, notificar_falha_slack.
25+ casos.
"""
import sys, os, sqlite3, tempfile, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

# Redireciona DB para temporário
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()

import slack_status_atas as _sts
_sts.DB_PATH = Path(_tmp.name)


def _criar_banco():
    conn = sqlite3.connect(_tmp.name)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fonte_id TEXT NOT NULL,
            nome TEXT NOT NULL,
            status TEXT NOT NULL,
            mensagem TEXT,
            criado_em TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


@pytest.fixture(autouse=True)
def clean_db():
    Path(_tmp.name).unlink(missing_ok=True)
    _criar_banco()
    yield


def _inserir_log(fonte_id, nome, status, mensagem="", criado_em=None):
    conn = sqlite3.connect(_tmp.name)
    ts = criado_em or "datetime('now')"
    if criado_em:
        conn.execute(
            "INSERT INTO sync_log (fonte_id, nome, status, mensagem, criado_em) VALUES (?,?,?,?,?)",
            (fonte_id, nome, status, mensagem, criado_em)
        )
    else:
        conn.execute(
            "INSERT INTO sync_log (fonte_id, nome, status, mensagem) VALUES (?,?,?,?)",
            (fonte_id, nome, status, mensagem)
        )
    conn.commit()
    conn.close()


def _make_command(text="", channel="C001", thread_ts=None):
    return {"channel_id": channel, "text": text, "thread_ts": thread_ts}


# ── _data_brt ─────────────────────────────────────────────────────────────────

def test_01_data_brt_converte():
    resultado = _sts._data_brt("2026-10-28T15:00:00+00:00")
    assert "12:00" in resultado  # UTC-3

def test_02_data_brt_iso_sem_tz():
    resultado = _sts._data_brt("2026-10-28T15:00:00")
    assert resultado  # não explode

def test_03_data_brt_invalida_retorna_truncado():
    resultado = _sts._data_brt("invalido")
    assert len(resultado) <= 20

# ── _status_icon ──────────────────────────────────────────────────────────────

def test_04_status_icon_ok():
    assert _sts._status_icon("ok") == "✅"

def test_05_status_icon_erro():
    assert _sts._status_icon("erro") == "❌"

def test_06_status_icon_skip():
    assert _sts._status_icon("skip") == "⏭️"

def test_07_status_icon_desconhecido():
    assert _sts._status_icon("outro") == "❓"

# ── cmd_status_atas ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_08_status_atas_sem_registros():
    ack  = AsyncMock()
    say  = AsyncMock()
    await _sts.cmd_status_atas(ack, _make_command(), say)
    ack.assert_awaited_once()
    say.assert_awaited_once()
    assert "Nenhuma ata" in say.call_args[1]["text"] or "Nenhuma ata" in say.call_args[0][0]

@pytest.mark.asyncio
async def test_09_status_atas_com_registros():
    _inserir_log("ID1", "Source OF 2026-10-28.docx", "ok", "Salvo: Reunião Semanal")
    _inserir_log("ID2", "POD 2026-10-27.docx", "erro", "Timeout na API")
    ack  = AsyncMock()
    say  = AsyncMock()
    await _sts.cmd_status_atas(ack, _make_command(), say)
    texto = say.call_args[1]["text"]
    assert "✅" in texto
    assert "❌" in texto

@pytest.mark.asyncio
async def test_10_status_atas_limite_default():
    for i in range(15):
        _inserir_log(f"ID{i}", f"Ata_{i}.docx", "ok", f"Salvo: Titulo {i}")
    ack  = AsyncMock()
    say  = AsyncMock()
    await _sts.cmd_status_atas(ack, _make_command(""), say)
    texto = say.call_args[1]["text"]
    # Header menciona 10 (limite padrão)
    assert "10" in texto

@pytest.mark.asyncio
async def test_11_status_atas_limite_customizado():
    for i in range(5):
        _inserir_log(f"ID_c{i}", f"Ata_c{i}.docx", "ok")
    ack  = AsyncMock()
    say  = AsyncMock()
    await _sts.cmd_status_atas(ack, _make_command("3"), say)
    texto = say.call_args[1]["text"]
    assert "3" in texto

@pytest.mark.asyncio
async def test_12_status_atas_mostra_metricas_do_dia():
    _inserir_log("IDa", "ata_a.docx", "ok")
    _inserir_log("IDb", "ata_b.docx", "ok")
    _inserir_log("IDc", "ata_c.docx", "erro", "falhou")
    ack  = AsyncMock()
    say  = AsyncMock()
    await _sts.cmd_status_atas(ack, _make_command(), say)
    texto = say.call_args[1]["text"]
    assert "2" in texto  # 2 ok
    assert "1" in texto  # 1 erro

@pytest.mark.asyncio
async def test_13_status_atas_mostra_hint_reprocessar():
    _inserir_log("IDz", "ata.docx", "erro", "erro")
    ack  = AsyncMock()
    say  = AsyncMock()
    await _sts.cmd_status_atas(ack, _make_command(), say)
    texto = say.call_args[1]["text"]
    assert "reprocessar" in texto.lower()

# ── cmd_reprocessar_ata ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_14_reprocessar_sem_arg():
    ack  = AsyncMock()
    say  = AsyncMock()
    await _sts.cmd_reprocessar_ata(ack, _make_command(""), say)
    texto = say.call_args[1]["text"]
    assert "informe" in texto.lower() or "nome" in texto.lower()

@pytest.mark.asyncio
async def test_15_reprocessar_nao_encontrado():
    ack  = AsyncMock()
    say  = AsyncMock()
    await _sts.cmd_reprocessar_ata(ack, _make_command("nome_inexistente_xyz"), say)
    texto = say.call_args[1]["text"]
    assert "não encontrada" in texto.lower() or "nenhuma ata" in texto.lower()

@pytest.mark.asyncio
async def test_16_reprocessar_multiplas_opcoes():
    _inserir_log("ID_R1", "Source OF Outubro.docx", "ok")
    _inserir_log("ID_R2", "Source OF Novembro.docx", "ok")
    ack  = AsyncMock()
    say  = AsyncMock()
    await _sts.cmd_reprocessar_ata(ack, _make_command("Source OF"), say)
    texto = say.call_args[1]["text"]
    assert "específico" in texto.lower() or "encontrei" in texto.lower()

@pytest.mark.asyncio
async def test_17_reprocessar_uma_ata_inicia_task():
    _inserir_log("ID_UNIQUE", "Source OF 2026-10-28.docx", "erro", "Timeout")
    ack  = AsyncMock()
    say  = AsyncMock()

    with patch("asyncio.create_task") as mock_task:
        await _sts.cmd_reprocessar_ata(ack, _make_command("2026-10-28"), say)
        # Deve ter tentado criar task OU mostrado mensagem de início
        texto = say.call_args[1]["text"]
        assert "reprocessando" in texto.lower() or mock_task.called

# ── notificar_falha_slack ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_18_notificar_sem_channel():
    _sts.SLACK_ATAS_CHANNEL = ""
    slack = AsyncMock()
    await _sts.notificar_falha_slack(slack, "ata.docx", "Timeout erro")
    slack.chat_postMessage.assert_not_called()

@pytest.mark.asyncio
async def test_19_notificar_com_channel():
    _sts.SLACK_ATAS_CHANNEL = "C_TEST"
    slack = AsyncMock()
    await _sts.notificar_falha_slack(slack, "ata.docx", "Timeout erro")
    slack.chat_postMessage.assert_awaited_once()
    kwargs = slack.chat_postMessage.call_args[1]
    assert kwargs["channel"] == "C_TEST"
    assert "ata.docx" in kwargs["text"]
    assert "Timeout erro" in kwargs["text"]

@pytest.mark.asyncio
async def test_20_notificar_channel_override():
    _sts.SLACK_ATAS_CHANNEL = "C_DEFAULT"
    slack = AsyncMock()
    await _sts.notificar_falha_slack(slack, "ata.docx", "Erro", channel="C_OVERRIDE")
    kwargs = slack.chat_postMessage.call_args[1]
    assert kwargs["channel"] == "C_OVERRIDE"

@pytest.mark.asyncio
async def test_21_notificar_erro_no_slack_nao_explode():
    _sts.SLACK_ATAS_CHANNEL = "C_FAIL"
    slack = MagicMock()
    slack.chat_postMessage = AsyncMock(side_effect=Exception("Slack down"))
    # Não deve levantar exceção
    await _sts.notificar_falha_slack(slack, "ata.docx", "Erro")

@pytest.mark.asyncio
async def test_22_notificar_erro_longo_truncado():
    _sts.SLACK_ATAS_CHANNEL = "C_TEST2"
    slack = AsyncMock()
    erro_longo = "E" * 1000
    await _sts.notificar_falha_slack(slack, "ata.docx", erro_longo)
    kwargs = slack.chat_postMessage.call_args[1]
    assert len(kwargs["text"]) < 2000  # Slack tem limite de 4000, mas nossa msg deve ser razoável

@pytest.mark.asyncio
async def test_23_notificar_inclui_hint_reprocessar():
    _sts.SLACK_ATAS_CHANNEL = "C_HINT"
    slack = AsyncMock()
    await _sts.notificar_falha_slack(slack, "Source OF 2026-10.docx", "Erro X")
    texto = slack.chat_postMessage.call_args[1]["text"]
    assert "reprocessar" in texto.lower()

@pytest.mark.asyncio
async def test_24_status_atas_nome_longo_truncado():
    nome_longo = "A" * 100 + ".docx"
    _inserir_log("ID_LONG", nome_longo, "ok", "Salvo")
    ack  = AsyncMock()
    say  = AsyncMock()
    await _sts.cmd_status_atas(ack, _make_command(), say)
    texto = say.call_args[1]["text"]
    # Nome deve estar truncado
    assert "…" in texto or len([l for l in texto.split("\n") if "A" * 100 in l]) == 0

@pytest.mark.asyncio
async def test_25_status_atas_ack_sempre_chamado():
    ack  = AsyncMock()
    say  = AsyncMock()
    await _sts.cmd_status_atas(ack, _make_command(), say)
    ack.assert_awaited_once()

@pytest.mark.asyncio
async def test_26_reprocessar_ack_sempre_chamado():
    ack  = AsyncMock()
    say  = AsyncMock()
    await _sts.cmd_reprocessar_ata(ack, _make_command(""), say)
    ack.assert_awaited_once()
