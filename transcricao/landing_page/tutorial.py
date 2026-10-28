"""
landing_page/tutorial.py
Tutorial do agente de Landing Pages (comando /tutorial-landing).
"""


async def cmd_tutorial_landing(ack, respond):
    await ack()
    texto = """\
*🎓 Tutorial — Agente de Landing Pages*

Gera landing pages HTML completas pelo Slack — sem designer, sem dev.

*Como usar:*
Digite `/criar-landing` e responda 7 perguntas:

1. Cliente / Produto
2. Objetivo (ex: venda direta, captação de leads)
3. Público-alvo
4. Tom de voz (ex: urgente, empático, aspiracional)
5. Informações extras (ou `pular`)
6. Referências visuais que você GOSTA (ou `pular`)
7. Referências a EVITAR (ou `pular`)

Depois revise o briefing e digite *confirmar* para gerar.
Em qualquer momento, digite *cancelar* para abortar.

*O que é gerado:*
Arquivo `.html` mobile-first com: hero, benefícios, como funciona, depoimentos, garantia, FAQ, CTA final e rodapé.

*Para publicar:* arraste o arquivo para netlify.com/drop

*Comandos:*
`/criar-landing` — iniciar briefing
`/tutorial-landing` — este guia
_Só você está vendo esta mensagem._"""
    await respond(text=texto, response_type="ephemeral")
