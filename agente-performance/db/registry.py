"""
db/registry.py

Registry de clientes — carrega clientes.json e expõe funções de acesso.

Para adicionar um novo cliente:
  1. Adicionar bloco em clientes.json
  2. Criar loader e builder correspondentes
  3. Pronto — nenhum script precisa ser alterado

Uso:
    from db.registry import listar_clientes, get_cliente, get_clientes_ativos
"""

import inspect
import json
import logging
from importlib import import_module
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_REGISTRY_FILE = Path(__file__).parent.parent / "clientes.json"
_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("[Registry] Falha ao carregar clientes.json: %s", e)
            _cache = {"clientes": {}}
    return _cache


def listar_clientes() -> dict:
    """Retorna todos os clientes (ativos e inativos)."""
    return _load().get("clientes", {})


def get_clientes_ativos() -> dict:
    """Retorna apenas clientes com ativo=true."""
    return {k: v for k, v in listar_clientes().items() if v.get("ativo", True)}


def get_cliente(cliente_key: str) -> dict:
    """Retorna configuração de um cliente específico. Lança KeyError se não encontrado."""
    clientes = listar_clientes()
    if cliente_key not in clientes:
        raise KeyError(f"Cliente '{cliente_key}' não encontrado. Disponíveis: {list(clientes)}")
    return clientes[cliente_key]


def get_builder(cliente_key: str, tipo: str = "semanal") -> Callable:
    """
    Importa e retorna a função builder do cliente.

    Args:
        cliente_key: chave do cliente (ex: 'source_of')
        tipo:        'semanal', 'mensal' ou 'diario'

    Returns:
        Função builder pronta para chamar
    """
    cfg = get_cliente(cliente_key)
    fn_name = cfg.get(f"builder_{tipo}")
    if not fn_name:
        raise ValueError(f"Builder '{tipo}' não configurado para '{cliente_key}'")

    # Resolve módulo por tipo
    # Prioridade: modulo_map padrão por tipo → fallback para cfg["builder"] (módulo custom do cliente)
    modulo_map = {
        "semanal": "relatorio.builder_semanal",
        "mensal":  "relatorio.builder_mensal",
        "diario":  "relatorio.builder_diario",
    }
    modulo_path = modulo_map.get(tipo)
    if not modulo_path:
        modulo_path = cfg.get("builder")
    if not modulo_path:
        raise ValueError(f"Módulo builder não resolvido para tipo '{tipo}' em '{cliente_key}'")

    modulo = import_module(modulo_path)
    if not hasattr(modulo, fn_name):
        # Função não encontrada no módulo padrão — tenta módulo custom do cliente
        alt_path = cfg.get("builder")
        if alt_path and alt_path != modulo_path:
            modulo_alt = import_module(alt_path)
            if hasattr(modulo_alt, fn_name):
                logger.debug(
                    "[Registry] Builder '%s' encontrado no módulo alternativo '%s'",
                    fn_name, alt_path,
                )
                return getattr(modulo_alt, fn_name)
        raise AttributeError(
            f"Função '{fn_name}' não encontrada em '{modulo_path}'"
            + (f" nem em '{alt_path}'" if alt_path and alt_path != modulo_path else "")
        )
    return getattr(modulo, fn_name)


def _resolve_loader_params(plat_key: str, plat_cfg: dict) -> dict:
    """
    Monta os kwargs a serem passados ao loader a partir da config da plataforma.

    Parâmetros fixos (account_id, customer_id, tabela, token_env) são lidos
    diretamente do bloco da plataforma; 'loader_params' sobrepõe os demais.
    """
    params: dict = {}

    # Parâmetros de identificação por plataforma
    if plat_cfg.get("meta_account_id"):
        params["account_id"] = plat_cfg["meta_account_id"]
    if plat_cfg.get("gads_customer_id"):
        params["customer_id"] = plat_cfg["gads_customer_id"]
    if plat_cfg.get("tabela_db"):
        params["tabela"] = plat_cfg["tabela_db"]
    if plat_cfg.get("meta_token_env"):
        params["token_env"] = plat_cfg["meta_token_env"]

    # loader_params sobrepõe (dias, append, etc.)
    extra = plat_cfg.get("loader_params") or {}
    params.update(extra)

    return params


def get_loader(cliente_key: str) -> Callable:
    """
    Importa e retorna uma função loader composta para o cliente.

    Estratégia:
      1. Se o cliente tiver 'loader' + 'loader_fn' no nível raiz → usa diretamente.
      2. Senão itera sobre 'plataformas' e agrega os loaders de cada plataforma ativa
         numa única função que os chama em sequência, passando os parâmetros corretos.

    Returns:
        Função loader composta pronta para chamar (sem argumentos)
    """
    cfg = get_cliente(cliente_key)

    # Caso 1: loader no nível raiz (legado)
    modulo_path = cfg.get("loader")
    fn_name = cfg.get("loader_fn")
    if modulo_path and fn_name:
        modulo = import_module(modulo_path)
        return getattr(modulo, fn_name)

    # Caso 2: loaders por plataforma
    plataformas = cfg.get("plataformas", {})
    loaders: list[tuple[Callable, dict]] = []  # (fn, kwargs)
    for plat_key, plat_cfg in plataformas.items():
        if not plat_cfg.get("ativo", True):
            continue
        m = plat_cfg.get("loader")
        f = plat_cfg.get("loader_fn")
        if not m or not f:
            continue
        try:
            modulo = import_module(m)
            fn = getattr(modulo, f)
            kwargs = _resolve_loader_params(plat_key, plat_cfg)
            # Filtra kwargs para apenas os que a função aceita (evita TypeError)
            sig = inspect.signature(fn)
            accepts_var_keyword = any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in sig.parameters.values()
            )
            if not accepts_var_keyword:
                accepted = set(sig.parameters)
                ignored = set(kwargs) - accepted
                if ignored:
                    logger.debug(
                        "[Registry] Loader '%s.%s' ignora params não aceitos: %s",
                        m, f, ignored,
                    )
                kwargs = {k: v for k, v in kwargs.items() if k in accepted}
            loaders.append((fn, kwargs))
            logger.debug("[Registry] Loader '%s.%s' registrado com params: %s", m, f, list(kwargs))
        except Exception as e:
            logger.warning("[Registry] Loader '%s.%s' indisponível: %s", m, f, e)

    if not loaders:
        raise ValueError(f"Loader não configurado para '{cliente_key}'")

    def _loader_composto():
        """
        Chama todos os loaders ativos do cliente em sequência com seus parâmetros.
        Loaders individuais que falham são registrados como aviso mas não interrompem
        os demais. Levanta RuntimeError apenas se TODOS os loaders falharem.
        """
        falhas: list[str] = []
        sucesso = 0
        for fn, kwargs in loaders:
            fn_label = f"{fn.__module__}.{fn.__name__}"
            try:
                fn(**kwargs)
                sucesso += 1
                logger.debug("[Registry] Loader '%s' OK", fn_label)
            except Exception as exc:
                logger.warning("[Registry] Loader '%s' falhou (continuando): %s", fn_label, exc)
                falhas.append(f"{fn_label}: {exc}")
        if sucesso == 0:
            raise RuntimeError(
                f"Todos os loaders falharam para '{cliente_key}': " + " | ".join(falhas)
            )
        if falhas:
            logger.warning(
                "[Registry] %d/%d loaders falharam para '%s': %s",
                len(falhas), len(loaders), cliente_key, " | ".join(falhas),
            )

    return _loader_composto
