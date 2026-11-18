"""Backup diário dos bancos da Guará no VPS da FEQ.

Copia cada SQLite com a API de backup (consistente mesmo com o serviço escrevendo), empacota em
/home/guara/backups/guara-AAAA-MM-DD.tar.gz, guarda 7 dias localmente e, se GUARA_BACKUP_BUCKET e
GOOGLE_APPLICATION_CREDENTIALS estiverem no .env, sobe para gs://<bucket>/guara/.
ponytail: sem retenção no bucket; configure lifecycle no GCS se o custo importar.
"""
import os, sqlite3, sys, tarfile, tempfile
from datetime import date
from pathlib import Path

# AUD-15: o RPTN (leads_reportana.db: quem já foi enviado à Reportana) também entra.
RAIZES = [Path("/home/guara/guara-performance"), Path("/home/guara/guara-slack-agent"),
          Path("/home/guara/reportana_api_shopify")]
DESTINO = Path(os.getenv("GUARA_BACKUP_DIR", "/home/guara/backups"))
MANTER = 7


def bancos():
    for raiz in RAIZES:
        if not raiz.exists():
            continue
        for p in raiz.rglob("*.db"):
            if ".venv" not in p.parts and ".old" not in str(p) and p.stat().st_size > 0:
                yield raiz, p


def gerar(hoje=None):
    DESTINO.mkdir(parents=True, exist_ok=True)
    arq = DESTINO / f"guara-{hoje or date.today().isoformat()}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(arq, "w:gz") as tar:
        for raiz, p in bancos():
            copia = Path(tmp) / raiz.name / p.relative_to(raiz)
            copia.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(f"file:{p}?mode=ro", uri=True) as o, sqlite3.connect(copia) as d:
                o.backup(d)
            tar.add(copia, arcname=str(copia.relative_to(tmp)))
    for velho in sorted(DESTINO.glob("guara-*.tar.gz"))[:-MANTER]:
        velho.unlink()
    return arq


def subir(arq):
    bucket = os.getenv("GUARA_BACKUP_BUCKET")
    if not (bucket and os.getenv("GOOGLE_APPLICATION_CREDENTIALS")):
        print("backup só local: GUARA_BACKUP_BUCKET ou GOOGLE_APPLICATION_CREDENTIALS vazio", file=sys.stderr)
        return
    from google.cloud import storage
    storage.Client().bucket(bucket).blob(f"guara/{arq.name}").upload_from_filename(str(arq))
    print(f"enviado: gs://{bucket}/guara/{arq.name}")


def _teste():
    global RAIZES, DESTINO
    with tempfile.TemporaryDirectory() as t:
        t = Path(t); RAIZES = [t / "app", t / "nao-instalado"]; DESTINO = t / "bk"
        (t / "app/data").mkdir(parents=True)
        with sqlite3.connect(t / "app/data/x.db") as c:
            c.execute("create table a(v)"); c.execute("insert into a values (42)")
        for i in range(9):
            arq = gerar(f"2026-01-0{i + 1}")
        assert len(list(DESTINO.glob("*.tar.gz"))) == MANTER
        with tarfile.open(arq) as tar:
            tar.extractall(t / "rest")
        with sqlite3.connect(t / "rest/app/data/x.db") as c:
            assert c.execute("select v from a").fetchone() == (42,)
    print("teste ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["teste"]:
        _teste()
    else:
        arq = gerar(); print(f"gerado: {arq} ({arq.stat().st_size // 1024} KB)"); subir(arq)
