# Texto para testadores — Agente de Landing Page

Olá! 👋

Você foi selecionado para testar o **Agente de Landing Page** — ele gera páginas HTML completas e prontas para publicar a partir de um briefing feito aqui no Slack.

---

**📖 Leia antes de começar:**
https://github.com/Eroscv/Agente_Performance/blob/main/landing_page/README.md

---

**Por onde começar:**

```
/criar-landing
```

O agente faz 7 perguntas: cliente/produto, objetivo, público-alvo, tom de voz, informações extras, referências positivas e o que não quer. Leva menos de 2 minutos para preencher. A LP é gerada em background (~30s) e você recebe o arquivo `.html` aqui no Slack.

**Para publicar e ver no browser:**
1. Baixe o arquivo `.html` enviado pelo agente
2. Acesse [netlify.com/drop](https://netlify.com/drop) e arraste o arquivo
3. Online em segundos — sem precisar de servidor

---

**O que mais testar:**
- `/meus-projetos` — lista suas LPs anteriores com data e briefing
- `/variar-lp` — reaproveita um briefing anterior mudando só um campo (tom, objetivo, público)
- `/minhas-refs` — gerencia suas referências visuais acumuladas
- Testar tons diferentes na mesma LP: `urgente e direto` vs `aspiracional/premium`
- Pular campos opcionais com `pular` — ver como o agente preenche com boas práticas
- Gerar duas versões do mesmo produto com objetivos diferentes (`venda direta` vs `captação de leads`)
- Cancelar no meio com `cancelar`

---

**O que avaliar na LP gerada:**
- Headline principal — clara, direta, focada no benefício
- Seções completas: benefícios, como funciona, depoimentos, FAQ, CTAs
- Tom aplicado corretamente ao texto
- HTML abrindo sem erro no browser
- Visual limpo e publicável sem edição

---

**Período de testes: 1 semana.**

Me manda o feedback aqui no canal ou em DM:

1. Qualidade da LP gerada — o que ficou bom, o que ficou genérico
2. Algo que o briefing não capturou bem
3. Bug ou comportamento inesperado no fluxo
4. Sugestão de campo ou comando que deveria existir

Quanto mais detalhe melhor: o briefing que você usou, o que esperava e o que aconteceu.

Obrigado! 🙏
