# BigQuery — Status de Uso

**Projeto:** `guara-db`  
**Dataset:** `guara_performance`  
**Atualizado em:** 07/10/2026 (12:25 BRT)

---

## Tabelas e Tamanhos

| Tabela | Linhas | Tamanho |
|--------|-------:|--------:|
| `ads_diario` | 706.798 | 259,01 MB |
| `shopify_diario` | 1.043 | 0,11 MB |
| `analise_reunioes` | 27 | 0,02 MB |
| `metricas_sla` | 60 | 0,01 MB |
| `tickets_alerta` | 29 | 0,01 MB |
| `dooki_pedidos_diario` | 3 | ~0 MB |
| `v_ads_roas_real` *(view)* | — | 0 MB |
| `v_alertas_campanha` *(view)* | — | 0 MB |
| `v_analise_reunioes` *(view)* | — | 0 MB |
| `v_benchmark_mensal` *(view)* | — | 0 MB |
| `v_diario_90d` *(view)* | — | 0 MB |
| `v_mensal` *(view)* | — | 0 MB |
| `v_performance_completa` *(view)* | — | 0 MB |
| `v_performance_mensal` *(view)* | — | 0 MB |
| `v_tickets_alerta` *(view)* | — | 0 MB |
| `v_top_campanhas_mes` *(view)* | — | 0 MB |

**Total: 16 tabelas (6 com dados + 10 views)**

---

## Uso vs Free Tier

| Recurso | Usado | Limite Gratuito | % Utilizado |
|---------|------:|----------------:|------------:|
| Storage | 0,25 GB (259 MB) | 10 GB/mês | **2,5%** ✅ |
| Queries | < 1 GB/mês (est.) | 1 TiB/mês | **< 0,1%** ✅ |

**Custo atual: R$ 0,00** — bem dentro do tier gratuito.

---

## Limites do Free Tier (referência)

### Storage
- **10 GB/mês** de armazenamento ativo gratuito
- Acima disso: ~US$ 0,02/GB/mês (ativo) | ~US$ 0,01/GB/mês (longo prazo, +90 dias sem modificação)

### Queries (processamento)
- **1 TiB/mês** gratuito por conta
- Acima disso: US$ 6,25/TiB

### Operações sempre gratuitas
- Load jobs (carga de dados)
- Export jobs
- Cópia de tabelas
- Metadados e chamadas de API básicas

---

## Observações

- A tabela `ads_diario` representa **99,9% do storage usado** (706k linhas de dados de anúncios Meta/Google).
- No ritmo atual de crescimento, o limite de 10 GB de storage gratuito **não deve ser atingido tão cedo**.
- Monitorar caso a `ads_diario` continue crescendo — considerar particionamento por data para reduzir custo de queries futuras.
