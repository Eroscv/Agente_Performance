"""
relatorio/builder_shared.py

⚠️  ARQUIVO LEGADO — mantido para compatibilidade de imports existentes.

A implementação foi dividida em relatorio/shared/:
    constants.py   — CLIENTES, DB_PATH, _SQLITE_TIMEOUT, _MESES_PT, _com_timeout
    formatters.py  — _fmt_brl, _fmt_x, _fmt_pct, _fmt_n, _fmt_data_pt
    blocos.py      — builders de blocos Notion (resumo, tabela, alertas, funil…)
    analytics.py   — resumo_geral, alertas, tendências, busca de dados
    mensal.py      — blocos específicos do relatório mensal (SKUs, estados, narrativa…)

Para novos imports, prefira o submódulo:
    from relatorio.shared.blocos import _blocos_resumo
    from relatorio.shared.constants import CLIENTES, DB_PATH

Imports deste arquivo continuam funcionando:
    from relatorio.builder_shared import CLIENTES, _blocos_resumo  # ainda funciona
"""
# Constantes (privadas precisam de export explícito — não são incluídas no *)
from relatorio.shared.constants import (
    DB_PATH, BASE_DIR, _SQLITE_TIMEOUT, CLIENTES,
    _MESES_PT, _MESES_PT_ABR, _com_timeout,
)

# Formatadores
from relatorio.shared.formatters import _fmt_brl, _fmt_x, _fmt_pct, _fmt_n, _fmt_data_pt

# Blocos Notion
from relatorio.shared.blocos import (
    _blocos_resumo, _blocos_tabela_campanhas, _blocos_top_campanhas,
    _blocos_alocacao, _blocos_alertas, _blocos_comparativo,
    _blocos_funil, _blocos_placeholder, _mk_table_notion,
    _blocos_alocacao_canais, _blocos_alocacao_por_anuncio,
    _blocos_metricas_site_semanal,
)

# Analytics
from relatorio.shared.analytics import (
    _resumo_geral, _alertas, _buscar_campanhas,
    _buscar_dados_diarios, _blocos_tendencia_mensal,
)

# Mensal
from relatorio.shared.mensal import (
    _blocos_pedidos_sku, _blocos_pedidos_estado, _blocos_recompradoras,
    _blocos_visao_mensal_diaria, _blocos_visao_mensal_narrativa,
    _blocos_metas_mes, _blocos_proximos_passos, _blocos_analise_ultima_semana,
)

# Dados históricos hardcoded (Source OF) — movidos para clientes/source_of/dados_historicos.py
# Re-exportados aqui por compatibilidade
from clientes.source_of.dados_historicos import (
    _DADOS_HISTORICO, _META_Q3, _RECOMPRAS_HISTORICO, _RECOMPRAS_ESTADOS,
)
