# 📅 Histórico de Evolução — Agente de Performance Guará

> Documento completo de todas as melhorias realizadas, organizadas por data, com explicação do impacto em cada mudança.

---

## 2026-06-23 — Nascimento do agente (v2.0)

**Contexto:** Primeira versão funcional. Saiu de um script simples para um agente conversacional completo.

| Alteração | O que muda na prática |
|---|---|
| Integração Slack + Claude conversacional | O agente passa a responder perguntas em português pelo Slack em tempo real |
| NLP com Claude + fuzzy matching + memória de contexto | Entende variações de escrita ("semana passada", "últimos 7 dias", "essa semana") e lembra o contexto da conversa |
| Slack Block Kit — tabelas e formatação nativa | As respostas deixam de ser texto plano e ganham tabelas visuais no Slack |
| 9 slash commands (/hoje /semana /mes /top /campanha etc.) | Usuário pode acionar relatórios com comandos diretos, sem digitar perguntas |
| Exportação para Notion | Relatórios passam a ser publicados automaticamente no Notion com link enviado no Slack |
| Comparativos (WoW, MoM, antes/depois, ranking) | Agente responde "compare essa semana com a anterior" com delta e seta de tendência |
| Cache L1+L2 (memória + SQLite) | Mesma pergunta feita duas vezes responde instantaneamente, sem chamar a API novamente |
| 3 bugs críticos corrigidos | Busca fuzzy de campanhas, sugestões quando campanha não é encontrada, alertas ROAS com valores nulos |

---

## 2026-06-24 — Usabilidade e cache persistente

| Alteração | O que muda na prática |
|---|---|
| Cache L1+L2 sobrevive a restart | Respostas cacheadas não se perdem quando o servidor reinicia |
| /ajuda redesenhado com 10 exemplos práticos | Novos usuários conseguem usar o agente sem precisar de treinamento |
| 5 comandos auxiliares (/status, /exemplos, /metricas, /kb, /cache-limpar) | Mais controle operacional direto pelo Slack |

---

## 2026-06-26 — Lógica de relatório Meta Ads

| Alteração | O que muda na prática |
|---|---|
| Lógica Meta Ads com regras da reunião Bianca/Eros | Cálculos e métricas ajustados para o que foi acordado internamente |
| README e MVP.md com plano de etapas | Documentação técnica do projeto criada |

---

## 2026-06-29 — Relatórios automáticos (histórico Notion)

| Alteração | O que muda na prática |
|---|---|
| Histórico de relatórios organizado no Notion | Cada relatório gerado fica salvo no Notion com data, permitindo consulta retroativa |
| Cache de relatório — mesmo período atualiza em vez de recriar | Evita duplicidade: se o relatório da semana já existe no Notion, ele é atualizado no lugar de criar uma cópia nova |
| /relatorio slash command integrado | Usuário dispara geração de relatório direto pelo Slack |
| Cron para relatório semanal e mensal | Relatórios passam a ser gerados automaticamente sem ninguém precisar pedir |

---

## 2026-06-30 — Qualidade e robustez dos dados

| Alteração | O que muda na prática |
|---|---|
| Validador de integridade pós-carga | Antes de gerar qualquer relatório, o sistema verifica duplicatas, ROAS negativo, datas inválidas e cobertura |
| Deduplicação automática por (campanha, data_inicio) | Dados importados duas vezes não geram linhas duplicadas |
| ROAS lido direto do banco (roas_compras) | Remove cálculo manual que podia divergir do valor oficial da plataforma |
| ROAS ponderado, WHERE seguro, aviso em período vazio | Relatórios de períodos sem dados emitem aviso claro em vez de retornar tabela vazia sem explicação |
| NLP fallback com contexto, CPA guard, SQL granular | Erros de consulta passam a mostrar diagnóstico específico em vez de mensagem genérica |

---

## 2026-07-01 — Relatório semanal e mensal via Notion

| Alteração | O que muda na prática |
|---|---|
| Relatório semanal e mensal publicados no Notion | Toda semana e todo mês, o relatório completo é gerado e publicado automaticamente com link enviado no Slack |

---

## 2026-07-06 — Precisão 100% nas métricas

| Alteração | O que muda na prática |
|---|---|
| Elimina AVG() para métricas derivadas | ROAS, CPA, CTR calculados com agregação correta — sem distorção por médias de médias |
| 15 melhorias de qualidade de relatório | Formato Notion aprimorado: resumo executivo, funil visual, tabela enxuta |
| Testes cobrindo todas as melhorias | Regressões futuras são detectadas automaticamente |

---

## 2026-07-08 — Consultas e testes de integração

| Alteração | O que muda na prática |
|---|---|
| 6 melhorias em consultas e relatoria | Maior variedade de perguntas respondidas corretamente |
| Testes de integração para todos os tipos de consulta | Cada tipo de pergunta (ROAS, COMPRAS, FUNIL, WoW, MoM etc.) coberta por testes automatizados |

---

## 2026-07-13 — Resiliência e robustez

| Alteração | O que muda na prática |
|---|---|
| Retry infinito com backoff exponencial no Slack webhook | Alertas e relatórios nunca são perdidos por falha temporária de rede — sistema tenta até conseguir enviar |
| 9 melhorias de qualidade/robustez | Tratamento de casos-limite (NULL, períodos sem dados, campanhas inativas) |
| 61 testes cobrindo todo o pipeline | Suite completa: RESUMO, ROAS, COMPRAS, CAMPANHA, FUNIL, EVOLUCAO, WoW, MoM, períodos, ranking, builder, cache |

---

## 2026-07-15 — Google Sheets + NLP aprimorado

| Alteração | O que muda na prática |
|---|---|
| Integração Google Sheets como fonte de dados dinâmica | Dados que viviam em planilha agora alimentam o agente diretamente |
| 14 melhorias na consulta por linguagem natural | Mais perguntas do dia a dia respondidas sem precisar usar slash commands |
| Expansão de queries v3 (69 consultas mapeadas) | Agente reconhece e responde 69 tipos diferentes de pergunta |
| Auto-update CONSULTAS.md diário via cron | Documentação do que o agente sabe responder se atualiza automaticamente todo dia |

---

## 2026-07-16 — Relatório mensal completo

| Alteração | O que muda na prática |
|---|---|
| Análise semanal com 3 tabelas separadas (site/CRM, Meta Ads, Google Ads) | Cada canal fica em sua seção, mais fácil de ler |
| Relatório mensal com 17 seções, dados reais de Shopify/CRM | Relatório mensal passa a ter visão completa do negócio: rentabilidade, recompradoras, SKUs, estados |
| Análise narrativa automática (fases, correlação invest×ROAS) | Claude identifica padrões no mês e escreve um parágrafo de análise automaticamente |
| Título padronizado 'Source Of Report - Mensal Mês Ano' | Fácil de localizar no histórico do Notion |

---

## 2026-07-21 — POD Kombucha + Shopify + Multi-cliente

**Maior sprint até então. Sistema passa de 1 para 2 clientes.**

| Alteração | O que muda na prática |
|---|---|
| POD Kombucha — loader Google Ads + builder semanal/mensal | Segundo cliente entra no sistema com relatórios independentes |
| Módulo shopify/client.py (SKU, Estado, Recompras, Assiduidade) | Dados da loja Shopify entram nos relatórios: pedidos por SKU, por estado, recompras por cliente |
| Relatórios diário/semanal/mensal para Source OF e POD | Cada cliente recebe seu próprio relatório no horário certo, sem misturar dados |
| Registry multi-cliente (clientes.json) | Configuração centralizada: adicionar novo cliente vira questão de adicionar uma linha no JSON |
| Alertas ROAS proativos + relatório diário automático | Sistema avisa no Slack quando ROAS cai abaixo do esperado, sem ninguém precisar pedir |
| Breakdown diário ao pedir "essa semana até agora" | Pergunta "como foi essa semana" mostra tabela dia a dia, não só total |
| Funil de conversão em tabela estruturada | Funil deixa de ser lista de bullets e vira tabela com Etapa / Volume / Taxa |
| Comparativo com rentabilidade (MER, Receita Shopify, Invest.+Imposto) | Relatório mensal compara o que foi investido com o que voltou em receita real |
| Relatórios Slack com âncora + thread detalhada | Uma mensagem-âncora no canal, detalhes na thread — organiza sem poluir o canal |
| Atualização automática do Google Sheets antes do relatório | Dados sempre frescos antes de gerar qualquer relatório |

---

## 2026-07-22 — Shopify em tempo real + documentação

| Alteração | O que muda na prática |
|---|---|
| Integração Shopify em tempo real | Dados de pedidos consultados na hora, sem depender de sync anterior |
| Loader com retry 3x antes de abortar | Falha temporária de API não derruba o relatório todo |
| Documentação completa do sistema | README com arquitetura, uso, CLI, cron, alertas e roadmap |

---

## 2026-07-27 — Melhorias relatório semanal Source OF

| Alteração | O que muda na prática |
|---|---|
| Melhorias no relatório semanal Source OF | Formato e conteúdo refinados com base no uso real |

---

## 2026-08-17 — APIs diretas + arquitetura robusta

**Sprint de integração direta com Meta Ads e Google Ads (sem planilha intermediária).**

| Alteração | O que muda na prática |
|---|---|
| Meta Ads API direto — loader, sync diário, cron 07:30 UTC | Dados do Meta Ads buscados diretamente da API oficial todo dia às 07:30 — sem depender de exportação manual |
| Google Ads API direto — loader, view v_performance, índices | Dados do Google Ads buscados diretamente da API todo dia às 07:00 |
| POD Kombucha Meta Ads API conectada | Segundo cliente com Meta Ads via API |
| Arquitetura quebra builder.py em builder_semanal/mensal/periodo/shared | Código mais organizado, mais fácil de manter e testar individualmente |
| Tabelas separadas por cliente + fonte | Dados de cada cliente e cada plataforma em tabelas isoladas — impossível misturar |
| Insights & ações via Claude em todos os relatórios | Todos os relatórios (diário, semanal, mensal) passam a ter análise interpretativa automática |
| Log de todos os relatórios disparados em SQLite | Rastreabilidade completa: quando foi gerado, para qual cliente, qual canal Slack |
| Backup automático dos bancos SQLite a cada 3 dias | Proteção contra perda de dados históricos |
| Salvar todas as consultas de usuários | Permite analisar quais perguntas são mais feitas e o que o agente não sabe responder |
| Filtro cliente_key+plataforma completo | Perguntas sem contexto explícito de cliente assumem Source OF como padrão |
| Melhorias fluxo NLP — contexto persistido, KB, Google Ads NL | Agente lembra o contexto dentro da conversa e consulta a base de conhecimento antes de responder |
| Remove código morto | Sistema mais limpo, sem funções obsoletas que podiam causar confusão |

---

## 2026-08-18 — Arquitetura multi-agentes + isolamento de clientes

**Sprint de refatoração profunda. Sistema passa de monolítico para arquitetura em camadas.**

| Alteração | O que muda na prática |
|---|---|
| Arquitetura 2 camadas — OrchestratorAgent + ClientAgents | OrchestratorAgent roteia a pergunta; ClientAgent (Source OF ou POD) responde — nunca há risco de dados de um cliente aparecerem na resposta de outro |
| Isolamento estrutural de clientes (pastas clientes/pod/ e clientes/source_of/) | Código de cada cliente fisicamente separado — mudança em um não afeta o outro |
| db/connections.py — gerenciador multi-DB com views federadas | Uma conexão única que enxerga todos os bancos (SQLite por cliente/plataforma) como se fossem um só |
| ValidationAgent — validação de sanidade antes do LLM | Antes de chamar o Claude, o sistema verifica se os dados fazem sentido (ROAS impossível, volume zero, datas futuras) |
| Query Planner — 2-stage LLM para perguntas complexas | Perguntas como "compare as 3 melhores campanhas dessa semana vs semana passada" são decompostas em sub-queries antes de serem respondidas |
| SQL dinâmico (Text-to-SQL) como fallback | Perguntas que não se encaixam em nenhum template viram SQL gerado pelo Claude — responde qualquer pergunta ad-hoc |
| Confidence/Evidence — nível de confiança estruturado | Cada resposta indica de onde veio o dado e com qual grau de certeza |
| Grounded Generation (citation forcing + structured output) | Respostas fundamentadas em dados reais, sem invenção — o Claude é forçado a citar os números do banco |
| Deduplicação de alertas ROAS | Mesmo alerta não é reenviado duas vezes no mesmo dia |
| Log completo de disparos Slack | Rastreio de cada mensagem enviada ao Slack com timestamp, canal e status |
| Validação obrigatória pré-Slack com double-check | Antes de enviar qualquer mensagem, o sistema valida que os dados estão completos e coerentes |
| Excel semanal + relatórios source_of e pod_kombucha | Exportação em Excel disponível para ambos os clientes |
| Fix Meta Ads lote de 1 dia + retry individual | Contorna erro HTTP 500 da API da Meta buscando um dia por vez com retry independente |
| Fix chunking automático rich_text no Notion | Blocos de texto > 2000 caracteres são divididos automaticamente — sem erro de limite da API Notion |
| Aviso automático de delay de dados | Quando o usuário pergunta "hoje" mas o banco só tem até ontem, o sistema avisa com clareza |

---

## 2026-08-19 — DuckDB + Shopify POD + Consolidado Notion + Guardrails

**Sprint de performance analítica e experiência de relatório.**

| Alteração | O que muda na prática |
|---|---|
| DuckDB — db/duck.py + 24 testes (24/24 passando) | Motor analítico DuckDB adicionado ao lado do SQLite — consultas complexas (window functions, GROUP BY aninhado) ficam muito mais rápidas |
| ETL histórico para Parquet (star schema) | Todo o histórico de dados exportado em formato Parquet — base para análises analíticas pesadas e dashboards futuros |
| DuckDB lê Parquet via views dw_* | Consultas no DuckDB enxergam o histórico completo via views, sem precisar recarregar os dados |
| Shopify integração POD Kombucha | POD Kombucha passa a ter dados Shopify nos relatórios, igual à Source OF |
| Intent comparar_plataformas | Nova intenção reconhecida: "compare Meta Ads com Google Ads" exibe os dois lado a lado com diferencial |
| Relatório diário multi-plataforma consolidado | Uma única thread no Slack por cliente com todas as plataformas, em vez de mensagens espalhadas |
| Página Notion consolidada por cliente | Uma página no Notion por cliente com Google Ads + Meta Ads + Shopify — um único link no Slack |
| Guardrails de qualidade de dados | Sistema recusa gerar relatório se: volume de dados está abaixo do mínimo esperado, houve queda brusca inexplicada, ou os dados estão fora da janela de atribuição |
| DuckDB window functions no relatório diário | Comparativo automático dia vs dia anterior com % de variação, gerado pelo DuckDB |
| Fix ROAS 0.00x exibido corretamente | ROAS zero (campanha sem venda) agora exibe "0.00x" em vez de aparecer como dado ausente |
| Fix período do alerta ROAS usa data_maxima do banco | Alertas calculados com base na data mais recente que existe no banco para aquele cliente |
| Fix sync Meta Ads e Google Ads no banco federado correto | Dados de cada plataforma garantidamente gravados na fonte correta |
| Views federadas com auto-reconexão | Se a conexão com um banco cai, o sistema reconecta automaticamente sem derrubar o serviço |
| QA_REPORT.md — 39 casos manuais + 201 testes automatizados | Cobertura de testes documentada formalmente |

---

## Resumo de evolução por período

| Período | Foco principal | Resultado |
|---|---|---|
| Jun/23–24 | MVP conversacional | Agente responde pelo Slack em português |
| Jun/26–30 | Qualidade de dados e relatórios Notion | Dados confiáveis, relatórios automáticos |
| Jul/01–08 | Relatórios automáticos semanal/mensal | Zero trabalho manual para gerar relatórios |
| Jul/13–15 | Resiliência + Google Sheets + NLP melhorado | Sistema não perde mensagens, entende mais perguntas |
| Jul/16 | Relatório mensal completo (17 seções) | Relatório profissional com análise narrativa |
| Jul/21 | POD Kombucha + Shopify integrado | Sistema atende 2 clientes com dados reais de e-commerce |
| Jul/22 | Shopify em tempo real | Dados de pedidos consultados ao vivo |
| Ago/17 | APIs diretas Meta + Google Ads | Fim da dependência de planilhas manuais |
| Ago/18 | Arquitetura multi-agentes | Sistema robusto, isolado por cliente, com validação em múltiplas camadas |
| Ago/19 | DuckDB + Parquet + Guardrails | Base analítica de alta performance + qualidade garantida nos relatórios |
