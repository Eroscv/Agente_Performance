"""
db/loader.py

Carrega o CSV de campanhas no banco SQLite.
- Normaliza cabeçalhos usando COLUMN_MAP
- Converte tipos (datas, números)
- Deduplica por (campanha, data_inicio): para o mesmo par, mescla as linhas
  somando campos aditivos (investimento, compras, receita, impressoes…) e
  preservando campos de ratio (roas, ctr, cpc…) da linha com maior investimento.
- Idempotente: limpa e recarrega a cada chamada.

Uso:
    python -m db.loader
    ou importar load_csv() em outro módulo
"""

import csv
import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional
import sqlite3

from db.schema import COLUMN_MAP, CREATE_TABLE_SQL

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
CSV_PATH = BASE_DIR / "data" / "campanhas.csv"
DB_PATH  = BASE_DIR / "data" / "campanhas.db"

# ── Classificação das colunas para deduplicação ───────────────────────────────

# Campos que devem ser SOMADOS quando há mais de uma linha por (campanha, data)
ADDITIVE_COLS = {
    "investimento", "compras", "compras_app", "compras_offline", "compras_site",
    "compras_meta", "valor_compras", "impressoes", "alcance", "visualizacoes",
    "visualizadores", "cliques_todos", "cliques_foto", "cliques_loja",
    "cliques_link", "cliques_link_unicos", "cliques_unicos_todos",
    "reproducoes_video", "thruplays", "video_3s", "video_2s", "video_2s_unique",
    "visualizacoes_landing", "seguidores_instagram", "curtidas_facebook",
    "checkins", "comentarios", "compartilhamentos", "engajamento_pagina",
    "engajamentos_post", "lembretes_evento_ig", "entradas_grupo", "salvamentos",
    "reacoes", "participacoes_evento", "msg_inscricoes", "msg_reconexoes",
    "conversas_iniciadas", "conversas_respondidas_7d", "msg_boas_vindas",
    "adicoes_carrinho", "wishlist", "checkout_iniciado", "info_pagamento",
    "pesquisas", "views_conteudo", "valor_views_conteudo", "valor_pesquisas",
    "valor_checkout", "valor_info_pagamento", "valor_carrinho", "valor_wishlist",
    "resultados", "valor_resultado",
}

# Campos de ratio/média: preservar da linha com MAIOR investimento
# (todos os demais campos numéricos não listados em ADDITIVE_COLS)
# Campos de texto/data: sempre da linha com maior investimento
RATIO_SIGNAL_COL = "investimento"  # tiebreaker


# ── Conversores ───────────────────────────────────────────────────────────────

def _to_float(val: str) -> Optional[float]:
    if not val or val.strip() == "":
        return None
    try:
        return float(val.replace(",", "."))
    except ValueError:
        return None


def _to_date(val: str) -> Optional[str]:
    if not val or val.strip() == "":
        return None
    return val.strip()


# ── Deduplicação ──────────────────────────────────────────────────────────────

def _parse_row(row: dict, fieldnames: list[str]) -> dict:
    """Converte uma linha do CSV para dict com colunas SQL tipadas."""
    date_cols = {"data_inicio", "data_fim"}
    text_cols = {"campanha", "indicador_resultado", "indicador_valor_resultado"}
    result = {}
    for orig_header in fieldnames:
        sql_col = COLUMN_MAP.get(orig_header)
        if sql_col is None:
            continue
        raw = row.get(orig_header, "")
        if sql_col in date_cols:
            result[sql_col] = _to_date(raw)
        elif sql_col in text_cols:
            result[sql_col] = raw.strip() if raw else None
        else:
            result[sql_col] = _to_float(raw)
    return result


def _merge_rows(rows: list[dict]) -> dict:
    """
    Mescla N linhas do mesmo (campanha, data_inicio) em uma única linha.

    Estratégia:
      - Campos aditivos  → soma (ignorando None)
      - Campos de ratio  → valor da linha com maior investimento
      - Campos de texto/data → valor da linha com maior investimento
    """
    if len(rows) == 1:
        return rows[0]

    # Linha âncora: maior investimento (fonte dos campos de ratio)
    anchor = max(rows, key=lambda r: r.get(RATIO_SIGNAL_COL) or 0)
    merged = dict(anchor)

    # Somar campos aditivos
    for col in ADDITIVE_COLS:
        total = None
        for r in rows:
            v = r.get(col)
            if v is not None:
                total = (total or 0) + v
        merged[col] = total

    return merged


def _deduplicate(raw_rows: list[dict]) -> tuple[list[dict], int]:
    """
    Agrupa por (campanha, data_inicio) e mescla duplicatas.

    Retorna (linhas_deduplicadas, num_duplicatas_removidas).
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in raw_rows:
        key = (row.get("campanha"), row.get("data_inicio"))
        groups[key].append(row)

    deduped = []
    removed = 0
    for key, group in groups.items():
        if len(group) > 1:
            removed += len(group) - 1
            deduped.append(_merge_rows(group))
        else:
            deduped.append(group[0])

    if removed:
        logger.info("[loader] Deduplicação: %d linha(s) removida(s) de %d grupos com duplicata",
                    removed, sum(1 for g in groups.values() if len(g) > 1))

    return deduped, removed


# ── Loader principal ──────────────────────────────────────────────────────────

def load_csv(csv_path: Path = CSV_PATH, db_path: Path = DB_PATH) -> int:
    """
    Carrega o CSV no SQLite com deduplicação automática.
    Recria a tabela do zero (truncate + insert).
    Retorna número de linhas inseridas (após deduplicação).
    """
    logger.info("Iniciando carga: %s → %s", csv_path, db_path)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()

    # Cria schema
    for stmt in CREATE_TABLE_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)

    # Limpa dados anteriores
    cur.execute("DELETE FROM campanhas")

    # ── Leitura completa do CSV ───────────────────────────────────────────────
    raw_rows: list[dict] = []
    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for row in reader:
            parsed = _parse_row(row, fieldnames)
            if parsed:
                raw_rows.append(parsed)

    logger.info("[loader] %d linha(s) lidas do CSV", len(raw_rows))

    # ── Deduplicação ──────────────────────────────────────────────────────────
    clean_rows, n_removed = _deduplicate(raw_rows)
    if n_removed:
        logger.warning("[loader] %d duplicata(s) removida(s) — %d linha(s) limpas prontas para inserção",
                       n_removed, len(clean_rows))

    # ── Inserção em batch ─────────────────────────────────────────────────────
    # Determina colunas presentes (interseção entre COLUMN_MAP e as do CSV)
    valid_cols = [c for c in COLUMN_MAP.values() if any(c in r for r in clean_rows[:1])]
    # Garante ordem consistente usando a primeira linha como referência
    if clean_rows:
        valid_cols = [c for c in clean_rows[0].keys()]

    col_list     = ", ".join(valid_cols)
    placeholders = ", ".join(["?"] * len(valid_cols))

    BATCH_SIZE = 500
    inserted   = 0
    batch      = []

    for row in clean_rows:
        values = tuple(row.get(col) for col in valid_cols)
        batch.append(values)
        if len(batch) >= BATCH_SIZE:
            cur.executemany(
                f"INSERT INTO campanhas ({col_list}) VALUES ({placeholders})",
                batch,
            )
            inserted += len(batch)
            batch = []

    if batch:
        cur.executemany(
            f"INSERT INTO campanhas ({col_list}) VALUES ({placeholders})",
            batch,
        )
        inserted += len(batch)

    conn.commit()
    conn.close()
    logger.info("[loader] Carga concluída: %d linha(s) inseridas (%d duplicata(s) removidas)",
                inserted, n_removed)

    # ── Validação automática pós-carga ────────────────────────────────────────
    try:
        from db.validator import validar
        report = validar(db_path)
        if not report.ok:
            logger.error("[loader] Validação pós-carga com ERRORs:\n%s", report.summary())
        else:
            logger.info("[loader] Validação pós-carga OK — %d aviso(s)", len(report.warnings))
    except Exception as e:
        logger.warning("[loader] Falha ao rodar validação pós-carga: %s", e)

    return inserted


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    n = load_csv()
    print(f"✅ {n} linhas inseridas em {DB_PATH}")
    from db.validator import validar
    report = validar()
    print(report.summary())
