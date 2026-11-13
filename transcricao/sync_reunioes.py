"""
sync_reunioes.py

Monitora a pasta do Google Drive e salva automaticamente
novas reuniões no banco SQLite.

Fluxo:
  1. Lista arquivos .docx na pasta compartilhada do Drive
  2. Verifica quais ainda não foram processados (via fonte_id no banco)
  3. Baixa, extrai texto e salva no banco com metadados estruturados

Uso:
  python3 sync_reunioes.py           # processa novos arquivos
  python3 sync_reunioes.py --todos   # reprocessa todos (força)
"""

import argparse
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from tz import hoje_brt, agora_utc_iso
from google.oauth2 import service_account
from googleapiclient.discovery import build

try:
    from pymongo import MongoClient, ASCENDING
    from pymongo.errors import DuplicateKeyError as MongoDuplicateKeyError
    _MONGO_AVAILABLE = True
except ImportError:
    _MONGO_AVAILABLE = False

try:
    from google.cloud import bigquery as bq
    _BQ_AVAILABLE = True
except ImportError:
    _BQ_AVAILABLE = False

# ── Configuração ──────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent
CREDS_FILE = BASE_DIR / "credentials" / "gdrive-service-account.json"
DB_PATH    = BASE_DIR / "db" / "reunioes.db"
SCOPES     = ["https://www.googleapis.com/auth/drive"]

BQ_PROJECT  = os.getenv("BQ_PROJECT",  "guara-db")
BQ_DATASET  = os.getenv("BQ_DATASET",  "transcricao")
BQ_TABLE    = os.getenv("BQ_TABLE",    "transcricao")
BQ_CREDS    = os.getenv("BQ_CREDS",    str(BASE_DIR / "credentials" / "gdrive-service-account.json"))

MONGO_URI  = os.getenv(
    "MONGODB_URI",
    "mongodb+srv://erosvital9_db_user:6oDGHngGEDAgwLce@transcricoesefeedbacks.dmbgfdg.mongodb.net"
)
MONGO_DB   = "agente_performance"
MONGO_COL  = "reunioes"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Drive ─────────────────────────────────────────────────────────────────────

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        str(CREDS_FILE), scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


MIME_DOCX     = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_GDOC     = "application/vnd.google-apps.document"
MIME_SHORTCUT = "application/vnd.google-apps.shortcut"
MIME_FOLDER   = "application/vnd.google-apps.folder"

# Pasta Google Meet compartilhada
GOOGLE_MEET_FOLDER_ID = os.getenv("GOOGLE_MEET_FOLDER_ID", "1OQ3iHb19iiAedxiB8m-nXIOTqbn7sShz")


def _resolver_shortcut(service, file_id: str) -> dict | None:
    """Resolve atalho para o arquivo real. Retorna metadados ou None."""
    try:
        meta = service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,modifiedTime,shortcutDetails"
        ).execute()
        sc = meta.get("shortcutDetails", {})
        target_id   = sc.get("targetId")
        target_mime = sc.get("targetMimeType", "")
        if target_id and target_mime in (MIME_GDOC, MIME_DOCX):
            return service.files().get(
                fileId=target_id,
                fields="id,name,mimeType,modifiedTime"
            ).execute()
    except Exception as e:
        logger.debug("[shortcut] Falha ao resolver %s: %s", file_id, e)
    return None


def _listar_pasta_recursivo(service, folder_id: str) -> list[dict]:
    """Varre recursivamente a pasta e resolve atalhos."""
    docs = []
    page_token = None
    while True:
        kwargs = dict(
            q=f"'{folder_id}' in parents and trashed=false",
            pageSize=200,
            fields="nextPageToken,files(id,name,mimeType,modifiedTime)",
        )
        if page_token:
            kwargs["pageToken"] = page_token
        result = service.files().list(**kwargs).execute()
        for f in result.get("files", []):
            mime = f["mimeType"]
            if mime in (MIME_GDOC, MIME_DOCX):
                docs.append(f)
            elif mime == MIME_FOLDER:
                docs.extend(_listar_pasta_recursivo(service, f["id"]))
            elif mime == MIME_SHORTCUT:
                real = _resolver_shortcut(service, f["id"])
                if real:
                    docs.append(real)
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return docs


def listar_docx(service) -> list[dict]:
    """Lista todos os docs: busca global + varredura recursiva da pasta Google Meet."""
    # 1. Busca global
    result = service.files().list(
        pageSize=200,
        q=(
            f"(mimeType='{MIME_DOCX}' or mimeType='{MIME_GDOC}')"
            " and trashed=false"
        ),
        fields="files(id,name,mimeType,modifiedTime,parents)",
        orderBy="modifiedTime desc",
    ).execute()
    todos = {f["id"]: f for f in result.get("files", [])}

    # 2. Varredura recursiva (resolve atalhos)
    for f in _listar_pasta_recursivo(service, GOOGLE_MEET_FOLDER_ID):
        todos[f["id"]] = f

    docs = list(todos.values())
    docs.sort(key=lambda x: x.get("modifiedTime", ""), reverse=True)
    logger.info("Docs encontrados (global + Meet recursivo): %d", len(docs))
    return docs


def baixar_docx(service, file_id: str, mime_type: str = MIME_DOCX) -> bytes:
    """Baixa conteúdo. Google Docs são exportados como .docx."""
    if mime_type == MIME_GDOC:
        return service.files().export_media(
            fileId=file_id, mimeType=MIME_DOCX,
        ).execute()
    return service.files().get_media(fileId=file_id).execute()


# ── Banco ─────────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reunioes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            data            TEXT NOT NULL,
            hora            TEXT,
            titulo          TEXT NOT NULL,
            participantes   TEXT,
            resumo          TEXT,
            decisoes        TEXT,
            proximas_etapas TEXT,
            detalhes        TEXT,
            fonte_arquivo   TEXT,
            fonte_id        TEXT UNIQUE,
            criado_em       TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fonte_id    TEXT,
            nome        TEXT,
            status      TEXT,
            mensagem    TEXT,
            criado_em   TEXT
        )
    """)
    conn.commit()
    return conn


def ja_processado(conn: sqlite3.Connection, file_id: str) -> bool:
    """Já processado se estiver no SQLite local OU no MongoDB (fonte que sobreviveu à perda da AWS).

    Falha fechada: se o MongoDB não responder, trata como processado e pula. Reprocessar por engano
    custa uma chamada ao Claude por arquivo e cria tarefas repetidas no Notion; pular só atrasa.
    """
    if conn.execute("SELECT id FROM reunioes WHERE fonte_id = ?", (file_id,)).fetchone():
        return True
    if os.getenv("TRANSCRICAO_SEM_MONGO") == "1":  # escape explícito para rodar sem Mongo
        return False
    col = get_mongo_col()
    if col is None:
        logger.error("[dedup] MongoDB indisponível — pulando %s para não reprocessar (falha fechada)", file_id)
        return True
    try:
        return col.find_one({"fonte_id": file_id}, {"_id": 1}) is not None
    except Exception as e:
        logger.error("[dedup] erro ao consultar MongoDB (%s) — pulando %s", e, file_id)
        return True


def salvar_log(conn, file_id, nome, status, mensagem):
    conn.execute(
        "INSERT INTO sync_log (fonte_id, nome, status, mensagem, criado_em) VALUES (?,?,?,?,?)",
        (file_id, nome, status, mensagem, agora_utc_iso()),
    )
    conn.commit()


# ── Extração de texto ─────────────────────────────────────────────────────────

def extrair_doc(docx_bytes: bytes):
    """Retorna objeto Document (python-docx) a partir dos bytes."""
    import io
    return Document(io.BytesIO(docx_bytes))


def extrair_texto(docx_bytes: bytes) -> str:
    """Extrai texto puro do .docx (mantido para compatibilidade)."""
    doc = extrair_doc(docx_bytes)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


# ── Parser de conteúdo ────────────────────────────────────────────────────────

# Aliases de títulos de seção por fonte
_ALIASES_SECAO = {
    # Gemini
    "resumo":          ["resumo"],
    "decisoes":        ["decisões", "decisoes"],
    "proximas_etapas": ["próximas etapas", "proximas etapas"],
    "detalhes":        ["detalhes"],
    # Granola
    "resumo_granola":  ["resumo da reunião", "resumo da reuniao", "visão geral", "visao geral"],
    "decisoes_granola":["decisões tomadas", "decisoes tomadas"],
    "proximas_granola":["próximos passos", "proximos passos", "ações", "acoes", "action items",
                        "encaminhamentos", "próximas ações"],
}


def _secoes_por_heading(doc) -> dict[str, str]:
    """
    Extrai seções de um Document agrupando parágrafos por Heading.
    Retorna dict {titulo_secao_lower: conteudo_concatenado}.
    Suporta Heading 1, 2 e 3 como delimitadores de seção.
    """
    secoes: dict[str, list[str]] = {}
    secao_atual = None
    heading_styles = {"heading 1", "heading 2", "heading 3"}

    for p in doc.paragraphs:
        style_lower = p.style.name.lower()
        texto_p = p.text.strip()
        if not texto_p:
            continue

        if style_lower in heading_styles:
            secao_atual = texto_p.lower()
            if secao_atual not in secoes:
                secoes[secao_atual] = []
        elif secao_atual is not None:
            secoes[secao_atual].append(texto_p)

    return {k: "\n".join(v).strip() for k, v in secoes.items() if v}


def _buscar_secao(secoes_heading: dict[str, str], aliases: list[str]) -> str:
    """Busca conteúdo de seção por lista de aliases (case-insensitive, contém)."""
    for alias in aliases:
        for chave, conteudo in secoes_heading.items():
            if alias in chave:
                return conteudo
    return ""


def _entre_secoes(texto: str, inicio: str, fins: list[str]) -> str:
    """Extrai bloco de texto entre seções (fallback para formato Gemini antigo)."""
    pattern = rf"{re.escape(inicio)}\n(.*?)(?={'|'.join(re.escape(f) for f in fins)}|$)"
    m = re.search(pattern, texto, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


_MESES_DATA = {
    "jan": "01", "fev": "02", "mar": "03", "abr": "04",
    "mai": "05", "jun": "06", "jul": "07", "ago": "08",
    "set": "09", "out": "10", "nov": "11", "dez": "12",
}


def _extrair_data_hora(texto: str, nome_arquivo: str) -> tuple[str, str]:
    """Extrai data ISO e hora do nome do arquivo ou do corpo do texto."""
    # Nome estilo Gemini: "2026_09_28 09_00" ou "2026_09_28"
    m = re.search(r"(\d{4}_\d{2}_\d{2})\s+(\d{2}_\d{2})", nome_arquivo)
    if m:
        return m.group(1).replace("_", "-"), m.group(2).replace("_", ":")

    # Nome estilo Granola / Google Meet: "2026/10/26 14:00" ou "26/10/2026, 14h00"
    m = re.search(r"(\d{4})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2})", nome_arquivo)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", f"{m.group(4)}:{m.group(5)}"

    # Corpo: "Data: 26/10/2026, 14h00"
    m = re.search(r"Data:\s*(\d{1,2})/(\d{2})/(\d{4}),?\s*(\d{2})h(\d{2})", texto)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1).zfill(2)}", f"{m.group(4)}:{m.group(5)}"

    # Corpo Gemini antigo: "set. 28, 2026"
    m = re.search(r"(\w{3})\.\s+(\d{1,2}),\s+(\d{4})", texto)
    if m:
        mes = _MESES_DATA.get(m.group(1).lower(), "01")
        return f"{m.group(3)}-{mes}-{m.group(2).zfill(2)}", ""

    return hoje_brt().isoformat(), ""


def _extrair_participantes(doc, texto: str) -> str:
    """
    Tenta extrair participantes:
    1. Linha "Participantes: ..." no corpo (Granola)
    2. Linha começando com "convidado" (Gemini)
    """
    for p in doc.paragraphs:
        txt = p.text.strip()
        if txt.lower().startswith("participantes:"):
            return txt.split(":", 1)[1].strip()

    for linha in texto.splitlines():
        if linha.lower().startswith("convidado"):
            return linha.replace("convidado", "", 1).strip()

    return ""


def parsear_reuniao(texto: str, nome_arquivo: str, doc=None) -> dict:
    """
    Extrai estrutura da reunião.
    Suporta dois formatos:
      - Gemini: seções delimitadas por texto ("Resumo", "Próximas etapas"…)
      - Granola: seções delimitadas por Heading 2 ("Próximos Passos"…)

    Se `doc` (objeto Document) for passado, usa extração por heading primeiro.
    Fallback automático para regex de texto quando campos ficam vazios.
    """
    titulo = nome_arquivo.split(" - ")[0].strip() if " - " in nome_arquivo else nome_arquivo
    data_str, hora_str = _extrair_data_hora(texto, nome_arquivo)

    # ── Extração por headings (Granola e qualquer doc estruturado) ──
    secoes_heading: dict[str, str] = {}
    participantes = ""
    if doc is not None:
        secoes_heading = _secoes_por_heading(doc)
        participantes  = _extrair_participantes(doc, texto)

    if not participantes:
        for linha in texto.splitlines():
            if linha.lower().startswith("convidado"):
                participantes = linha.replace("convidado", "", 1).strip()
                break

    # ── Resumo ──
    resumo = _buscar_secao(secoes_heading,
        _ALIASES_SECAO["resumo"] + _ALIASES_SECAO["resumo_granola"])
    if not resumo:
        resumo = _entre_secoes(texto, "Resumo",
            ["Integração", "Decisões", "Próximas", "Detalhes", "Anexos"])

    # ── Decisões ──
    decisoes = _buscar_secao(secoes_heading,
        _ALIASES_SECAO["decisoes"] + _ALIASES_SECAO["decisoes_granola"])
    if not decisoes:
        decisoes = _entre_secoes(texto, "Decisões", ["Próximas etapas", "Detalhes", "Revise"])

    # ── Próximas etapas / Próximos Passos ──
    proximas = _buscar_secao(secoes_heading,
        _ALIASES_SECAO["proximas_etapas"] + _ALIASES_SECAO["proximas_granola"])
    if not proximas:
        proximas = _entre_secoes(texto, "Próximas etapas", ["Detalhes", "Revise", "Como está"])

    # ── Detalhes ──
    detalhes = _buscar_secao(secoes_heading, _ALIASES_SECAO["detalhes"])
    if not detalhes:
        detalhes = _entre_secoes(texto, "Detalhes", ["Revise", "Como está", "Confira"])

    # Fallback: se detalhes ainda vazio, concatenar todas as seções que sobraram
    if not detalhes and secoes_heading:
        conhecidas = set(
            _ALIASES_SECAO["resumo"] + _ALIASES_SECAO["resumo_granola"]
            + _ALIASES_SECAO["decisoes"] + _ALIASES_SECAO["decisoes_granola"]
            + _ALIASES_SECAO["proximas_etapas"] + _ALIASES_SECAO["proximas_granola"]
        )
        extras = [
            f"[{k}]\n{v}"
            for k, v in secoes_heading.items()
            if not any(alias in k for alias in conhecidas)
        ]
        detalhes = "\n\n".join(extras)[:4000]

    from glossario import normalizar  # ASR do Meet: "Sorso Soft" -> "Source Of", "FEC" -> "FEQ"
    return {
        "data":            data_str,
        "hora":            hora_str,
        "titulo":          normalizar(titulo),
        "participantes":   participantes,
        "resumo":          normalizar(resumo),
        "decisoes":        normalizar(decisoes),
        "proximas_etapas": normalizar(proximas),
        "detalhes":        normalizar(detalhes),
    }


def salvar_reuniao(conn: sqlite3.Connection, dados: dict, nome_arquivo: str, file_id: str):
    agora = agora_utc_iso()
    conn.execute("""
        INSERT OR IGNORE INTO reunioes
          (data, hora, titulo, participantes, resumo, decisoes,
           proximas_etapas, detalhes, fonte_arquivo, fonte_id, criado_em)
        VALUES
          (:data, :hora, :titulo, :participantes, :resumo, :decisoes,
           :proximas_etapas, :detalhes, :fonte_arquivo, :fonte_id, :criado_em)
    """, {**dados, "fonte_arquivo": nome_arquivo, "fonte_id": file_id, "criado_em": agora})
    conn.commit()


def atualizar_reuniao_campos_vazios(
    conn: sqlite3.Connection,
    dados: dict,
    nome_arquivo: str,
    file_id: str,
) -> bool:
    """
    Atualiza campos vazios de uma reunião já existente (reprocessamento).
    Só preenche campos que hoje são NULL ou string vazia.
    Retorna True se algum campo foi atualizado.
    """
    row = conn.execute(
        """SELECT resumo, decisoes, proximas_etapas, detalhes, participantes
           FROM reunioes WHERE fonte_id = ?""",
        (file_id,)
    ).fetchone()
    if not row:
        return False

    updates = {}
    for campo in ("resumo", "decisoes", "proximas_etapas", "detalhes", "participantes"):
        antigo = row[campo] if isinstance(row, sqlite3.Row) else row[list(dados.keys()).index(campo)]
        novo   = dados.get(campo) or ""
        # Atualiza apenas se o campo estava vazio e o novo tem conteúdo
        if (not antigo or antigo.strip() == "") and novo.strip():
            updates[campo] = novo

    if not updates:
        return False

    set_clause = ", ".join(f"{c} = :{c}" for c in updates)
    conn.execute(
        f"UPDATE reunioes SET {set_clause} WHERE fonte_id = :fonte_id",
        {**updates, "fonte_id": file_id},
    )
    conn.commit()
    logger.info(
        "[update] Campos atualizados em '%s': %s",
        file_id[:12], ", ".join(updates.keys()),
    )
    return True


# ── MongoDB ───────────────────────────────────────────────────────────────

_mongo_client = None


def get_mongo_col():
    """Retorna a collection MongoDB, criando índice único na primeira vez."""
    global _mongo_client
    if not _MONGO_AVAILABLE:
        return None
    try:
        if _mongo_client is None:
            _mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)
        col = _mongo_client[MONGO_DB][MONGO_COL]
        col.create_index("fonte_id", unique=True, background=True)
        col.create_index("data", background=True)
        col.create_index("titulo", background=True)
        return col
    except Exception as e:
        logger.warning("[MongoDB] Não foi possível conectar: %s", e)
        return None


def salvar_mongo(dados: dict, nome_arquivo: str, file_id: str) -> bool:
    """Salva reunião no MongoDB. Retorna True se inseriu, False se já existia ou erro."""
    col = get_mongo_col()
    if col is None:
        return False
    agora = agora_utc_iso()
    doc = {
        **dados,
        "fonte_arquivo": nome_arquivo,
        "fonte_id":      file_id,
        "criado_em":     agora,
        # Campos extras para cruzamento futuro
        "_source":       "gdrive",
        "_projeto":      "agente_performance",
        "_sync_ts":      datetime.now(timezone.utc),
    }
    try:
        col.insert_one(doc)
        return True
    except MongoDuplicateKeyError:
        return False  # Já existe — silencioso
    except Exception as e:
        logger.warning("[MongoDB] Falha ao salvar '%s': %s", file_id, e)
        return False


# ── Reformatação de próximas etapas via Claude ────────────────────────────────

_NOMES_TIME = [
    "Aline Satur", "Aline",
    "Bianca Garbin", "Bianca",
    "Eros Vital", "Eros",
    "Guilherme Oliveira", "Guilherme",
    "Isabela Rodrigues", "Isabela", "Isa",
    "Matheus Ferreira", "Matheus",
    "Miguel Ticianeli", "Miguel",
]

_REFORMATAR_SYSTEM = """Você converte o texto de próximas etapas de uma reunião de agência de marketing
em tarefas estruturadas.

Time disponível (únicos que podem ser responsáveis):
- Aline Satur
- Bianca Garbin
- Eros Vital
- Guilherme Oliveira
- Isabela Rodrigues
- Matheus Ferreira
- Miguel Ticianeli

Clientes possíveis: Source Of, POD, Botica, Liv Up, Bazar, Todos

Frentes possíveis: ADS, Social, Conta, Relatoria, Planejamento, Site, Dados, Gestão

Formato de saída OBRIGATÓRIO para cada tarefa:
[Nome Completo] CLIENTE: FRENTE - Entrega: Descrição objetiva em uma frase.

Regras de formação do título:
- CLIENTE: nome exato do cliente mencionado (Source Of, POD, Botica, Liv Up, Bazar). Se for tarefa interna sem cliente, use o nome da empresa principal do contexto ou omita o prefixo.
- FRENTE: escolha a frente mais adequada ao contexto (ADS = anúncios pagos, Social = redes sociais, Conta = gestão de conta/cliente, Relatoria = relatórios, Planejamento = planejamento estratégico, Site = website/landing page, Dados = analytics/dados, Gestão = tarefas internas de gestão)
- Entrega: descrição curta da entrega em até 6 palavras, começando com verbo no infinitivo

Exemplos corretos:
[Bianca Garbin] Botica: ADS - Variação criativos dimples: Criar novas variações de criativos para campanha Dimples.
[Miguel Ticianeli] Source Of: Social - Postagens semana: Produzir conteúdo para feed da semana.
[Guilherme Oliveira] POD: Relatoria - Relatório mensal: Consolidar dados de performance do mês.
[Eros Vital] Gestão: Planejamento - Organizar reunião semanal: Agendar e preparar pauta da reunião.

NUNCA omita os colchetes. NUNCA escreva só o nome sem colchetes.

Regras adicionais:
- Use APENAS nomes do time listado acima
- Se a tarefa menciona um nome não listado (ex: Rafa, Nath, Alessandro), atribua a Miguel Ticianeli
- Se nenhum responsável for mencionado, atribua a Miguel Ticianeli
- Uma tarefa por linha, sem numeração, sem markdown
- Retorne APENAS as linhas formatadas, nada mais
- Se o texto não contiver tarefas reais, retorne exatamente: NÃO_HÁ_TAREFAS
"""


def reformatar_proximas_etapas(texto: str, anthropic_key: str = "") -> str:
    """
    Usa Claude Haiku para converter texto livre de próximas etapas
    no formato estruturado [Nome] Título: Descrição.

    Retorna o texto reformatado, ou o original se falhar.
    """
    # Se já está no formato correto, não faz nada
    if re.search(r"\[[A-Z][a-z]+ [A-Z][a-z]+\]", texto):
        return texto  # já tem [Nome Sobrenome]

    try:
        from utils.llm import gerar
        resultado = gerar(_REFORMATAR_SYSTEM, texto, max_tokens=1000)
        if resultado == "NÃO_HÁ_TAREFAS" or not resultado:
            return texto
        return resultado
    except Exception as e:
        logger.warning("[reformatar] LLM falhou: %s", e)
        return texto


# ── BigQuery ─────────────────────────────────────────────────────────────────

_bq_client = None

BQ_SCHEMA = [
    bq.SchemaField("fonte_id",        "STRING",    mode="REQUIRED") if _BQ_AVAILABLE else None,
    bq.SchemaField("data",            "DATE",      mode="NULLABLE") if _BQ_AVAILABLE else None,
    bq.SchemaField("hora",            "STRING",    mode="NULLABLE") if _BQ_AVAILABLE else None,
    bq.SchemaField("titulo",          "STRING",    mode="REQUIRED") if _BQ_AVAILABLE else None,
    bq.SchemaField("participantes",   "STRING",    mode="NULLABLE") if _BQ_AVAILABLE else None,
    bq.SchemaField("resumo",          "STRING",    mode="NULLABLE") if _BQ_AVAILABLE else None,
    bq.SchemaField("decisoes",        "STRING",    mode="NULLABLE") if _BQ_AVAILABLE else None,
    bq.SchemaField("proximas_etapas", "STRING",    mode="NULLABLE") if _BQ_AVAILABLE else None,
    bq.SchemaField("detalhes",        "STRING",    mode="NULLABLE") if _BQ_AVAILABLE else None,
    bq.SchemaField("fonte_arquivo",   "STRING",    mode="NULLABLE") if _BQ_AVAILABLE else None,
    bq.SchemaField("criado_em",       "TIMESTAMP", mode="NULLABLE") if _BQ_AVAILABLE else None,
    bq.SchemaField("atualizado_em",   "TIMESTAMP", mode="NULLABLE",
                   description="Timestamp da última versão — use para deduplicar via v_transcricao") if _BQ_AVAILABLE else None,
] if _BQ_AVAILABLE else []


def get_bq_table():
    """Retorna referência à tabela BQ, criando dataset e tabela se necessário."""
    global _bq_client
    if not _BQ_AVAILABLE:
        return None
    try:
        if _bq_client is None:
            if os.path.exists(BQ_CREDS):
                from google.oauth2 import service_account as _sa
                _creds = _sa.Credentials.from_service_account_file(
                    BQ_CREDS,
                    scopes=["https://www.googleapis.com/auth/bigquery"],
                )
                _bq_client = bq.Client(project=BQ_PROJECT, credentials=_creds)
            else:
                _bq_client = bq.Client(project=BQ_PROJECT)  # ADC fallback
        dataset_ref = _bq_client.dataset(BQ_DATASET)
        # Cria dataset se não existir
        try:
            _bq_client.get_dataset(dataset_ref)
        except Exception:
            ds = bq.Dataset(dataset_ref)
            ds.location = "US"
            _bq_client.create_dataset(ds)
            logger.info("[BigQuery] Dataset '%s' criado.", BQ_DATASET)
        # Cria tabela se não existir
        table_ref = dataset_ref.table(BQ_TABLE)
        try:
            return _bq_client.get_table(table_ref)
        except Exception:
            table = bq.Table(table_ref, schema=BQ_SCHEMA)
            _bq_client.create_table(table)
            logger.info("[BigQuery] Tabela '%s.%s' criada.", BQ_DATASET, BQ_TABLE)
            return _bq_client.get_table(table_ref)
    except Exception as e:
        logger.warning("[BigQuery] Falha ao obter tabela: %s", e)
        return None


def salvar_bigquery(dados: dict, nome_arquivo: str, file_id: str) -> bool:
    """Insere reunião no BigQuery via streaming insert. Retorna True se sucesso."""
    table = get_bq_table()
    if table is None:
        return False
    try:
        agora = agora_utc_iso()
        row = {
            "fonte_id":        file_id,
            "data":            dados.get("data") or None,
            "hora":            dados.get("hora") or None,
            "titulo":          dados.get("titulo", ""),
            "participantes":   dados.get("participantes") or None,
            "resumo":          dados.get("resumo") or None,
            "decisoes":        dados.get("decisoes") or None,
            "proximas_etapas": dados.get("proximas_etapas") or None,
            "detalhes":        dados.get("detalhes") or None,
            "fonte_arquivo":   nome_arquivo,
            "criado_em":       agora,
            "atualizado_em":   agora,  # sempre a hora desta versão
        }
        # Sem row_ids — append puro. Deduplicar via view v_transcricao
        errors = _bq_client.insert_rows_json(table, [row])
        if errors:
            logger.warning("[BigQuery] Erros ao inserir '%s': %s", file_id, errors)
            return False
        return True
    except Exception as e:
        logger.warning("[BigQuery] Exceção ao inserir '%s': %s", file_id, e)
        return False


# ── Ponto de entrada ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--todos", action="store_true", help="Reprocessa todos os arquivos")
    args = parser.parse_args()

    logger.info("Iniciando sync de reuniões do Google Drive...")

    # Orquestração delegada ao IngestaoAgent (Camada 0) — ver agents/ingestao.py.
    # Import local pra evitar import circular: agents.ingestao importa este
    # módulo de volta (sync_reunioes), o que só funciona depois que o
    # carregamento deste arquivo já terminou (ou seja, dentro de main(), não
    # no topo do arquivo).
    from agents.ingestao import IngestaoAgent
    IngestaoAgent().executar(todos=args.todos)


if __name__ == "__main__":
    main()
