"""Bootstrap de dependencias. NAO importar PySide6/PIL aqui."""

from __future__ import annotations

import importlib.util
import subprocess
import sys

REQUIRED_PACKAGES = {
    "PIL": "Pillow",
    "PySide6": "PySide6",
    "google.protobuf": "protobuf",
    "grpc_tools": "grpcio-tools",
}


def _module_available(module_name: str) -> bool:
    # find_spec lanca ModuleNotFoundError quando o pacote pai (ex.: "google")
    # nao existe, em vez de retornar None. Tratamos isso como ausente.
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def ensure_dependencies() -> None:
    missing = [pip for mod, pip in REQUIRED_PACKAGES.items() if not _module_available(mod)]
    if not missing:
        return
    subprocess.run([sys.executable, "-m", "pip", "install", *missing], check=True)
