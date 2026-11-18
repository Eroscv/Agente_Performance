"""KB em SQLite: ciclo salvar → 👍 → contexto, sem rede."""
import importlib


def test_ciclo_completo(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_DB_PATH", str(tmp_path / "kb.db"))
    import notion.knowledge_base as kb
    kb = importlib.reload(kb)

    a = kb.salvar_entrada("ROAS da Black Friday?", "ROAS 4,2x", "roas", campanha="BF 2026", slack_msg_ts="111.1")
    b = kb.salvar_entrada("Gasto ontem?", "R$ 1.200", "investimento", slack_msg_ts="222.2")
    assert a and b and kb.buscar_contexto() == []                 # nada aprovado ainda

    assert kb.buscar_por_slack_ts("111.1") == a
    assert kb.aprovar_entrada(a, "Vitor") and kb.aprovar_entrada(a, "Eros")   # dois 👍
    assert not kb.aprovar_entrada("999")

    ctx = kb.buscar_contexto(intent="roas", campanha="bf 2026")
    assert [e["page_id"] for e in ctx] == [a] and ctx[0]["frequencia"] == 3
    assert kb.buscar_contexto(intent="roas", campanha="Natal") == []
    assert kb.buscar_contexto(intent="investimento") == []
    assert "ROAS 4,2x" in kb.formatar_contexto_para_prompt(ctx)
    assert [e["aprovado"] for e in kb.listar_entradas()] == [True, False]
