"""Pacote da GUI Qt do Graphics Resources Manager.

Mantido SEM imports de PySide6/PIL no nivel de pacote: o launcher precisa
poder importar `grm.deps` e rodar `ensure_dependencies()` antes que qualquer
modulo que dependa de Qt/Pillow seja carregado.
"""
