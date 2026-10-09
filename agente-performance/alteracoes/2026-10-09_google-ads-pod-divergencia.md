# Google Ads POD Kombucha — Divergência R$7,7k vs R$4,7k (09/10/2026)

## O que foi investigado

Durante a verificação da Semana 40 (28/09 a 04/10), o Google Ads da POD Kombucha mostrava
**R$ 7.692** no banco de dados, mas o relatório apresentava **R$ 4.668**.

---

## O que foi descoberto

A divergência não é um erro de dados. O banco está capturando **duas contas Google Ads diferentes** rodando ao mesmo tempo:

| Conta | Campanhas | Investimento Sem 40 |
|---|---|---|
| **Conta antiga** (pré-MCC) | `PMax Geral`, `[SEARCH] Branded`, `[SEARCH] Hidratante Corporal`, `[Guará] Shopping - Branded`, `[Guara] Shopping - Gel Creme Facial` | R$ 3.023,93 |
| **Conta nova** (MCC 612-367-2195) | `[PMAX] Geral`, `[SEARCH] [POD] Branded`, `[SEARCH][PODDI] Branded`, `[DEMAND GEN] [POD]`, `[SHOPPING] [POD] [Lata Kombucha] Branded` | R$ 4.668,34 |
| **Total capturado** | — | **R$ 7.692,27** |

O relatório que mostra R$4.668 está considerando **apenas a conta nova (MCC)**.
O banco captura as duas porque o sync busca todas as campanhas disponíveis.

---

## O que já foi corrigido (não precisa fazer nada)

- ✅ Dados corretos no banco — as duas contas estão sendo capturadas normalmente
- ✅ MCC 612-367-2195 confirmado ativo desde 07/10/2026
- ✅ Conta nova sincronizando sem problemas

---

## O que fica pendente — precisa de confirmação humana

### ❓ Pergunta 1 — A conta antiga ainda deveria estar ativa na Sem 40?

As campanhas da conta antiga (`PMax Geral`, `[SEARCH] Branded` etc.) estavam investindo
**R$ 3.023** entre 28/09 e 04/10 — ou seja, **antes** da conexão do MCC (07/10).

Precisa confirmar no painel do Google Ads:
- Essas campanhas estavam rodando intencionalmente nesse período?
- Ou deveriam ter sido pausadas antes da virada para o MCC?

### ❓ Pergunta 2 — Qual é o investimento real da Sem 40?

Dependendo da resposta acima, o valor correto muda:

- Se as duas contas rodaram em paralelo intencionalmente → **R$ 7.692 é o real**
- Se a conta antiga não deveria estar ativa → **R$ 4.668 é o real** e houve R$ 3.023 investidos indevidamente

### ❓ Pergunta 3 — A conta antiga ainda está ativa hoje?

Se sim, o banco vai continuar somando as duas. Precisa definir se:
- A conta antiga deve ser pausada/encerrada
- Ou se ambas devem continuar rodando em paralelo

---

## Como confirmar

1. Acessar o Google Ads Manager (MCC 612-367-2195)
2. Verificar quais contas filhas estão ativas
3. Confirmar se as campanhas `PMax Geral`, `[SEARCH] Branded` etc. estão pausadas ou ativas
4. Me retornar com a resposta — ajusto o sync se necessário

---

*Investigado por Cesar (Agente de Performance Guará) em 09/10/2026.*
