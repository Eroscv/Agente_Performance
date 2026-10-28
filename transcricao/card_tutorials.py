"""
card_tutorials.py
Tutoriais para os agentes de transcrição e imagem.
"""


async def cmd_tutorial_transcricao(ack, respond):
    await ack()
    texto = """\
*🎓 Tutorial — Agente de Transcrição*
_Transforma atas de reunião em tarefas acionáveis no Notion, automaticamente._

*Como alimentar o agente:*
Suba o arquivo `.docx` da ata na pasta compartilhada do Google Drive (*Google Meet*).
O agente processa automaticamente e cria os cards no Notion.

*Formato de título gerado:*
`CLIENTE: FRENTE - Entrega`
Ex: `Botica: ADS - Variação campeão Dimples`

*Frentes disponíveis:*
ADS · Social · Conta · Relatoria · Planejamento · Site · Dados · Gestão

*Responsáveis mapeados:*
Aline Satur · Bianca Garbin · Eros Vital · Guilherme Oliveira
Isabela Rodrigues · Matheus Ferreira · Miguel Ticianeli

*Criar card manual:* `/criar-card`
*Ver status dos cards:* `/status-cards [nome]`
*Outros agentes:* `/tutorial` · `/tutorial-landing` · `/tutorial-imagem`
_Só você está vendo esta mensagem._"""
    await respond(text=texto, response_type="ephemeral")


async def cmd_tutorial_imagem(ack, respond):
    await ack()
    texto = """\
*🎓 Tutorial — Agente de Imagem*
_Gera criativos de mídia paga via Gemini diretamente pelo Slack._

*Como usar:*
Mencione o agente com a descrição do criativo:
`@agente banner de Black Friday para POD Kombucha, cores vibrantes`
`@agente story de lançamento para Source OF, minimalista`

*Formatos:*
• Banner/Horizontal (16:9): inclua `banner`, `horizontal`, `16:9`
• Story/Reels (9:16): inclua `story`, `reels`, `vertical`
• Post/Feed (1:1): padrão se não especificar

*Limites:* máx 10 imagens/hora por usuário.
*Resultado:* sempre postado em #image_gen.
_Só você está vendo esta mensagem._"""
    await respond(text=texto, response_type="ephemeral")
