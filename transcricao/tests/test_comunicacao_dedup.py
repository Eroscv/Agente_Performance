"""
tests/test_comunicacao_dedup.py

Testa o fix de dedup do ComunicacaoAgent: tarefas_ja_criadas() era
chamada em lugar nenhum (achado documentado no commit da estruturação
em subagentes), e mesmo que fosse, a checagem sempre voltaria vazia
porque criar_card_notion() nunca escrevia a propriedade "Origem Reunião"
que o filtro de dedup consulta. Os dois lados foram corrigidos juntos.

Sem chamada real ao Notion: monkeypatch nas funções de
criar_tarefas_notion.py que fariam a requisição HTTP, e um SQLite
temporário no lugar do banco de produção.
"""
import sqlite3
import sys
from datetime import date, timezone, datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import criar_tarefas_notion as impl  # noqa: E402
from agents.comunicacao import ComunicacaoAgent  # noqa: E402


def _preparar_banco(tmp_path, monkeypatch):
    db_path = tmp_path / "reunioes.db"
    monkeypatch.setattr(impl, "DB_PATH", db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE reunioes (
            id INTEGER PRIMARY KEY, data TEXT, titulo TEXT,
            participantes TEXT, proximas_etapas TEXT, fonte_id TEXT
        )
    """)
    conn.execute(
        "INSERT INTO reunioes (data, titulo, participantes, proximas_etapas, fonte_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            date.today().isoformat(),
            "Reunião Semanal POD",
            "Eros",
            "[Eros] Revisar criativos: ver fadiga de frequência\n"
            "[Miguel] Validar fator de imposto: confirmar com Bianca",
            "fonte-abc123",
        ),
    )
    conn.commit()
    conn.close()


def test_pula_tarefa_ja_criada_e_cria_a_nova(tmp_path, monkeypatch):
    _preparar_banco(tmp_path, monkeypatch)

    # "Revisar criativos" já existe no Notion (segundo o dedup fake);
    # "Validar fator de imposto" é nova.
    monkeypatch.setattr(impl, "tarefas_ja_criadas", lambda fonte_id: {"Revisar criativos"})

    criados_payload = []

    def fake_criar_card(**kwargs):
        criados_payload.append(kwargs["tarefa"]["titulo"])
        return True

    monkeypatch.setattr(impl, "criar_card_notion", fake_criar_card)

    resultado = ComunicacaoAgent().executar(dry_run=False)

    assert resultado["criados"] == 1
    assert resultado["duplicados"] == 1
    assert resultado["erros"] == 0
    assert criados_payload == ["Validar fator de imposto"]


def test_falha_na_consulta_de_dedup_nao_trava_o_pipeline(tmp_path, monkeypatch):
    _preparar_banco(tmp_path, monkeypatch)

    def fake_tarefas_ja_criadas(fonte_id):
        raise ConnectionError("Notion fora do ar")

    monkeypatch.setattr(impl, "tarefas_ja_criadas", fake_tarefas_ja_criadas)
    monkeypatch.setattr(impl, "criar_card_notion", lambda **kwargs: True)

    resultado = ComunicacaoAgent().executar(dry_run=False)

    # Dedup falhou (Notion fora do ar) -> segue sem dedup, cria as 2 tarefas.
    assert resultado["criados"] == 2
    assert resultado["duplicados"] == 0
    assert resultado["erros"] == 0


def test_criar_card_notion_grava_origem_reuniao_para_dedup_funcionar(monkeypatch):
    """Sem isso, tarefas_ja_criadas() nunca encontraria nada mesmo sendo
    chamada — era a segunda metade do bug (achado na sessão anterior)."""
    capturado = {}

    class _RespostaFake:
        status_code = 200
        text = ""

        def json(self):
            return {}

    def fake_post(url, headers=None, json=None, timeout=None):
        capturado["payload"] = json
        return _RespostaFake()

    monkeypatch.setattr(impl.requests, "post", fake_post)

    ok = impl.criar_card_notion(
        tarefa={"responsavel_nome": "Eros", "responsavel_id": "uid-1",
                "titulo": "Revisar criativos", "descricao": "desc"},
        reuniao_data=date.today().isoformat(),
        reuniao_titulo="Reunião Semanal POD",
        fonte_id="fonte-abc123",
        clientes=["POD"],
        area="Performance",
        dry_run=False,
    )

    assert ok is True
    origem = capturado["payload"]["properties"]["Origem Reunião"]
    assert origem["rich_text"][0]["text"]["content"] == "fonte-abc123"
