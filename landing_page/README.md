# 🚀 Agente de Landing Page — Guia de Uso

Gere landing pages HTML completas e prontas para publicar direto pelo Slack.
O agente aprende suas preferências e referências visuais ao longo do tempo.

---

## Comandos disponíveis

| Comando | O que faz |
|---|---|
| `/criar-landing` | Cria uma nova landing page (fluxo de 7 perguntas) |
| `/meus-projetos [N]` | Lista suas últimas N LPs geradas |
| `/variar-lp [id]` | Gera nova versão de uma LP mudando só um campo |
| `/minhas-refs` | Gerencia suas referências visuais acumuladas |
| `/tutorial-landing` | Mostra este guia |

---

## /criar-landing — passo a passo

```
/criar-landing
```

O agente faz **7 perguntas**. Se você já gerou LPs antes, ele sugere seus valores anteriores como dica.

**Passo 1 — Cliente / Produto**
> Ex: `Source OF — Colágeno Verisol 300g`
> Ex: `POD Kombucha — Kit Iniciante 6 sabores`

**Passo 2 — Objetivo**
O que a LP deve fazer:
- `captação de leads`
- `venda direta`
- `lançamento de produto`
- `evento/webinar`
- `teste A/B`
- Ou descreva livremente

**Passo 3 — Público-alvo**
> Ex: `Mulheres 30–50 anos, interessadas em saúde e bem-estar`

**Passo 4 — Tom de voz**
- `urgente e direto`
- `empático e educativo`
- `aspiracional/premium`
- `descontraído e jovem`
- `técnico/especialista`
- Ou descreva livremente

> 💡 Se você já usou o agente antes, seu tom favorito aparece como sugestão.

**Passo 5 — Informações extras** _(opcional)_
Promoção, preço, benefícios, depoimentos, garantia...
> Ex: `10% off na primeira compra · Frete grátis acima de R$150 · Garantia 30 dias`

Digite `pular` para deixar o agente usar as melhores práticas.

**Passo 6 — Referências positivas** _(opcional)_
Sites ou estilos que você considera referência visual.
> Ex: `Apple.com — clean, espaço em branco, tipografia grande`
> Ex: `Notion.so — minimalista, sem ruído`

> 💡 Suas refs anteriores aparecem como dica. Digite `pular` para usar as mesmas.

**Passo 7 — O que NÃO quer** _(opcional)_
Estilos ou elementos a evitar.
> Ex: `Fundo escuro, countdown fake, visual gritante de info-produto`

Digite `pular` para não aplicar restrições.

**Confirmação**
Revise o briefing completo e confirme:
- `confirmar` — inicia a geração ✅
- `cancelar` — descarta tudo ❌

> ⏳ A LP é gerada em **background** (~30 segundos). Você recebe uma notificação aqui quando estiver pronta — pode continuar usando outros comandos enquanto isso.

---

## O que você recebe

Um arquivo `.html` enviado direto no Slack, completo e pronto para usar:

**Estrutura da LP gerada:**
1. Hero — Headline + Subheadline + CTA principal
2. Benefícios — 4–6 cards com ícone, título e descrição
3. Como funciona — 3 passos numerados
4. Prova social — 3 depoimentos com nome e cargo
5. Garantia — bloco de confiança
6. FAQ — 4 perguntas e respostas
7. CTA final — urgência + botão
8. Rodapé — copyright + links legais

**Para publicar:**
- Baixe o `.html` e arraste em [netlify.com/drop](https://netlify.com/drop) — online em segundos
- Ou abra direto no browser para revisar antes

---

## /meus-projetos

```
/meus-projetos
```
Lista suas últimas 8 LPs com cliente, objetivo, tom e data.

```
/meus-projetos 15
```
Lista as últimas 15.

Cada projeto mostra o comando `/variar-lp [id]` pronto para reusar o briefing.

---

## /variar-lp — gerar nova versão

Reaproveita o briefing de uma LP anterior mudando só um campo.

```
/variar-lp
```
Mostra seus projetos recentes para escolher.

```
/variar-lp 3
```
Abre direto o projeto #3.

**Campos que podem ser alterados:**
- `1` Objetivo
- `2` Tom de voz
- `3` Público-alvo
- `4` Extras / Promoção
- `5` Referências visuais
- `6` Gerar novamente sem mudanças (nova versão do mesmo briefing)

> 💡 Útil para testar variações de tom ou objetivo sem refazer todo o briefing.

---

## /minhas-refs — gerenciar referências visuais

```
/minhas-refs
```
Lista todas as suas referências positivas e negativas acumuladas ao longo dos projetos.

**Para descartar uma referência:**
```
descartar 3
```
```
descartar 3 7 12
```

Digite `sair` quando terminar.

> As referências são usadas automaticamente como contexto nas próximas LPs.

---

## Dicas gerais

- A qualquer momento durante o fluxo, digite `cancelar` para sair
- Se demorar mais de **60 minutos** sem responder, a sessão expira automaticamente
- Todas as LPs geradas ficam salvas no histórico (incluindo o HTML completo)
- Suas preferências de tom e cliente são salvas e sugeridas na próxima vez
- Referências visuais acumulam entre projetos — quanto mais você usa, mais contexto o agente tem
