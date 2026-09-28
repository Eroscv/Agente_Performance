# Fase 2 — Arquitetura Multi-Agentes
**Gatilho:** 3+ clientes ativos OU 2+ plataformas novas além de Meta/Google

---

## Por que esperar?

O engine monolítico atual resolve bem para 2 clientes e 2 plataformas.
O custo de refatorar agora (risco, tempo, regressões) supera o ganho.
Fase 2 só vale quando a complexidade do negócio justificar a complexidade do código.

---

## O que muda

### Hoje
```
consultar() → _executar_intent() → queries.py → resultado
```

### Fase 2
```
OrchestratorAgent
    ├── MetaAdsAgent     (especialista Meta — queries, métricas, creative insights)
    ├── GoogleAdsAgent   (especialista Google — PMAX, smart bidding, quality score)
    ├── ReportAgent      (assíncrono — relatórios longos, exports, agendamentos)
    └── SQLAgent         (fallback dinâmico — já existe via sql_generator.py)
```

---

## Agentes planejados

### OrchestratorAgent
- Recebe a pergunta processada (intent + params)
- Decide qual agente(s) ativar
- Para perguntas multi-cliente: dispara em paralelo via `asyncio.gather()`
- Agrega e ordena resultados antes de passar para `gerar_resposta()`

```python
class OrchestratorAgent(BaseAnalysisAgent):
    def __init__(self):
        self.agents = {
            "meta_ads":    MetaAdsAgent(),
            "google_ads":  GoogleAdsAgent(),
            "report":      ReportAgent(),
            "sql":         SQLAgent(),          # já existe
        }

    async def dispatch(self, intent, params, clientes: list[str]) -> dict:
        """Disparo paralelo para múltiplos clientes."""
        tasks = [
            self.agents[params["plataforma"]].execute_async(intent, params, ck)
            for ck in clientes
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return self._merge(results, clientes)
```

---

### MetaAdsAgent(BaseAnalysisAgent)
- Herda `base_agent.py` (já existe)
- Encapsula toda lógica específica do Meta Ads:
  - Creative performance (CTR por formato, video vs imagem)
  - Frequency caps e fadiga de criativo
  - Breakdown por posicionamento (feed, stories, reels)
  - ROAS ajustado Shopify (lógica do delta atual em `engine.py`)

```python
class MetaAdsAgent(BaseAnalysisAgent):
    PLATAFORMA = "meta_ads"

    def execute(self, intent, campanha, data_inicio, data_fim, limit, meta, cliente_key, **kw):
        # Delega para queries especializadas Meta
        ...

    def roas_ajustado(self, cliente_key, data_inicio, data_fim) -> dict:
        """ROAS real via Shopify (hoje embutido no engine.py)."""
        ...
```

---

### GoogleAdsAgent(BaseAnalysisAgent)
- Especialista Google Ads:
  - Performance Max campaigns
  - Smart bidding analysis
  - Quality Score breakdown
  - Search terms (quando disponível)

```python
class GoogleAdsAgent(BaseAnalysisAgent):
    PLATAFORMA = "google_ads"

    def execute(self, intent, campanha, data_inicio, data_fim, limit, meta, cliente_key, **kw):
        ...
```

---

### ReportAgent
- Assíncrono — não bloqueia o Slack handler
- Gera relatórios longos em background e notifica quando pronto
- Casos de uso:
  - `"relatório mensal completo"`
  - `"export de todas as campanhas do trimestre"`
  - Envio agendado (cron semanal)

```python
class ReportAgent:
    async def generate_async(self, tipo: str, params: dict, canal_id: str) -> None:
        """Gera em background e notifica via Slack quando pronto."""
        asyncio.create_task(self._run(tipo, params, canal_id))

    async def _run(self, tipo, params, canal_id):
        dados = await self._collect(tipo, params)
        relatorio = self._format(dados)
        await slack_notify(canal_id, relatorio)
```

---

## Dispatch paralelo multi-cliente

Pergunta hoje: `"como está o Source OF e o POD Kombucha essa semana?"`
Fluxo atual: executa sequencialmente (Source OF → POD → agrega)

Fluxo Fase 2:
```python
async def _dispatch_multi_cliente(intent, params, clientes):
    tasks = [executar_cliente(intent, params, ck) for ck in clientes]
    results = await asyncio.gather(*tasks)
    # Agrega em paralelo — economiza ~50% do tempo para N clientes
    return aggregate(results)
```

Ganho real: para 3 clientes, cai de ~3×latência para ~1×latência (limite do mais lento).

---

## Migração incremental

A grande vantagem: `base_agent.py` já existe. A migração pode ser feita intent por intent:

1. Criar `MetaAdsAgent` com os intents mais usados (`top_roas`, `resumo_geral`)
2. No `_executar_intent()`, checar se existe agente especializado antes de chamar queries diretamente
3. Migrar intents restantes ao longo do tempo
4. `engine.py` fica como orquestrador legacy que vai esvaziando gradualmente

```python
# engine.py — migração incremental
def _executar_intent(intent, campanha, data_inicio, data_fim, limit, meta, cliente_key, plataforma):
    # Nova rota: agente especializado se existir
    agente = _get_agent_for_platform(plataforma)
    if agente and agente.handles(intent):
        return agente.execute(intent, campanha, data_inicio, data_fim, limit, meta, cliente_key)

    # Rota legada (intocada até ser migrada)
    return _executar_intent_legacy(intent, ...)
```

---

## Checklist de gatilho

Quando qualquer um desses for verdade, iniciar Fase 2:

- [ ] 3º cliente ativo adicionado
- [ ] TikTok Ads ou outra plataforma integrada
- [ ] Latência média de `consultar()` > 5s
- [ ] `engine.py` ultrapassar 1200 linhas
- [ ] Relatório assíncrono solicitado pelo usuário

---

## Estado atual dos componentes

| Componente | Status |
|---|---|
| `base_agent.py` | ✅ Criado |
| `sql_generator.py` (SQLAgent) | ✅ Implementado + Query Planner |
| `MetaAdsAgent` | 🔲 Planejado |
| `GoogleAdsAgent` | 🔲 Planejado |
| `ReportAgent` | 🔲 Planejado |
| `OrchestratorAgent` | 🔲 Planejado |
| Dispatch paralelo async | 🔲 Planejado |
