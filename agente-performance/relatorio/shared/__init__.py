"""
relatorio/shared/__init__.py

Re-exporta helpers do builder_shared para compatibilidade.
"""

from relatorio.shared.constants import (
    DB_PATH, BASE_DIR, _SQLITE_TIMEOUT, CLIENTES, _MESES_PT, _MESES_PT_ABR, _com_timeout
)
from relatorio.shared.formatters import _fmt_brl, _fmt_x, _fmt_pct, _fmt_n, _fmt_data_pt
from relatorio.shared.blocos import _blocos_resumo, _blocos_tabela_campanhas, _blocos_top_campanhas, _blocos_alocacao, _blocos_alertas, _blocos_comparativo, _blocos_funil, _blocos_placeholder, _mk_table_notion, _blocos_alocacao_canais, _blocos_alocacao_por_anuncio, _blocos_metricas_site_semanal
from relatorio.shared.analytics import _resumo_geral, _alertas, _buscar_campanhas, _com_timeout, _buscar_dados_diarios, _blocos_tendencia_mensal
from relatorio.shared.mensal import _blocos_pedidos_sku, _blocos_pedidos_estado, _blocos_recompradoras, _blocos_visao_mensal_diaria, _blocos_visao_mensal_narrativa, _blocos_metas_mes, _blocos_proximos_passos, _blocos_analise_ultima_semana
