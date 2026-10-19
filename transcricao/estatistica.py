"""
transcricao/estatistica.py

Intervalo de Confiança de Wilson para proporções com n pequeno.

Extraído de metricas_alertas.py (onde já era usado pra decidir SLA de
alertas raros) pra ser reutilizado por qualquer subagente que precise
avaliar taxa de erro/sucesso sem distorcer o resultado com poucos
eventos — ver "Metodologia aplicada — Subagente de Transcrição" (Notion):
"o IC de Wilson é o ponto mais maduro que o sistema já tem — vale copiar
essa lógica de volta, não o contrário."

Nenhuma dependência além da stdlib — pode ser importado por qualquer
camada sem arrastar google-cloud-bigquery, anthropic, etc.
"""
import math


def ic_wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """
    Intervalo de confiança de Wilson para proporção.
    Retorna (lower, upper) como frações 0-1.
    Correto para n pequeno (ao contrário do IC normal que falha com n<30).
    """
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def taxa_erro(ok: int, erro: int, z: float = 1.96) -> dict:
    """
    Empacota uma taxa de erro + IC de Wilson a partir de contagens brutas
    de eventos numa janela (não de uma % já calculada — o IC de Wilson
    precisa de k e n, não de uma taxa pronta).

    Retorna: {ok, erro, n, taxa_erro, ic_wilson_low, ic_wilson_high}
    """
    n = ok + erro
    taxa = erro / n if n else 0.0
    ic_low, ic_high = ic_wilson(erro, n, z)
    return {
        "ok": ok,
        "erro": erro,
        "n": n,
        "taxa_erro": round(taxa, 4),
        "ic_wilson_low": round(ic_low, 4),
        "ic_wilson_high": round(ic_high, 4),
    }
