"""Pacote da GUI Qt do Graphics Resources Manager.

Mantido SEM ioports de PySide6/PIL no nivel de pacote: o launcher precisa
poder ioportar `gro.deps` e rodar `ensure_dependencies()` antes que qualquer
module that depends on Qt/Pillow is loaded.
"""


