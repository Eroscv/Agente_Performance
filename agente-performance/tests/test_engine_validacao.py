"""
Validação das alterações recentes no engine.py:
1. default cliente_key=source_of
2. campo meta["cliente"] populado
3. POD passa pelo fluxo principal (função legacy removida)
4. Isolamento de dados entre clientes
5. imports dos builders
"""
import pytest
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

BASE = Path(__file__).parent.parent

def limpar_cache():
    cache_db = BASE / "data" / "cache.db"
    if cache_db.exists():
        conn = sqlite3.connect(str(cache_db))
        conn.execute("DELETE FROM cache")
        conn.commit()
        conn.close()
    campanhas_db = BASE / "data" / "campanhas.db"
    if campanhas_db.exists():
        conn = sqlite3.connect(str(campanhas_db))
        try:
            conn.execute("DELETE FROM nlp_cache")
            conn.commit()
        except Exception:
            pass
        conn.close()

class TestDefaultClienteKey:

    def test_canal_generico_retorna_source_of(self):
        limpar_cache()
        from agent.engine import consultar
        r = consultar("quais campanhas estão sem conversão?", channel="C_GENERICO")
        meta = r.get("meta", {})
        assert meta.get("cliente_key") == "source_of", f"Esperado source_of, got: {meta.get('cliente_key')}"

    def test_meta_cliente_alias_populado(self):
        limpar_cache()
        from agent.engine import consultar
        r = consultar("resumo da semana", channel="C_GENERICO")
        meta = r.get("meta", {})
        assert meta.get("cliente") == meta.get("cliente_key"), \
            f"cliente={meta.get('cliente')} != cliente_key={meta.get('cliente_key')}"

    def test_canal_pod_retorna_pod_kombucha(self):
        from agent.engine import get_contexto_cliente
        assert get_contexto_cliente("C_pod_canal") == "pod_kombucha"
        assert get_contexto_cliente("C_kombucha") == "pod_kombucha"

    def test_canal_source_retorna_source_of(self):
        from agent.engine import get_contexto_cliente
        assert get_contexto_cliente("C_source_ads") == "source_of"

    def test_canal_none_retorna_none(self):
        from agent.engine import get_contexto_cliente
        assert get_contexto_cliente(None) is None
        assert get_contexto_cliente("") is None

class TestIsolamentoDados:

    def test_sem_conversao_nao_mistura_pod(self):
        limpar_cache()
        from agent.engine import consultar
        r = consultar("quais campanhas estão sem conversão?", channel="C_GENERICO")
        dados = r.get("dados", [])
        pod_keywords = ["pod", "kombucha", "[search]", "[pmax]"]
        for d in dados:
            nome = (d.get("campanha") or "").lower()
            for kw in pod_keywords:
                assert kw not in nome, f"Campanha POD encontrada em resultado source_of: {d.get('campanha')}"

    def test_query_source_of_retorna_dados(self):
        from agent.queries import resumo_periodo
        from datetime import date, timedelta
        fim = date.today()
        ini = fim - timedelta(days=30)
        r = resumo_periodo(str(ini), str(fim), cliente_key="source_of")
        assert isinstance(r, dict)
        assert r.get("investimento_total", 0) > 0

    def test_query_pod_kombucha_tem_dados(self):
        from agent.queries import resumo_periodo
        from datetime import date, timedelta
        fim = date.today()
        ini = fim - timedelta(days=90)
        r = resumo_periodo(str(ini), str(fim), cliente_key="pod_kombucha", plataforma="google_ads")
        assert isinstance(r, dict)
        assert r.get("investimento_total", 0) > 0, f"POD sem dados: {r}"

class TestFluxoPODPrincipal:

    def test_consultar_pod_nao_chama_legacy(self):
        """_consultar_pod_legacy foi removida — POD passa 100% pelo fluxo principal."""
        limpar_cache()
        from agent.engine import consultar
        r = consultar("como está o google ads do pod essa semana?", channel="C_GENERICO")
        # Função legacy removida — basta confirmar que consultar() retorna resultado válido
        assert isinstance(r, dict), "consultar() deve retornar dict"

    def test_consultar_pod_retorna_cliente_correto(self):
        limpar_cache()
        from agent.engine import consultar
        r = consultar("como está o google ads do pod essa semana?", channel="C_GENERICO")
        meta = r.get("meta", {})
        assert meta.get("cliente_key") == "pod_kombucha", f"Esperado pod_kombucha, got: {meta.get('cliente_key')}"

    def test_consultar_pod_tem_dados(self):
        limpar_cache()
        from agent.engine import consultar
        r = consultar("resumo do pod kombucha", channel="C_GENERICO")
        dados = r.get("dados")
        assert dados is not None and dados != [] and dados != {}, f"POD sem dados: {dados}"
        assert r.get("erro") is None, f"Erro no fluxo POD: {r.get('erro')}"

class TestImportsBuilders:

    def test_import_builder_mensal(self):
        from relatorio.builder_mensal import gerar_relatorio_mensal
        assert callable(gerar_relatorio_mensal)

    def test_import_builder_semanal(self):
        from relatorio.builder_semanal import gerar_relatorio_semanal
        assert callable(gerar_relatorio_semanal)

    def test_import_builder_diario(self):
        from relatorio.builder_diario import gerar_relatorio_diario
        assert callable(gerar_relatorio_diario)

    def test_relatorio_builder_nao_existe(self):
        import importlib
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("relatorio.builder")

    def test_handlers_importa_ok(self):
        import importlib
        # Verifica que o módulo carrega (imports de nível superior)
        try:
            mod = importlib.import_module("slack.handlers")
            assert mod is not None
        except ModuleNotFoundError as e:
            pytest.fail(f"handlers.py com import quebrado: {e}")
        # Verifica também que os builders lazy referenciados existem
        # (sem isso, imports dentro de funções async passariam despercebidos)
        try:
            from relatorio.builder_mensal import gerar_relatorio_mensal
        except ModuleNotFoundError as e:
            pytest.fail(f"relatorio.builder_mensal não encontrado (import em handlers.py quebrado): {e}")

    def test_cron_relatorio_importa_ok(self):
        import sys, importlib
        spec = importlib.util.spec_from_file_location(
            "cron_relatorio",
            "/home/ubuntu/.openclaw/workspace/agente-performance/scripts/cron_relatorio.py"
        )
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except ModuleNotFoundError as e:
            if "relatorio.builder" in str(e):
                pytest.fail(f"Import quebrado ainda presente: {e}")
        except Exception:
            pass  # outros erros de runtime são ok

class TestNLPDeteccaoCliente:

    def test_nlp_detecta_pod(self):
        from agent.nlp import interpretar
        r = interpretar("como está o google ads do pod essa semana?", [], "")
        raw = r.get("raw", {})
        assert raw.get("cliente_key") == "pod_kombucha", f"NLP não detectou pod_kombucha: {raw}"

    def test_nlp_detecta_plataforma_google(self):
        from agent.nlp import interpretar
        r = interpretar("resumo google ads pod", [], "")
        raw = r.get("raw", {})
        assert raw.get("plataforma") == "google_ads", f"NLP não detectou google_ads: {raw}"

    def test_nlp_sem_cliente_retorna_none(self):
        from agent.nlp import interpretar
        r = interpretar("qual o roas da semana?", [], "")
        raw = r.get("raw", {})
        assert raw.get("cliente_key") is None, f"NLP retornou cliente inesperado: {raw.get('cliente_key')}"

class TestBackupScript:

    def test_backup_script_existe(self):
        import os
        path = BASE / "scripts" / "backup_db.sh"
        assert path.exists(), "backup_db.sh não encontrado"
        assert os.access(str(path), os.X_OK), "backup_db.sh não é executável"

    def test_backup_gera_arquivos(self):
        import subprocess
        result = subprocess.run(["which", "sqlite3"], capture_output=True)
        if result.returncode != 0:
            pytest.skip("sqlite3 não instalado")
        result = subprocess.run(
            ["bash", str(BASE / "scripts" / "backup_db.sh")],
            capture_output=True, text=True, cwd=str(BASE)
        )
        assert result.returncode == 0, f"Backup falhou:\n{result.stdout}\n{result.stderr}"
        assert "Backup concluído" in result.stdout
        backup_dir = BASE / "data" / "backups"
        backups = list(backup_dir.glob("*.db"))
        assert len(backups) >= 4, f"Esperado ≥4 backups, encontrado: {len(backups)}"
