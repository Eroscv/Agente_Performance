"""
scripts/metas_planilha.py — Plano semanal (cenário "otimizada") das planilhas de planejamento.

As planilhas ficam no Drive (IDs no .env: METAS_PLANILHA_SOURCE_OF, METAS_PLANILHA_POD) e são lidas
pela conta de serviço do GCP. Cada aba de plataforma tem uma linha de cabeçalho com as semanas
("S46 otimizada" no Meta; "S46" no Google) e linhas de métrica (Investimento, CPM, CTR...).
Semana N começa na segunda 28/09/2026 + 7·(N-40); "S52-S01" é a S53.

    plano("source_of")  ->  {"meta_ads": {date(2026,11,9): {"invest": 50615.0, "roas": 5.86, ...}}, ...}

ponytail: depende do layout da planilha (rótulo na 1ª célula de texto da linha, semana no cabeçalho). Mudou o layout,
o parse devolve vazio e o ciclo 3h avisa "plano não encontrado" em vez de alertar errado.
"""
from __future__ import annotations

import io
import os
import re
from datetime import date, timedelta

S40 = date(2026, 9, 28)

# cliente -> (variável com o ID do arquivo, {plataforma: (aba, sufixo da coluna da semana)})
FONTES = {
    "source_of": ("METAS_PLANILHA_SOURCE_OF", {"meta_ads": ("Meta - otimizada", "otimizada"),
                                                "google_ads": ("Google - otimizada", "")}),
    "pod_kombucha": ("METAS_PLANILHA_POD", {"meta_ads": ("Plan Meta - Otimizado", "otimizada"),
                                             "google_ads": ("Plan Google", "")}),
}

# métrica -> rótulos aceitos na 1ª coluna (comparação exata, sem caixa/espaços extras)
ROTULOS = {
    "invest": ("investimento", "investimento todas campanhas"),
    "cpm": ("cpm",),
    "ctr": ("ctr",),
    "cpc": ("cpc", "custo por clique"),
    "conv": ("taxa de conversão",),
    "ticket": ("ticket medio", "ticket médio"),
    "receita": ("receita",),
    "roas": ("roas meta", "roas google"),
}


def _semana(cab: str, sufixo: str) -> date | None:
    """'S46 otimizada' -> segunda da S46; 'S52-S01otimizada' -> S53. None se não for a coluna do cenário."""
    c = re.sub(r"\s+", " ", str(cab or "")).strip().lower()
    m = re.fullmatch(r"s(\d{2})(-s01)? ?" + re.escape(sufixo), c) if sufixo else re.fullmatch(r"s(\d{2})(-s01)?", c)
    if not m:
        return None
    n = int(m.group(1)) + (1 if m.group(2) else 0)
    return S40 + timedelta(weeks=n - 40)


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def ler_aba(ws, sufixo: str) -> dict[date, dict[str, float]]:
    linhas = [list(r) for r in ws.iter_rows(values_only=True)]
    for i, r in enumerate(linhas):
        cols = {j: s for j, v in enumerate(r) if (s := _semana(v, sufixo))}
        if len(cols) >= 4:  # linha de cabeçalho das semanas
            break
    else:
        return {}
    out: dict[date, dict[str, float]] = {s: {} for s in cols.values()}
    for r in linhas[i + 1:]:
        rot = next((re.sub(r"\s+", " ", v).strip().lower() for v in r[:4] if isinstance(v, str)), "")
        met = next((k for k, v in ROTULOS.items() if rot in v), None)
        if not met:
            continue
        for j, s in cols.items():
            if met not in out[s] and j < len(r) and (x := _num(r[j])) is not None:
                out[s][met] = x  # 1ª ocorrência vence (o Google repete o bloco do Meta mais abaixo em algumas abas)
    return {s: m for s, m in out.items() if m.get("invest")}


def parse(conteudo: bytes, abas: dict[str, tuple[str, str]]) -> dict[str, dict]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True, read_only=True)
    return {plat: ler_aba(wb[aba], suf) for plat, (aba, suf) in abas.items() if aba in wb.sheetnames}


def baixar(file_id: str) -> bytes:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    cred = service_account.Credentials.from_service_account_file(
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"], scopes=["https://www.googleapis.com/auth/drive.readonly"])
    d = build("drive", "v3", credentials=cred, cache_discovery=False)
    mime = d.files().get(fileId=file_id, fields="mimeType", supportsAllDrives=True).execute()["mimeType"]
    req = (d.files().export_media(fileId=file_id, mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
           if mime == "application/vnd.google-apps.spreadsheet" else d.files().get_media(fileId=file_id))
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, req)
    while not dl.next_chunk()[1]:
        pass
    return buf.getvalue()


def plano(cliente: str) -> dict[str, dict]:
    var, abas = FONTES[cliente]
    fid = os.getenv(var, "")
    if not fid:
        raise RuntimeError(f"{var} não configurado")
    return parse(baixar(fid), abas)


if __name__ == "__main__":
    import sys
    assert _semana("S46 otimizada", "otimizada") == date(2026, 11, 9)
    assert _semana("S52-S01otimizada", "otimizada") == date(2026, 12, 28)
    assert _semana("S46", "otimizada") is None and _semana("S46", "") == date(2026, 11, 9)
    assert _semana("Uplift", "") is None
    for caminho, cli in zip(sys.argv[1::2], sys.argv[2::2]):  # teste local: arquivo.xlsx cliente ...
        for plat, sem in parse(open(caminho, "rb").read(), FONTES[cli][1]).items():
            print(cli, plat, len(sem), "semanas")
            for s in sorted(sem)[5:8]:
                print("  ", s, {k: round(v, 4) for k, v in sem[s].items()})
