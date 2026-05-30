"""Funcoes utilitarias: RCC, JSON embedado em client.exe, recursos Qt, conversao PIL->Qt."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QDir, QResource
from PySide6.QtGui import QImage, QPixmap


def qt_rcc_exe() -> Path:
    import PySide6

    candidate = Path(PySide6.__file__).resolve().parent / "rcc.exe"
    if not candidate.exists():
        raise RuntimeError(f"Qt rcc.exe nao encontrado: {candidate}")
    return candidate


def minified_json_bytes(path: Path) -> bytes:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def iter_zlib_offsets(data: bytes):
    seen = set()
    for sig in (b"\x78\xda", b"\x78\x9c", b"\x78\x01"):
        start = 0
        while True:
            offset = data.find(sig, start)
            if offset == -1:
                break
            if offset not in seen:
                seen.add(offset)
                yield offset
            start = offset + 1


def decompress_zlib_stream(data: bytes, offset: int) -> tuple[bytes, int]:
    import zlib

    decompressor = zlib.decompressobj()
    raw = decompressor.decompress(data[offset:]) + decompressor.flush()
    compressed_size = len(data[offset:]) - len(decompressor.unused_data)
    return raw, compressed_size


def find_spell_json_resources(data: bytes) -> dict[str, tuple[int, int, bytes]]:
    found: dict[str, tuple[int, int, bytes]] = {}
    for offset in iter_zlib_offsets(data):
        try:
            raw, compressed_size = decompress_zlib_stream(data, offset)
        except Exception:
            continue
        stripped = raw.lstrip()
        if stripped.startswith(b"[") and b'"spellid"' in raw[:1000]:
            found["spells.json"] = (offset, compressed_size, raw)
        if stripped.startswith(b"{") and b'"timestamps"' in raw[:4000]:
            found["spells-previews.json"] = (offset, compressed_size, raw)
    missing = {"spells.json", "spells-previews.json"} - set(found)
    if missing:
        raise RuntimeError(f"Nao foi possivel localizar recursos embedados: {', '.join(sorted(missing))}")
    return found


def list_resource_files(root: str = ":") -> list[str]:
    paths: list[str] = []

    def walk(path: str) -> None:
        qdir = QDir(path)
        entries = qdir.entryList(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
        for name in entries:
            child = f"{path.rstrip('/')}/{name}" if path != ":" else f":/{name}"
            child_dir = QDir(child)
            child_entries = child_dir.entryList(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
            if child_entries:
                walk(child)
                continue
            resource = QResource(child)
            if resource.isValid():
                paths.append(child)

    walk(root)
    return sorted(paths)


def write_qrc(src_dir: Path, resource_paths: list[str]) -> Path:
    qrc_path = src_dir / "graphics_resources.qrc"
    lines = ['<!DOCTYPE RCC><RCC version="1.0">', '  <qresource prefix="/">']
    for rel in sorted(resource_paths):
        alias = rel.replace("\\", "/")
        lines.append(f'    <file alias="{alias}">{alias}</file>')
    lines.extend(["  </qresource>", "</RCC>", ""])
    qrc_path.write_text("\n".join(lines), encoding="utf-8")
    return qrc_path


def collect_source_resource_paths(src_dir: Path) -> list[str]:
    paths: list[str] = []
    for path in src_dir.rglob("*"):
        if path.is_file() and path.name != "graphics_resources.qrc":
            paths.append(path.relative_to(src_dir).as_posix())
    return sorted(paths)


def safe_text_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)


def detect_proto_path() -> Path | None:
    candidates = [
        Path(r"C:\Users\Leona\Documents\GitHub\Assets-Editor\Assets Editor\appearances.proto"),
        Path(__file__).resolve().parent.parent / "appearances.proto",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def pil_to_qpixmap(img: Image.Image) -> QPixmap:
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())
