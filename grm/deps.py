"""Dependency bootstrap. DO NOT import PySide6/PIL here."""

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
    # find_spec raises ModuleNotFoundError when the parent package (e.g. "google")
    # does not exist, instead of returning None. We treat this as missing.
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def ensure_dependencies() -> None:
    missing = [pip for mod, pip in REQUIRED_PACKAGES.items() if not _module_available(mod)]
    if not missing:
        return
    subprocess.run([sys.executable, "-m", "pip", "install", *missing], check=True)
