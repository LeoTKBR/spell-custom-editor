"""Permite `python -m grm`. Garante dependencias antes de importar a app."""

from __future__ import annotations

from .deps import ensure_dependencies

ensure_dependencies()

from .app import main  # noqa: E402

if __name__ == "__main__":
    main()
