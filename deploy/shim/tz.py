# ponytail: o tz.py do servidor antigo não está no GitHub; reexporta o agent/tz.py e repõe a função que faltava.
import importlib.util as _u
from datetime import datetime, timezone

_s = _u.spec_from_file_location("_agent_tz", "/home/guara/guara-performance/agente-performance/agent/tz.py")
_m = _u.module_from_spec(_s); _s.loader.exec_module(_m)
globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})


def agora_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
