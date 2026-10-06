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
    row = conn.execute(
        "SELECT id FROM reunioes WHERE fonte_id = ?", (file_id,)
    ).fetchone()
    return row is not None


def salvar_log(conn, file_id, nome, status, mensagem):
    conn.execute(
        "INSERT INTO sync_log (fonte_id, nome, status, mensagem, criado_em) VALUES (?,?,?,?,?)",
        (file_id, nome, status, mensagem, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


# ── Extração de texto ─────────────────────────────────────────────────────────

def extrair_texto(docx_bytes: bytes) -> str:
    """Extrai texto puro do .docx."""
    import io
    doc = Document(io.BytesIO(docx_bytes))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


# ── Parser de conteúdo ────────────────────────────────────────────────────────

def _entre_secoes(texto: str, inicio: str, fins: list[str]) -> str:
    """Extrai bloco de texto entre seções."""
    pattern = rf"{re.escape(inicio)}\n(.*?)(?={'|'.join(re.escape(f) for f in fins)}|$)"
    m = re.search(pattern, texto, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def parsear_reuniao(texto: str, nome_arquivo: str) -> dict:
    """
    Extrai estrutura da reunião do texto bruto do .docx do Gemini.
    Tenta extrair: data, hora, título, participantes, resumo,
    decisões, próximas etapas e detalhes.
    """
    linhas = texto.splitlines()

    # ── Título (segunda linha não vazia após "Observações") ──
    titulo = nome_arquivo.split(" - ")[0].strip() if " - " in nome_arquivo else nome_arquivo

    # ── Data e hora ──
    data_str = ""
    hora_str = ""
    # Tenta pegar do nome do arquivo: "2026_09_28 09_00"
    m_data = re.search(r"(\d{4}_\d{2}_\d{2})\s+(\d{2}_\d{2})", nome_arquivo)
    if m_data:
        data_str = m_data.group(1).replace("_", "-")
        hora_str = m_data.group(2).replace("_", ":")
    else:
        # Tenta pegar do corpo (ex: "set. 28, 2026")
        m_data2 = re.search(r"(\w{3})\.\s+(\d{1,2}),\s+(\d{4})", texto)
        if m_data2:
            meses = {
                "jan": "01", "fev": "02", "mar": "03", "abr": "04",
                "mai": "05", "jun": "06", "jul": "07", "ago": "08",
                "set": "09", "out": "10", "nov": "11", "dez": "12",
            }
            mes = meses.get(m_data2.group(1).lower(), "01")
            data_str = f"{m_data2.group(3)}-{mes}-{m_data2.group(2).zfill(2)}"

    # ── Participantes (linha após "convidado") ──
    participantes = ""
    for i, linha in enumerate(linhas):
        if linha.lower().startswith("convidado"):
            participantes = linha.replace("convidado", "").strip()
            break

    # ── Resumo ──
    resumo = _entre_secoes(texto, "Resumo", ["Integração", "Decisões", "Próximas", "Detalhes", "Anexos"])

    # ── Decisões ──
    decisoes = _entre_secoes(texto, "Decisões", ["Próximas etapas", "Detalhes", "Revise"])

    # ── Próximas etapas ──
    proximas = _entre_secoes(texto, "Próximas etapas", ["Detalhes", "Revise", "Como está"])

    # ── Detalhes ──
    detalhes = _entre_secoes(texto, "Detalhes", ["Revise", "Como está", "Confira"])

    return {
        "data":            data_str or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "hora":            hora_str,
        "titulo":          titulo,
        "participantes":   participantes,
        "resumo":          resumo,
        "decisoes":        decisoes,
        "proximas_etapas": proximas,
        "detalhes":        detalhes,
    }


def salvar_reuniao(conn: sqlite3.Connection, dados: dict, nome_arquivo: str, file_id: str):
    agora = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR IGNORE INTO reunioes
          (data, hora, titulo, participantes, resumo, decisoes,
           proximas_etapas, detalhes, fonte_arquivo, fonte_id, criado_em)
        VALUES
          (:data, :hora, :titulo, :participantes, :resumo, :decisoes,
           :proximas_etapas, :detalhes, :fonte_arquivo, :fonte_id, :criado_em)
    """, {**dados, "fonte_arquivo": nome_arquivo, "fonte_id": file_id, "criado_em": agora})
    conn.commit()


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
    agora = datetime.now(timezone.utc).isoformat()
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
        agora = datetime.now(timezone.utc).isoformat()
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

    service = get_drive_service()
    conn    = get_conn()

    arquivos = listar_docx(service)
    logger.info(f"Arquivos .docx encontrados no Drive: {len(arquivos)}")

    novos = 0
    pulados = 0
    erros = 0

    for arq in arquivos:
        file_id = arq["id"]
        nome    = arq["name"]

        if not args.todos and ja_processado(conn, file_id):
            logger.info(f"  ⏭  Já processado: {nome}")
            pulados += 1
            continue

        logger.info(f"  📥 Processando: {nome}")

        try:
            conteudo = baixar_docx(service, file_id, arq.get("mimeType", MIME_DOCX))
            texto    = extrair_texto(conteudo)
            dados    = parsear_reuniao(texto, nome)
            salvar_reuniao(conn, dados, nome, file_id)

            # Dual-write → MongoDB
            mongo_ok = salvar_mongo(dados, nome, file_id)
            mongo_status = "✅ Mongo" if mongo_ok else "⚡ Mongo já existia"

            # Triple-write → BigQuery
            bq_ok = salvar_bigquery(dados, nome, file_id)
            bq_status = "✅ BQ" if bq_ok else "⚠️ BQ falhou" if _BQ_AVAILABLE else "BQ indisponível"

            logger.info(f"  ✅ SQLite | {mongo_status} | {bq_status} | {dados['titulo']} | {dados['data']} {dados['hora']}")
            salvar_log(conn, file_id, nome, "ok", f"Salvo: {dados['titulo']}")
            novos += 1

        except Exception as e:
            logger.error(f"  ❌ Erro em {nome}: {e}")
            salvar_log(conn, file_id, nome, "erro", str(e))
            erros += 1

    conn.close()
    if _mongo_client:
        _mongo_client.close()
    if _BQ_AVAILABLE and _bq_client is not None:
        try:
            _bq_client.close()
        except Exception:
            pass
    logger.info(f"\nSync concluído — novos: {novos} | pulados: {pulados} | erros: {erros}")


if __name__ == "__main__":
    main()
