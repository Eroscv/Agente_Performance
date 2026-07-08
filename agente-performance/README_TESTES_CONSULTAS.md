# Testes de Consultas — Agente de Performance Guará

Cobertura completa de integração para todas as funções de consulta do agente.

**Resultado:** ✅ 88 testes | 17 classes | 0 falhas

---

## Como Rodar

```bash
cd agente-performance
source venv/bin/activate

# Todos os testes de consulta
python -m pytest tests/test_consultas.py -v

# Todos os testes do projeto
python -m pytest tests/ -v

# Com relatório de cobertura
python -m pytest tests/test_consultas.py --tb=short -q
```

---

## Banco de Dados de Referência

| Item | Valor |
|---|---|
| Arquivo | `data/campanhas.db` |
| Total de linhas | ~28.256 |
| Campanhas únicas | 32 |
| Período disponível | 2024-01-23 → 2026-06-23 |
| Período de referência nos testes | 2026-06-01 → 2026-06-23 |

---

## Cobertura por Classe de Teste

### 1. `TestListarCampanhas` — `listar_campanhas()`
Verifica listagem de todas as campanhas únicas.

| Teste | O que valida |
|---|---|
| `test_retorna_lista` | Retorna list não vazia |
| `test_tem_campos_esperados` | Campos: `campanha`, `primeira_data`, `ultima_data`, `dias_com_dados`, `investimento_total` |
| `test_ordenado_por_investimento` | Ordem decrescente por investimento total |
| `test_sem_campanha_vazia` | Nenhum nome de campanha vazio ou nulo |
| `test_min_32_campanhas` | Banco tem >= 32 campanhas |

---

### 2. `TestResumoPorCampanha` — `resumo_por_campanha(data_inicio?, data_fim?)`
Resumo agregado por campanha com todas as métricas principais.

| Teste | O que valida |
|---|---|
| `test_retorna_dados` | Retorna dados para o período de junho 2026 |
| `test_tem_metricas_essenciais` | Campos: `roas`, `ctr_medio`, `cpc_medio`, `cpa_ajustado`, `frequencia_media` |
| `test_investimento_positivo` | Investimento >= 0 para todas as campanhas |
| `test_sem_filtro_de_data` | Funciona sem filtro de data |
| `test_roas_positivo_onde_ha_receita` | ROAS > 0 quando receita > 0 e ROAS não é null |

---

### 3. `TestPerformanceCampanha` — `performance_campanha(campanha, data_inicio?, data_fim?)`
Série temporal diária de uma campanha específica.

| Teste | O que valida |
|---|---|
| `test_match_exato` | Encontra campanha por nome exato |
| `test_match_like_fragmento` | Fallback LIKE funciona com fragmento ("hidratante") |
| `test_campos_serie_temporal` | Campos: `data`, `campanha`, `roas`, `impressoes`, `ctr`, `cpc` |
| `test_ordenado_por_data` | Série ordenada por data ASC |
| `test_campanha_inexistente_retorna_lista` | Retorna lista vazia sem exceção |
| `test_cobertura_periodo` | Junho 2026 retorna exatamente 23 dias |

---

### 4. `TestTopCampanhasROAS` — `top_campanhas_roas(limit?, data_inicio?, data_fim?, excluir_awareness?)`
Ranking de campanhas por ROAS ponderado.

| Teste | O que valida |
|---|---|
| `test_retorna_lista` | Retorna list |
| `test_roas_decrescente` | Ordenação decrescente por ROAS |
| `test_exclui_awareness_por_padrao` | Campanhas de awareness/tráfego não aparecem por padrão |
| `test_inclui_awareness_quando_solicitado` | `excluir_awareness=False` amplia o resultado |
| `test_investimento_minimo_100` | Todas as campanhas no ranking têm investimento >= R$100 |
| `test_campos_esperados` | Campos: `campanha`, `roas`, `investimento`, `receita`, `compras` |

---

### 5. `TestTopCampanhasCompras` — `top_campanhas_compras(limit?, data_inicio?, data_fim?)`
Ranking de campanhas por volume de compras.

| Teste | O que valida |
|---|---|
| `test_retorna_lista` | Retorna list não vazia |
| `test_ordenado_por_compras` | Ordenação decrescente por número de compras |
| `test_limit_respeitado` | Retorna no máximo `limit` campanhas |
| `test_tem_cpa_ajustado` | CPA ajustado > 0 quando há compras e investimento > 0 |
| `test_sem_filtro_de_data` | Funciona sem filtro de data |

---

### 6. `TestEvolucaoDiaria` — `evolucao_diaria(campanha?, data_inicio?, data_fim?)`
Evolução diária de métricas (geral ou por campanha).

| Teste | O que valida |
|---|---|
| `test_geral_retorna_serie` | Retorna série para todas as campanhas |
| `test_geral_ordenado_por_data` | Série geral ordenada ASC |
| `test_por_campanha_retorna_serie` | Retorna série para campanha específica |
| `test_por_campanha_cobre_periodo` | Junho 2026 = 23 dias |
| `test_campos_serie` | Campos: `data`, `investimento`, `receita`, `compras`, `roas`, `impressoes`, `cliques`, `ctr_medio` |
| `test_investimento_diario_positivo` | Investimento diário >= 0 |

---

### 7. `TestResumoPeriodo` — `resumo_periodo(data_inicio, data_fim)`
Totais consolidados de um período.

| Teste | O que valida |
|---|---|
| `test_retorna_dict` | Retorna dict não vazio |
| `test_sem_aviso_para_periodo_valido` | Sem chave `aviso` para período com dados |
| `test_metricas_completas` | 15 métricas obrigatórias presentes |
| `test_valores_positivos` | `investimento_total`, `receita_total`, `roas`, `ctr_medio`, `cpc_medio` > 0 |
| `test_roas_junho_2026` | ROAS de junho 2026 > 1x (campanhas rentáveis) |
| `test_periodo_sem_dados_retorna_aviso` | Período inválido retorna `{"aviso": ...}` |
| `test_funil_consistente` | Impressões ≥ Cliques ≥ Carrinho ≥ Checkout ≥ Compras |

---

### 8. `TestAlertasROASBaixo` — `alertas_roas_baixo(threshold?)`
Campanhas com ROAS abaixo do threshold nos últimos 30 dias.

| Teste | O que valida |
|---|---|
| `test_retorna_lista` | Retorna list |
| `test_todos_abaixo_do_threshold` | Campanhas com `tipo_alerta=roas_baixo` estão de fato abaixo |
| `test_sem_conversao_aparece_como_alerta` | Campanhas sem conversão são sinalizadas |
| `test_investimento_minimo_100` | Alertas só para campanhas com invest >= R$100 |
| `test_threshold_zero_retorna_vazio` | Threshold 0 não gera erros |
| `test_campos_alerta` | Campos: `campanha`, `roas`, `investimento`, `receita`, `tipo_alerta` |

---

### 9. `TestCompararCampanhas` — `comparar_campanhas(campanhas, data_inicio?, data_fim?)`
Comparação lado a lado de campanhas específicas.

| Teste | O que valida |
|---|---|
| `test_duas_campanhas` | Retorna exatamente 2 campanhas quando pedido |
| `test_campanha_unica` | Funciona com lista de 1 campanha |
| `test_lista_vazia_retorna_vazia` | `[]` retorna `[]` sem erros |
| `test_campos_comparacao` | Campos: `campanha`, `roas`, `cpc_medio`, `cpa_ajustado`, `frequencia_media` |
| `test_ordenado_por_investimento` | Ordem decrescente por investimento |

---

### 10. `TestMetricasFunil` — `metricas_funil(campanha?, data_inicio?, data_fim?)`
Métricas de funil: impressões → cliques → carrinho → checkout → compra.

| Teste | O que valida |
|---|---|
| `test_funil_geral` | Retorna dict para todas as campanhas |
| `test_funil_por_campanha` | Retorna dict para campanha específica |
| `test_campos_funil` | Campos: `impressoes`, `cliques`, `adicoes_carrinho`, `checkout_iniciado`, `compras`, `receita` |
| `test_hierarquia_funil` | Impressões ≥ Cliques ≥ Carrinho ≥ Checkout ≥ Compras |
| `test_impressoes_junho_acima_1m` | Junho 2026 > 1 milhão de impressões |

---

### 11. `TestCompararPeriodos` — `comparar_periodos(a_inicio, a_fim, b_inicio, b_fim)`
Comparação de dois períodos com variação percentual.

| Teste | O que valida |
|---|---|
| `test_retorna_estrutura` | Chaves: `periodo_a`, `periodo_b`, `variacao` |
| `test_periodo_a_tem_metricas` | `periodo_a` tem `investimento` > 0 para maio 2026 |
| `test_variacao_calculada` | `variacao` contém métricas calculadas |
| `test_periodos_diferentes_dao_variacao` | Funciona com períodos históricos distintos |

---

### 12. `TestCompararCampanhasDetalhado` — `comparar_campanhas_detalhado(campanhas, data_inicio?, data_fim?)`
Comparação detalhada com vencedores por métrica.

| Teste | O que valida |
|---|---|
| `test_retorna_dict` | Retorna dict com `campanhas`, `vencedores`, `periodo` |
| `test_inclui_campanhas_solicitadas` | As campanhas pedidas aparecem em `r["campanhas"]` |
| `test_sem_filtro_de_data` | Funciona sem filtro de data |

---

### 13. `TestCompararSemanaASemana` — `comparar_semana_a_semana(campanha?, semanas?)`
Evolução WoW (week-over-week).

| Teste | O que valida |
|---|---|
| `test_retorna_lista` | Retorna list |
| `test_tem_n_semanas` | Retorna <= N semanas solicitadas |
| `test_campos_wow` | Campos: `semana`, `data_inicio`, `data_fim`, `investimento`, `roas` |
| `test_por_campanha` | Funciona com campanha específica |
| `test_ordenado_cronologicamente` | Ordenação ASC por data |

---

### 14. `TestCompararMesAMes` — `comparar_mes_a_mes(campanha?, meses?)`
Evolução MoM (month-over-month).

| Teste | O que valida |
|---|---|
| `test_retorna_lista` | Retorna list |
| `test_tem_n_meses` | Retorna <= N meses solicitados |
| `test_campos_mom` | Campos: `investimento`, `roas`, `compras` |
| `test_por_campanha` | Funciona com campanha específica |

---

### 15. `TestCompararAntesDepois` — `comparar_antes_depois(data_virada, campanha?)`
Comparação antes e depois de uma data de virada.

| Teste | O que valida |
|---|---|
| `test_retorna_estrutura` | Chaves: `data_virada`, `antes`, `depois`, `variacao` |
| `test_data_virada_registrada` | `data_virada` preservada no resultado |
| `test_antes_tem_metricas` | `antes` tem dados de investimento |
| `test_por_campanha` | Funciona para campanha específica |
| `test_variacao_percentual` | `variacao` é dict com métricas |

---

### 16. `TestRankingComparativo` — `ranking_comparativo(metrica, data_inicio?, data_fim?)`
Ranking de todas as campanhas por qualquer métrica.

| Teste | O que valida |
|---|---|
| `test_ranking_roas` | Retorna ranking por ROAS |
| `test_ordenado_por_metrica_roas` | Ordenação decrescente por ROAS |
| `test_sem_filtro_de_data` | Funciona sem filtro de data |
| `test_retorna_todas_campanhas` | Retorna >= 1 campanha |
| `test_campos_ranking` | Campo `campanha` presente |

---

### 17. `TestDataRangeDisponivel` — `data_range_disponivel()`
Diagnóstico do banco — range disponível para consultas.

| Teste | O que valida |
|---|---|
| `test_retorna_dict` | Retorna dict |
| `test_campos_completos` | Campos: `data_minima`, `data_maxima`, `total_linhas`, `total_campanhas` |
| `test_data_minima_antes_de_maxima` | `data_minima` < `data_maxima` |
| `test_banco_tem_28k_linhas` | >= 28.000 linhas no banco |
| `test_banco_tem_32_campanhas` | >= 32 campanhas distintas |
| `test_data_minima_formato_iso` | Datas em formato `YYYY-MM-DD` |

---

## Resultado Final

```
========================= 88 passed in 3.33s =========================
```

| Classe | Testes | Status |
|---|---|---|
| TestListarCampanhas | 5 | ✅ |
| TestResumoPorCampanha | 5 | ✅ |
| TestPerformanceCampanha | 6 | ✅ |
| TestTopCampanhasROAS | 6 | ✅ |
| TestTopCampanhasCompras | 5 | ✅ |
| TestEvolucaoDiaria | 6 | ✅ |
| TestResumoPeriodo | 7 | ✅ |
| TestAlertasROASBaixo | 6 | ✅ |
| TestCompararCampanhas | 5 | ✅ |
| TestMetricasFunil | 5 | ✅ |
| TestCompararPeriodos | 4 | ✅ |
| TestCompararCampanhasDetalhado | 3 | ✅ |
| TestCompararSemanaASemana | 5 | ✅ |
| TestCompararMesAMes | 4 | ✅ |
| TestCompararAntesDepois | 5 | ✅ |
| TestRankingComparativo | 5 | ✅ |
| TestDataRangeDisponivel | 6 | ✅ |
| **TOTAL** | **88** | **✅** |
