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
    modulo_map = {
        "semanal": "relatorio.builder_semanal",
        "mensal":  "relatorio.builder_mensal",
        "diario":  "relatorio.builder_diario",
    }
    modulo_path = modulo_map.get(tipo) or cfg.get("builder")
    if not modulo_path:
        raise ValueError(f"Módulo builder não resolvido para tipo '{tipo}' em '{cliente_key}'")

    modulo = import_module(modulo_path)
    return getattr(modulo, fn_name)


def get_loader(cliente_key: str) -> Callable:
    """
    Importa e retorna uma função loader composta para o cliente.

    Estratégia:
      1. Se o cliente tiver 'loader' + 'loader_fn' no nível raiz → usa diretamente.
      2. Senão itera sobre 'plataformas' e agrega os loaders de cada plataforma ativa
         numa única função que os chama em sequência.

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
    loaders: list[Callable] = []
    for plat_key, plat_cfg in plataformas.items():
        if not plat_cfg.get("ativo", True):
            continue
        m = plat_cfg.get("loader")
        f = plat_cfg.get("loader_fn")
        if not m or not f:
            continue
        try:
            modulo = import_module(m)
            loaders.append(getattr(modulo, f))
        except Exception as e:
            logger.warning("[Registry] Loader '%s.%s' indisponível: %s", m, f, e)

    if not loaders:
        raise ValueError(f"Loader não configurado para '{cliente_key}'")

    def _loader_composto():
        """Chama todos os loaders ativos do cliente em sequência."""
        for fn in loaders:
            fn()

    return _loader_composto
