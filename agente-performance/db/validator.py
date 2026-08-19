"""
db/validator.py

Validação de integridade dos dados após carga do CSV.
Roda automaticamente em load_csv() e pode ser chamado manualmente.

Checks implementados:
  1. Duplicatas (campanha + data_inicio)
  2. Investimento zero com compras/receita > 0 (atribuição sem custo)
  3. valor_compras > 0 com roas_compras NULL (ROAS ausente sem justificativa)
  4. Valores negativos em campos quantitativos
  5. Datas fora de ordem (data_fim < data_inicio)
  6. Cobertura de dados (alerta se < 7 dias ou banco vazio)
  7. Consistência ROAS x valor_compras/investimento (divergência > 5%)

Retorna ValidationReport com lista de issues classificados por severidade:
  - ERROR:   dado incorreto, pode impactar relatórios
  - WARNING: suspeito, mas pode ter explicação legítima
  - INFO:    informativo, sem impacto direto
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from db.connections import query_fed as query, query_fed_one as query_one, LEGACY_DB_PATH as DB_PATH

logger = logging.getLogger(__name__)

Severity = Literal["ERROR", "WARNING", "INFO"]


@dataclass
class ValidationIssue:
    severity: Severity
    check:    str
    message:  str
    count:    int = 0
    samples:  list[dict] = field(default_factory=list)

    def __str__(self) -> str:
        sample_str = ""
        if self.samples:
            s = self.samples[0]
            sample_str = f" | ex: {s}"
        return f"[{self.severity}] {self.check}: {self.message} (n={self.count}){sample_str}"


@dataclass
class ValidationReport:
    issues:  list[ValidationIssue] = field(default_factory=list)
    passed:  list[str]             = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "ERROR"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "WARNING"]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        lines = [
            f"{'✅' if self.ok else '❌'} Validação: {len(self.errors)} erro(s), "
            f"{len(self.warnings)} aviso(s), {len(self.passed)} check(s) OK"
        ]
        for issue in self.issues:
            lines.append(f"  {issue}")
        if self.passed:
            lines.append(f"  ✔ Passou: {', '.join(self.passed)}")
        return "\n".join(lines)

    def log(self) -> None:
        for issue in self.errors:
            logger.error("[validator] %s", issue)
        for issue in self.warnings:
            logger.warning("[validator] %s", issue)
        for check in self.passed:
            logger.debug("[validator] ✔ %s OK", check)


# ── Checks individuais ────────────────────────────────────────────────────────

def _check_duplicatas(report: ValidationReport) -> None:
    """Campanhas com mais de uma linha por dia — dobra métricas em SUM()."""
    rows = query("""
        SELECT campanha, data_inicio, COUNT(*) AS n
        FROM campanhas
        GROUP BY campanha, data_inicio
        HAVING COUNT(*) > 1
        ORDER BY n DESC
        LIMIT 5
    """)
    total = query_one("""
        SELECT COUNT(*) AS n FROM (
            SELECT campanha, data_inicio FROM campanhas
            GROUP BY campanha, data_inicio HAVING COUNT(*) > 1
        )
    """) or {}
    count = int(total.get("n") or 0)

    if count > 0:
        report.issues.append(ValidationIssue(
            severity="ERROR",
            check="duplicatas",
            message=f"{count} par(es) (campanha, data) duplicados — SUM() vai inflar métricas",
            count=count,
            samples=[dict(r) for r in rows[:3]],
        ))
    else:
        report.passed.append("duplicatas")


def _check_investimento_zero_com_conversao(report: ValidationReport) -> None:
    """Investimento = 0 mas há compras ou receita — atribuição sem custo."""
    rows = query("""
        SELECT campanha, data_inicio, investimento, compras, valor_compras
        FROM campanhas
        WHERE investimento = 0
          AND (compras > 0 OR valor_compras > 0)
        LIMIT 5
    """)
    total = query_one("""
        SELECT COUNT(*) AS n FROM campanhas
        WHERE investimento = 0 AND (compras > 0 OR valor_compras > 0)
    """) or {}
    count = int(total.get("n") or 0)

    if count > 0:
        report.issues.append(ValidationIssue(
            severity="WARNING",
            check="invest_zero_com_conversao",
            message=f"{count} linha(s) com investimento=0 mas compras/receita > 0 — pode ser atribuição cross-campaign",
            count=count,
            samples=[dict(r) for r in rows[:3]],
        ))
    else:
        report.passed.append("invest_zero_com_conversao")


def _check_roas_ausente_com_receita(report: ValidationReport) -> None:
    """valor_compras > 0 mas roas_compras NULL — ROAS não reportado pelo Meta."""
    total = query_one("""
        SELECT COUNT(*) AS n FROM campanhas
        WHERE valor_compras > 0 AND roas_compras IS NULL
    """) or {}
    count = int(total.get("n") or 0)

    if count > 0:
        report.issues.append(ValidationIssue(
            severity="WARNING",
            check="roas_ausente_com_receita",
            message=f"{count} linha(s) com valor_compras > 0 mas roas_compras NULL — Meta não reportou ROAS",
            count=count,
        ))
    else:
        report.passed.append("roas_ausente_com_receita")


def _check_valores_negativos(report: ValidationReport) -> None:
    """Campos quantitativos com valores negativos."""
    campos = ["investimento", "compras", "valor_compras", "impressoes", "cliques_link", "alcance"]
    negatives = []
    for col in campos:
        r = query_one(f"SELECT COUNT(*) AS n FROM campanhas WHERE {col} < 0") or {}
        n = int(r.get("n") or 0)
        if n > 0:
            negatives.append(f"{col}:{n}")

    if negatives:
        report.issues.append(ValidationIssue(
            severity="ERROR",
            check="valores_negativos",
            message=f"Valores negativos encontrados: {', '.join(negatives)}",
            count=len(negatives),
        ))
    else:
        report.passed.append("valores_negativos")


def _check_datas_invalidas(report: ValidationReport) -> None:
    """data_fim < data_inicio ou datas nulas em linhas com investimento."""
    fora_ordem = query_one(
        "SELECT COUNT(*) AS n FROM campanhas WHERE data_fim < data_inicio"
    ) or {}
    nulas = query_one(
        "SELECT COUNT(*) AS n FROM campanhas WHERE (data_inicio IS NULL OR data_fim IS NULL) AND investimento > 0"
    ) or {}

    issues_found = False
    if int(fora_ordem.get("n") or 0) > 0:
        report.issues.append(ValidationIssue(
            severity="ERROR",
            check="datas_invalidas",
            message=f"{fora_ordem['n']} linha(s) com data_fim < data_inicio",
            count=int(fora_ordem["n"]),
        ))
        issues_found = True
    if int(nulas.get("n") or 0) > 0:
        report.issues.append(ValidationIssue(
            severity="ERROR",
            check="datas_nulas",
            message=f"{nulas['n']} linha(s) com data nula e investimento > 0",
            count=int(nulas["n"]),
        ))
        issues_found = True

    if not issues_found:
        report.passed.append("datas_invalidas")


def _check_cobertura(report: ValidationReport) -> None:
    """Banco vazio ou com poucos dias de dados."""
    dr = query_one("""
        SELECT
            COUNT(*) AS total,
            COUNT(DISTINCT data_inicio) AS dias_unicos,
            MIN(data_inicio) AS data_min,
            MAX(data_fim)    AS data_max
        FROM campanhas
        WHERE investimento > 0
    """) or {}

    total = int(dr.get("total") or 0)
    dias  = int(dr.get("dias_unicos") or 0)

    if total == 0:
        report.issues.append(ValidationIssue(
            severity="ERROR",
            check="cobertura",
            message="Banco vazio — nenhuma linha com investimento > 0",
        ))
    elif dias < 7:
        report.issues.append(ValidationIssue(
            severity="WARNING",
            check="cobertura",
            message=f"Apenas {dias} dia(s) de dados ({dr.get('data_min')} → {dr.get('data_max')}) — relatórios podem ser inconclusivos",
            count=dias,
        ))
    else:
        report.passed.append(f"cobertura ({dias} dias, {dr.get('data_min')} → {dr.get('data_max')})")


def _check_consistencia_roas(report: ValidationReport) -> None:
    """
    Divergência > 5% entre roas_compras e (valor_compras / investimento).

    Uma divergência esperada pode acontecer quando o Meta usa janela de atribuição
    diferente — por isso é WARNING, não ERROR.
    """
    rows = query("""
        SELECT
            campanha,
            data_inicio,
            ROUND(roas_compras, 4)                                     AS roas_meta,
            ROUND(valor_compras / NULLIF(investimento, 0), 4)          AS roas_calc,
            ROUND(
                ABS(roas_compras - valor_compras / NULLIF(investimento, 0))
                / roas_compras * 100,
            2) AS diff_pct
        FROM campanhas
        WHERE roas_compras IS NOT NULL
          AND roas_compras > 0
          AND investimento > 0
          AND valor_compras > 0
          AND ABS(roas_compras - valor_compras / NULLIF(investimento, 0))
              / roas_compras > 0.05
        ORDER BY diff_pct DESC
        LIMIT 5
    """)
    total = query_one("""
        SELECT COUNT(*) AS n FROM campanhas
        WHERE roas_compras IS NOT NULL AND roas_compras > 0
          AND investimento > 0 AND valor_compras > 0
          AND ABS(roas_compras - valor_compras / NULLIF(investimento, 0))
              / roas_compras > 0.05
    """) or {}
    count = int(total.get("n") or 0)

    if count > 0:
        report.issues.append(ValidationIssue(
            severity="WARNING",
            check="consistencia_roas",
            message=(
                f"{count} linha(s) com divergência > 5% entre roas_compras e "
                f"valor_compras/investimento — pode ser janela de atribuição diferente no Meta"
            ),
            count=count,
            samples=[dict(r) for r in rows[:3]],
        ))
    else:
        report.passed.append("consistencia_roas")


# ── Orquestrador ──────────────────────────────────────────────────────────────

def validar(db_path: Path = DB_PATH) -> ValidationReport:
    """
    Executa todos os checks e retorna um ValidationReport.

    Uso:
        from db.validator import validar
        report = validar()
        report.log()
        print(report.summary())
    """
    report = ValidationReport()

    checks = [
        _check_duplicatas,
        _check_investimento_zero_com_conversao,
        _check_roas_ausente_com_receita,
        _check_valores_negativos,
        _check_datas_invalidas,
        _check_cobertura,
        _check_consistencia_roas,
    ]

    for check_fn in checks:
        try:
            check_fn(report)
        except Exception as e:
            logger.exception("[validator] Erro no check %s: %s", check_fn.__name__, e)
            report.issues.append(ValidationIssue(
                severity="ERROR",
                check=check_fn.__name__,
                message=f"Exceção durante validação: {e}",
            ))

    report.log()
    return report


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")
    r = validar()
    print(r.summary())
