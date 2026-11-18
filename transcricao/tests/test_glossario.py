"""Nomes de clientes deturpados pelo ASR do Meet saem corrigidos na ingestão (sprint de confiabilidade, nov/2026)."""
import sqlite3

from glossario import corrigir_banco, normalizar
from sync_reunioes import parsear_reuniao


def test_variantes_reais_das_reunioes():
    for errado in ("Sorso Soft", "Sorosof", "SourceSoft", "Sorsof", "Sorsoft", "SourceOf"):
        assert normalizar(f"vídeos da {errado} prontos") == "vídeos da Source Of prontos"
    assert normalizar("Reunião FEC amanhã, fechamento do mês") == "Reunião FEQ amanhã, fechamento do mês"
    assert normalizar("criativos da Pod; ele pode subir") == "criativos da POD; ele pode subir"


def test_nao_mexe_em_email_nem_expressao_comum():
    t = "pedro@guaramedia.com.br definiu a single source of truth"
    assert normalizar(t) == t


def test_parser_entrega_texto_corrigido():
    d = parsear_reuniao("Resumo\nAjuste nos criativos da Sorso Soft e da FEC.\n", "Reunião Sorosof - 2026/11/12.docx")
    assert "Sorso" not in str(d) and "FEC" not in str(d)


def test_corrige_banco_existente():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE reunioes (id INTEGER PRIMARY KEY, titulo, resumo, decisoes, proximas_etapas, detalhes)")
    c.execute("CREATE TABLE analise_reunioes (id INTEGER PRIMARY KEY, titulo, alertas, clientes_mencionados, resumo_analise)")
    c.execute("INSERT INTO reunioes VALUES (1, '3P SO', 'Vídeos da Sorosof', '', NULL, 'ok')")
    c.execute("""INSERT INTO analise_reunioes VALUES (1, 't', '[]', '["Sorso Soft", "FEC"]', 'x')""")
    assert corrigir_banco(c, aplicar=False) == 2
    assert corrigir_banco(c, aplicar=True) == 2 and corrigir_banco(c, aplicar=False) == 0
    assert c.execute("SELECT clientes_mencionados FROM analise_reunioes").fetchone()[0] == '["Source Of", "FEQ"]'
