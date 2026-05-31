"""Core: all IO/parse/sprites/build logic, WITHOUT widgets (runs in thread)."""

from __future__ import annotations

import importlib.util
import io
import json
import lzma
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QResource
from PySide6.QtGui import QImage

from .constants import CLIENT_SPELL_PATHS, SPELL_SHEETS
from .helpers import (
    collect_source_resource_paths,
    detect_proto_path,
    find_spell_json_resources,
    list_resource_files,
    minified_json_bytes,
    qt_rcc_exe,
    safe_text_value,
    write_qrc,
)


class GraphicsCore:
    def __init__(self) -> None:
        self.client_dir: str = ""
        self.spells_data: list[dict] = []
        self.previews_data: dict[str, dict] = {}
        self.effects_catalog: list[dict] = []
        self.missiles_catalog: list[dict] = []
        self.sprite_catalog_ranges: list[dict] = []
        self.icon_sheet_32: Image.Image | None = None

        self._sprite_sheet_cache: dict[str, Image.Image] = {}
        self._sprite_image_cache: dict[int, Image.Image | None] = {}
        self._effects_by_id: dict[int, dict] = {}
        self._missiles_by_id: dict[int, dict] = {}
        self._object_msgs: dict[int, object] = {}
        self._object_entry_cache: dict[int, dict] = {}
        self._anim_meta_cache: dict[tuple, dict] = {}
        self._field_object_ids: set[int] = set()
        self._app_module = None
        self._appearances = None

        # Callbacks de UI (ligados a sinais Qt -> thread-safe).
        self.log = lambda msg: None
        self.progress = lambda step, detail, value: None
        self.load_field_objects_json()

    def field_objects_json_path(self) -> Path:
        return Path(__file__).resolve().parent / "field_objects.json"

    def load_field_objects_json(self, path: Path | None = None) -> int:
        target = path or self.field_objects_json_path()
        if not target.exists():
            self._field_object_ids = set()
            return 0
        try:
            parsed = json.loads(target.read_text(encoding="utf-8"))
            ids = parsed.get("field_object_ids", []) if isinstance(parsed, dict) else []
            self._field_object_ids = {int(x) for x in ids if int(x) > 0}
            self.log(f"Fields JSON loaded: {len(self._field_object_ids)} ids.")
            return len(self._field_object_ids)
        except Exception as exc:  # noqa: BLE001
            self._field_object_ids = set()
            self.log(f"[WARN] Failed to read fields JSON: {exc}")
            return 0

    def generate_field_objects_json_from_items_xml(self, items_xml_path: Path, output_json_path: Path | None = None) -> int:
        if not items_xml_path.exists():
            raise RuntimeError(f"items.xml not found: {items_xml_path}")
        tree = ET.parse(items_xml_path)
        root = tree.getroot()
        field_ids: set[int] = set()

        for item in root.findall(".//item"):
            has_field_attr = False
            for attr in item.findall("attribute"):
                if safe_text_value(attr.get("key", "")).strip().lower() == "field":
                    has_field_attr = True
                    break
            if not has_field_attr:
                continue
            if item.get("id"):
                try:
                    iid = int(item.get("id", "0"))
                    if iid > 0:
                        field_ids.add(iid)
                except Exception:
                    pass
                continue
            # Suporte para ranges (fromid/toid) usados em alguns items.xml
            try:
                from_id = int(item.get("fromid", "0"))
                to_id = int(item.get("toid", "0"))
            except Exception:
                from_id = to_id = 0
            if from_id > 0 and to_id >= from_id:
                for iid in range(from_id, to_id + 1):
                    field_ids.add(iid)

        out_path = output_json_path or self.field_objects_json_path()
        payload = {"field_object_ids": sorted(field_ids)}
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._field_object_ids = set(field_ids)
        self.log(f"Fields JSON updated: {out_path} ({len(field_ids)} ids).")
        return len(field_ids)

    # ---- paths -----------------------------------------------------------
    def client_paths(self) -> dict[str, Path]:
        raw_base = (self.client_dir or "").strip()
        if not raw_base:
            raise RuntimeError("Select the client folder before loading.")
        base = Path(raw_base).resolve()
        if not base.exists() or not base.is_dir():
            raise RuntimeError("Selected client folder is invalid.")
        paths = {
            "base": base,
            "source_rcc": base / "bin" / "graphics_resources.rcc",
            "output_rcc": base / "bin" / "graphics_resources_custom.rcc",
            "src_dir": base / "graphics_resources_src",
            "client_exe": base / "bin" / "client.exe",
            "spells_dir": base / "spells",
        }
        missing = []
        if not paths["source_rcc"].exists():
            missing.append(str(paths["source_rcc"]))
        if not paths["client_exe"].exists():
            missing.append(str(paths["client_exe"]))
        if missing:
            raise RuntimeError(
                "Invalid client folder. Required files not found:\n- "
                + "\n- ".join(missing)
            )
        return paths

    # ---- carregamento ----------------------------------------------------
    def load_client(self) -> None:
        p = self.client_paths()
        self.progress("Automatic backup", "Creating initial backup...", 5)
        self.auto_backup_on_load(p)
        self.progress("Decompile RCC", "Extracting files...", 30)
        self.decompile_rcc()
        self.progress("Extract JSON", "Reading client.exe and extracting JSON...", 60)
        self.extract_spell_jsons()
        self.progress("Open JSON", "Loading editors...", 80)
        self.load_json_data()
        self.load_effects_and_missiles_catalog()
        self.load_icon_sheet()
        self.progress("Completed", "Client loaded and ready for editing.", 100)

    def auto_backup_on_load(self, p: dict[str, Path]) -> None:
        src_rcc = p["source_rcc"]
        if not src_rcc.exists():
            return
        bak_rcc = src_rcc.with_suffix(src_rcc.suffix + ".bak")
        shutil.copy2(src_rcc, bak_rcc)
        self.log(f"Automatic RCC backup updated: {bak_rcc}")

    def decompile_rcc(self) -> None:
        p = self.client_paths()
        src_dir = p["src_dir"]
        if src_dir.exists():
            shutil.rmtree(src_dir)
        src_dir.mkdir(parents=True, exist_ok=True)
        already_registered = set(list_resource_files())
        if not QResource.registerResource(str(p["source_rcc"])):
            raise RuntimeError(f"Failed to open RCC: {p['source_rcc']}")
        resource_paths = sorted(set(list_resource_files()) - already_registered)
        for qpath in resource_paths:
            rel = qpath.removeprefix(":/")
            out_path = src_dir / rel
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(bytes(QResource(qpath).data()))
        QResource.unregisterResource(str(p["source_rcc"]))
        write_qrc(src_dir, [x.removeprefix(":/") for x in resource_paths])
        self.log(f"Decompiled to: {src_dir}")

    def extract_spell_jsons(self) -> None:
        p = self.client_paths()
        resources = find_spell_json_resources(p["client_exe"].read_bytes())
        p["spells_dir"].mkdir(parents=True, exist_ok=True)
        for name, (_, _, raw) in resources.items():
            out = p["spells_dir"] / name
            out.write_bytes(raw)
            self.log(f"JSON extracted: {out}")

    # ---- validacao -------------------------------------------------------
    def validate_spells_schema(self, obj) -> None:
        if not isinstance(obj, list):
            raise RuntimeError("spells.json must be a list.")
        seen = set()
        for i, item in enumerate(obj):
            if not isinstance(item, dict):
                raise RuntimeError(f"spells item #{i + 1} must be an object.")
            if "spellid" not in item or not isinstance(item["spellid"], int):
                raise RuntimeError(f"spells item #{i + 1} requires integer spellid.")
            if item["spellid"] in seen:
                raise RuntimeError(f"duplicate spellid in spells.json: {item['spellid']}")
            seen.add(item["spellid"])

    def validate_previews_schema(self, obj) -> None:
        if not isinstance(obj, dict):
            raise RuntimeError("spells-previews.json must be an object.")
        for key, value in obj.items():
            if not str(key).isdigit():
                raise RuntimeError(f"Invalid key in previews: {key}")
            if not isinstance(value, dict):
                raise RuntimeError(f"Preview record[{key}] must be an object.")
            if "spellid" not in value:
                raise RuntimeError(f"Preview record[{key}] without spellid.")
            if int(value["spellid"]) != int(key):
                raise RuntimeError(f"Preview record[{key}] must have spellid {key}.")

    def validate_jsons(self) -> None:
        p = self.client_paths()
        spells_path = p["spells_dir"] / "spells.json"
        previews_path = p["spells_dir"] / "spells-previews.json"
        if not spells_path.exists() or not previews_path.exists():
            raise RuntimeError("Could not find spells.json and spells-previews.json in spells folder.")
        self.validate_spells_schema(json.loads(spells_path.read_text(encoding="utf-8")))
        self.validate_previews_schema(json.loads(previews_path.read_text(encoding="utf-8")))
        self.log("JSONs validated successfully.")

    def load_json_data(self) -> None:
        p = self.client_paths()
        self.spells_data = json.loads((p["spells_dir"] / "spells.json").read_text(encoding="utf-8"))
        self.previews_data = json.loads((p["spells_dir"] / "spells-previews.json").read_text(encoding="utf-8"))
        self.validate_spells_schema(self.spells_data)
        self.validate_previews_schema(self.previews_data)

    def save_spells_file(self) -> None:
        self.validate_spells_schema(self.spells_data)
        p = self.client_paths()
        out = p["spells_dir"] / "spells.json"
        out.write_text(json.dumps(self.spells_data, ensure_ascii=False, indent=4), encoding="utf-8")
        self.log(f"File saved: {out}")

    def save_previews_file(self) -> None:
        self.validate_previews_schema(self.previews_data)
        p = self.client_paths()
        out = p["spells_dir"] / "spells-previews.json"
        ordered = dict(sorted(self.previews_data.items(), key=lambda x: int(x[0])))
        out.write_text(json.dumps(ordered, ensure_ascii=False, indent=4), encoding="utf-8")
        self.previews_data = ordered
        self.log(f"File saved: {out}")

    # ---- catalogo FX / objetos ------------------------------------------
    def load_effects_and_missiles_catalog(self) -> None:
        self.effects_catalog = []
        self.missiles_catalog = []
        self.sprite_catalog_ranges = []
        self._sprite_sheet_cache = {}
        self._sprite_image_cache = {}
        self._effects_by_id = {}
        self._missiles_by_id = {}
        self._object_msgs = {}
        self._object_entry_cache = {}
        self._anim_meta_cache = {}
        try:
            p = self.client_paths()
            assets_dir = p["base"] / "assets"
            catalog_content = assets_dir / "catalog-content.json"
            app_dat = self._appearances_dat_path(catalog_content, assets_dir)
            proto_path = detect_proto_path()
            if app_dat is None or proto_path is None:
                self.log("FX/Missiles catalog: appearances.dat or appearances.proto not found.")
                return
            self.sprite_catalog_ranges = self._load_sprite_ranges_from_catalog(catalog_content, assets_dir)
            effects, missiles, object_msgs = self._parse_appearances_dat(proto_path, app_dat)
            self.effects_catalog = effects
            self.missiles_catalog = missiles
            self._effects_by_id = {int(e["id"]): e for e in effects}
            self._missiles_by_id = {int(m["id"]): m for m in missiles}
            self._object_msgs = object_msgs
            self._warmup_object_preview_cache()
            self.log(f"Catalog loaded: {len(effects)} effects, {len(missiles)} missiles, {len(object_msgs)} objects.")
        except Exception as exc:  # noqa: BLE001
            self.log(f"[WARN] Failed to load FX/Missiles catalog: {exc}")

    def _warmup_object_preview_cache(self) -> None:
        # Precarrega apenas os objetos que entram na lista (filtrados por JSON, quando existir).
        if self._field_object_ids:
            selected_ids = sorted([int(oid) for oid in self._field_object_ids if int(oid) in self._object_msgs])
        else:
            selected_ids = sorted(int(oid) for oid in self._object_msgs.keys())
        total = len(selected_ids)
        if total <= 0:
            return
        for i, oid in enumerate(selected_ids, start=1):
            try:
                cat = self.object_by_id(oid)
                _ = self.sprite_for_catalog_entry(cat, 0, 0, 0)
            except Exception:
                continue
            if i % 200 == 0 or i == total:
                self.progress("Catalog", f"Preloading objects ({i}/{total})...", 80 + int((i / total) * 15))

    def _appearances_dat_path(self, catalog_path: Path, assets_dir: Path) -> Path | None:
        try:
            if catalog_path.exists():
                parsed = json.loads(catalog_path.read_text(encoding="utf-8"))
                for entry in parsed:
                    if entry.get("type") == "appearances" and entry.get("file"):
                        candidate = assets_dir / entry["file"]
                        if candidate.exists():
                            return candidate
        except Exception:
            pass
        return next(iter(sorted(assets_dir.glob("appearances-*.dat"))), None)

    def _parse_appearances_dat(self, proto_path: Path, dat_path: Path) -> tuple[list[dict], list[dict], dict[int, object]]:
        module = self._app_module
        if module is None:
            with tempfile.TemporaryDirectory(prefix="proto_build_") as tmp_dir:
                subprocess.run(
                    [sys.executable, "-m", "grpc_tools.protoc", f"-I{proto_path.parent}", f"--python_out={tmp_dir}", str(proto_path)],
                    check=True,
                )
                pb2_path = Path(tmp_dir) / "appearances_pb2.py"
                if not pb2_path.exists():
                    raise RuntimeError("Could not generate appearances_pb2.py")
                spec = importlib.util.spec_from_file_location("appearances_pb2_runtime", pb2_path)
                if spec is None or spec.loader is None:
                    raise RuntimeError("Failed to load generated protobuf module.")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            self._app_module = module

        appearances = module.Appearances()
        appearances.ParseFromString(dat_path.read_bytes())
        self._appearances = appearances
        effects = [self._appearance_entry(a) for a in appearances.effect]
        missiles = [self._appearance_entry(a) for a in appearances.missile]
        effects.sort(key=lambda x: x["id"])
        missiles.sort(key=lambda x: x["id"])
        object_msgs = {int(getattr(a, "id", 0)): a for a in getattr(appearances, "object", [])}
        return effects, missiles, object_msgs

    def _appearance_entry(self, a) -> dict:
        sprite_ids: list[int] = []
        pattern_width = pattern_height = pattern_depth = layers = 1
        frame_durations: list[int] = []
        frame_durations_min: list[int] = []
        frame_durations_max: list[int] = []
        try:
            for fg in getattr(a, "frame_group", []):
                if not getattr(fg, "sprite_info", None):
                    continue
                si = fg.sprite_info
                pattern_width = max(1, int(getattr(si, "pattern_width", 1) or 1))
                pattern_height = max(1, int(getattr(si, "pattern_height", 1) or 1))
                pattern_depth = max(1, int(getattr(si, "pattern_depth", 1) or 1))
                layers = max(1, int(getattr(si, "layers", 1) or 1))
                try:
                    for ph in getattr(si.animation, "sprite_phase", []):
                        mn = int(getattr(ph, "duration_min", 0) or 0)
                        mx = int(getattr(ph, "duration_max", mn) or mn)
                        if mn <= 0 and mx <= 0:
                            mn = 40
                            mx = 40
                        if mx < mn:
                            mx = mn
                        frame_durations_min.append(max(1, mn))
                        frame_durations_max.append(max(1, mx))
                        frame_durations.append(max(1, (mn + mx) // 2))
                except Exception:
                    pass
                for sid in getattr(fg.sprite_info, "sprite_id", []):
                    n = int(sid)
                    if n > 0:
                        sprite_ids.append(n)
        except Exception:
            sprite_ids = []
        return {
            "id": int(getattr(a, "id", 0)),
            "name": safe_text_value(getattr(a, "name", "")),
            "sprite_id": sprite_ids[0] if sprite_ids else 0,
            "sprite_ids": sprite_ids,
            "pattern_width": pattern_width,
            "pattern_height": pattern_height,
            "pattern_depth": pattern_depth,
            "layers": layers,
            "frame_durations": frame_durations,
            "frame_durations_min": frame_durations_min,
            "frame_durations_max": frame_durations_max,
        }

    def _load_sprite_ranges_from_catalog(self, catalog_path: Path, assets_dir: Path) -> list[dict]:
        if not catalog_path.exists():
            return []
        parsed = json.loads(catalog_path.read_text(encoding="utf-8"))
        ranges: list[dict] = []
        for entry in parsed:
            if entry.get("type") != "sprite":
                continue
            if "firstspriteid" not in entry or "lastspriteid" not in entry or "file" not in entry:
                continue
            ranges.append({
                "first": int(entry["firstspriteid"]),
                "last": int(entry["lastspriteid"]),
                "file": assets_dir / entry["file"],
                "spritetype": int(entry.get("spritetype", 0)),
            })
        ranges.sort(key=lambda x: x["first"])
        return ranges

    def _find_sprite_range_for_id(self, sprite_id: int):
        if sprite_id <= 0:
            return None
        for r in self.sprite_catalog_ranges:
            if r["first"] <= sprite_id <= r["last"]:
                return r
        return None

    def _sprite_dims_for_range(self, r: dict) -> tuple[int, int]:
        st = int(r.get("spritetype", 0) or 0)
        return {0: (32, 32), 1: (32, 64), 2: (64, 32), 3: (64, 64)}.get(st, (32, 32))

    def _decompress_cip_lzma_bmp(self, file_path: Path) -> Image.Image:
        raw = file_path.read_bytes()
        pos = 0
        while pos < len(raw):
            b = raw[pos]
            pos += 1
            if b != 0:
                break
        pos += 4
        while pos < len(raw):
            b = raw[pos]
            pos += 1
            if (b & 0x80) == 0:
                break
        props = raw[pos: pos + 5]
        pos += 5
        pos += 8
        comp = raw[pos:]
        prop0 = props[0]
        lc = prop0 % 9
        rest = prop0 // 9
        lp = rest % 5
        pb = rest // 5
        dict_size = int.from_bytes(props[1:5], "little")
        filters = [{"id": lzma.FILTER_LZMA1, "dict_size": dict_size, "lc": lc, "lp": lp, "pb": pb}]
        bmp_bytes = lzma.decompress(comp, format=lzma.FORMAT_RAW, filters=filters)
        return Image.open(io.BytesIO(bmp_bytes)).convert("RGBA")

    def _get_sprite_sheet(self, file_path: Path) -> Image.Image | None:
        key = str(file_path)
        if key in self._sprite_sheet_cache:
            return self._sprite_sheet_cache[key]
        if not file_path.exists():
            return None
        img = self._decompress_cip_lzma_bmp(file_path)
        self._sprite_sheet_cache[key] = img
        return img

    def _get_sprite_image_by_id(self, sprite_id: int) -> Image.Image | None:
        if sprite_id in self._sprite_image_cache:
            return self._sprite_image_cache[sprite_id]
        result = self._extract_sprite_image(sprite_id)
        self._sprite_image_cache[sprite_id] = result
        return result

    def _extract_sprite_image(self, sprite_id: int) -> Image.Image | None:
        r = self._find_sprite_range_for_id(sprite_id)
        if r is None:
            return None
        sheet = self._get_sprite_sheet(r["file"])
        if sheet is None:
            return None
        sw, sh = self._sprite_dims_for_range(r)
        idx = sprite_id - r["first"]
        cols = max(1, sheet.width // sw)
        row = idx // cols
        col = idx % cols
        x0 = col * sw
        y0 = row * sh
        if x0 + sw > sheet.width or y0 + sh > sheet.height:
            return None
        return sheet.crop((x0, y0, x0 + sw, y0 + sh))

    def effect_by_id(self, effect_id: int):
        return self._effects_by_id.get(int(effect_id))

    def missile_by_id(self, missile_id: int):
        return self._missiles_by_id.get(int(missile_id))

    def object_by_id(self, object_id: int):
        oid = int(object_id)
        cached = self._object_entry_cache.get(oid)
        if cached is not None:
            return cached
        msg = self._object_msgs.get(oid)
        if msg is None:
            return None
        entry = self._appearance_entry(msg)
        self._object_entry_cache[oid] = entry
        return entry

    def object_entries(self) -> list[dict]:
        out = []
        for oid, msg in self._object_msgs.items():
            if self._field_object_ids and int(oid) not in self._field_object_ids:
                continue
            out.append({
                "id": int(oid),
                "name": safe_text_value(getattr(msg, "name", "")),
            })
        out.sort(key=lambda x: int(x["id"]))
        return out

    def _is_field_object(self, msg) -> bool:
        flags = getattr(msg, "flags", None)
        if flags is None:
            return False
        try:
            return bool(getattr(flags, "avoid", False))
        except Exception:
            return False

    def max_effect_id(self) -> int:
        return max([int(e.get("id", 1)) for e in self.effects_catalog], default=1)

    def max_missile_id(self) -> int:
        return max([int(m.get("id", 1)) for m in self.missiles_catalog], default=1)

    def _animation_meta_for_entry(self, cat: dict) -> dict:
        pw = max(1, int(cat.get("pattern_width", 1)))
        ph = max(1, int(cat.get("pattern_height", 1)))
        pd = max(1, int(cat.get("pattern_depth", 1)))
        layers = max(1, int(cat.get("layers", 1)))
        sprite_ids = [int(s) for s in cat.get("sprite_ids", []) if int(s) > 0]
        sig = (
            int(cat.get("id", 0)),
            pw,
            ph,
            pd,
            layers,
            len(sprite_ids),
            tuple(int(d) for d in cat.get("frame_durations_min", []) if int(d) > 0),
            tuple(int(d) for d in cat.get("frame_durations_max", []) if int(d) > 0),
            tuple(int(d) for d in cat.get("frame_durations", []) if int(d) > 0),
        )
        cached = self._anim_meta_cache.get(sig)
        if cached is not None:
            return cached
        sprites_per_phase = max(1, layers * pw * ph * pd)
        phases_available = max(1, len(sprite_ids) // sprites_per_phase)
        min_durations = [int(d) for d in cat.get("frame_durations_min", []) if int(d) > 0]
        max_durations = [int(d) for d in cat.get("frame_durations_max", []) if int(d) > 0]
        durations = [int(d) for d in cat.get("frame_durations", []) if int(d) > 0]

        if min_durations and max_durations:
            c = min(len(min_durations), len(max_durations))
            durations = [max(1, (min_durations[i] + max_durations[i]) // 2) for i in range(c)]
        if not durations:
            durations = [60] * phases_available
        if len(durations) < phases_available:
            durations.extend([durations[-1]] * (phases_available - len(durations)))
        elif len(durations) > phases_available:
            durations = durations[:phases_available]

        cumulative: list[int] = []
        acc = 0
        for d in durations:
            acc += d
            cumulative.append(acc)
        meta = {
            "sprites_per_phase": sprites_per_phase,
            "phases_available": phases_available,
            "durations": durations,
            "total_duration": max(1, cumulative[-1] if cumulative else 1),
            "cumulative": cumulative,
        }
        self._anim_meta_cache[sig] = meta
        return meta

    def _current_phase_for_catalog_entry(self, cat: dict, elapsed_ms: int) -> int:
        meta = self._animation_meta_for_entry(cat)
        if meta["phases_available"] <= 1:
            return 0
        t = elapsed_ms % meta["total_duration"]
        for i, threshold in enumerate(meta["cumulative"]):
            if t < threshold:
                return i
        return len(meta["durations"]) - 1

    def sprite_for_catalog_entry(self, cat: dict | None, elapsed_ms: int, pattern_x: int = 0, pattern_y: int = 0):
        if not cat:
            return None
        sprite_ids = [int(s) for s in cat.get("sprite_ids", []) if int(s) > 0]
        if not sprite_ids:
            sid = int(cat.get("sprite_id", 0))
            if sid > 0:
                sprite_ids = [sid]
        if not sprite_ids:
            return None
        pw = max(1, int(cat.get("pattern_width", 1)))
        ph = max(1, int(cat.get("pattern_height", 1)))
        pd = max(1, int(cat.get("pattern_depth", 1)))
        layers = max(1, int(cat.get("layers", 1)))
        px = min(max(int(pattern_x), 0), pw - 1)
        py = min(max(int(pattern_y), 0), ph - 1)
        meta = self._animation_meta_for_entry(cat)
        sprites_per_phase = int(meta["sprites_per_phase"])
        phases_available = int(meta["phases_available"])
        phase = self._current_phase_for_catalog_entry(cat, elapsed_ms) % phases_available
        idx = phase * sprites_per_phase + (py * pw + px) * layers
        idx = min(max(idx, 0), len(sprite_ids) - 1)
        return self._get_sprite_image_by_id(sprite_ids[idx])

    def animation_total_duration_max_ms(self, cat: dict | None) -> int:
        if not cat:
            return 0
        max_durations = [int(d) for d in cat.get("frame_durations_max", []) if int(d) > 0]
        if max_durations:
            return max(1, sum(max_durations))
        # fallback for catalogs without per-phase min/max
        avg_durations = [int(d) for d in cat.get("frame_durations", []) if int(d) > 0]
        if avg_durations:
            return max(1, sum(avg_durations))
        return 0

    def missile_pattern_for_offset(self, dx: int, dy: int) -> tuple[int, int]:
        if dx == 0 and dy == 0:
            return (1, 1)
        sx = -1 if dx < 0 else (1 if dx > 0 else 0)
        sy = -1 if dy < 0 else (1 if dy > 0 else 0)
        table = {
            (-1, -1): (0, 0), (0, -1): (1, 0), (1, -1): (2, 0),
            (1, 0): (2, 1), (1, 1): (2, 2), (0, 1): (1, 2),
            (-1, 1): (0, 2), (-1, 0): (0, 1),
        }
        return table.get((sx, sy), (0, 1))

    # ---- icones ----------------------------------------------------------
    def load_icon_sheet(self) -> None:
        p = self.client_paths()
        sheet_path = p["src_dir"] / SPELL_SHEETS[0][0]
        self.icon_sheet_32 = Image.open(sheet_path).convert("RGBA") if sheet_path.exists() else None

    def icon_count(self) -> int:
        if self.icon_sheet_32 is None:
            return 0
        return self.icon_sheet_32.width // SPELL_SHEETS[0][1]

    def icon_crop(self, idx: int, size: int) -> Image.Image | None:
        p = self.client_paths()
        rel = next(r for r, s in SPELL_SHEETS if s == size)
        sheet_path = p["src_dir"] / rel
        if not sheet_path.exists():
            return None
        sheet = Image.open(sheet_path).convert("RGBA")
        if idx >= sheet.width // size:
            return None
        return sheet.crop((idx * size, 0, (idx + 1) * size, size))

    def _expand_sheet_to_index(self, sheet: Image.Image, size: int, target_index: int) -> Image.Image:
        current_count = sheet.width // size
        if target_index < current_count:
            return sheet
        expanded = Image.new("RGBA", ((target_index + 1) * size, size), (0, 0, 0, 0))
        expanded.alpha_composite(sheet, (0, 0))
        return expanded

    def ensure_icon_index_capacity(self, target_index: int) -> None:
        if target_index < 0:
            raise RuntimeError("Target index cannot be negative.")
        p = self.client_paths()
        for rel, size in SPELL_SHEETS:
            sheet_path = p["src_dir"] / rel
            if not sheet_path.exists():
                raise RuntimeError(f"Spritesheet not found: {sheet_path}.")
            sheet = Image.open(sheet_path).convert("RGBA")
            expanded = self._expand_sheet_to_index(sheet, size, target_index)
            if expanded.width != sheet.width:
                expanded.save(sheet_path)
                self.log(f"Custom space created up to index {target_index} in {sheet_path.name}.")

    def add_or_replace_icon(self, icon_file: str, selected_index: int | None) -> int:
        icon_path = Path(icon_file)
        if not icon_path.exists():
            raise RuntimeError("Icon file not found.")
        idx = 0
        for i, (rel, size) in enumerate(SPELL_SHEETS, start=1):
            sheet_path = self.client_paths()["src_dir"] / rel
            if not sheet_path.exists():
                raise RuntimeError(f"Spritesheet not found: {sheet_path}. Run Load Client first.")
            sheet = Image.open(sheet_path).convert("RGBA")
            count = sheet.width // size
            idx = count if selected_index is None else selected_index
            if idx >= count:
                expanded = Image.new("RGBA", ((idx + 1) * size, size), (0, 0, 0, 0))
                expanded.alpha_composite(sheet, (0, 0))
                sheet = expanded
            icon = Image.open(icon_path).convert("RGBA")
            if icon.size != (size, size):
                icon = icon.resize((size, size), Image.Resampling.LANCZOS)
            sheet.alpha_composite(icon, (idx * size, 0))
            sheet.save(sheet_path)
            self.log(f"Spritesheet updated: {sheet_path.name}")
            self.progress("Icon", f"Updated {sheet_path.name}", 10 + (i / len(SPELL_SHEETS)) * 85)
        self.load_icon_sheet()
        self.progress("Completed", "Icons atualizados com sucesso.", 100)
        return idx

    def remove_icon(self, idx: int) -> None:
        for i, (rel, size) in enumerate(SPELL_SHEETS, start=1):
            sheet_path = self.client_paths()["src_dir"] / rel
            if not sheet_path.exists():
                raise RuntimeError(f"Spritesheet not found: {sheet_path}.")
            sheet = Image.open(sheet_path).convert("RGBA")
            max_index = (sheet.width // size) - 1
            if idx > max_index:
                raise RuntimeError(f"Index {idx} out of range for {sheet_path.name} (max {max_index}).")
            sheet.paste((0, 0, 0, 0), (idx * size, 0, (idx + 1) * size, size))
            sheet.save(sheet_path)
            self.log(f"Icon removed at index {idx}: {sheet_path.name}")
            self.progress("Icon", f"Removed in {sheet_path.name}", 10 + (i / len(SPELL_SHEETS)) * 85)
        self.load_icon_sheet()
        self.progress("Completed", "Icon removido com sucesso.", 100)

    def move_icon_index(self, source_idx: int, target_idx: int) -> None:
        self.ensure_icon_index_capacity(max(source_idx, target_idx))
        for i, (rel, size) in enumerate(SPELL_SHEETS, start=1):
            sheet_path = self.client_paths()["src_dir"] / rel
            sheet = Image.open(sheet_path).convert("RGBA")
            source_box = (source_idx * size, 0, (source_idx + 1) * size, size)
            target_box = (target_idx * size, 0, (target_idx + 1) * size, size)
            source_icon = sheet.crop(source_box)
            target_icon = sheet.crop(target_box)
            sheet.paste(source_icon, target_box)
            sheet.paste(target_icon, source_box)
            sheet.save(sheet_path)
            self.progress("Icon", f"Reordered in {sheet_path.name}", 10 + (i / len(SPELL_SHEETS)) * 85)
        self.load_icon_sheet()
        self.log(f"Icons swapped between indexes {source_idx} and {target_idx}.")
        self.progress("Completed", "Icon reordering completed.", 100)

    # ---- build / install (portado fielmente do Tkinter; destrutivo) ------
    def compile_rcc(self) -> None:
        p = self.client_paths()
        qrc_path = p["src_dir"] / "graphics_resources.qrc"
        if not qrc_path.exists():
            write_qrc(p["src_dir"], collect_source_resource_paths(p["src_dir"]))
        self.progress("Compile", "Running rcc.exe...", 45)
        subprocess.run(
            [str(qt_rcc_exe()), "-binary", str(qrc_path), "-o", str(p["output_rcc"])],
            cwd=p["src_dir"],
            check=True,
        )
        self.validate_rcc(p["output_rcc"])
        self.log(f"RCC compiled: {p['output_rcc']}")

    def validate_rcc(self, rcc_path: Path) -> None:
        if not QResource.registerResource(str(rcc_path)):
            raise RuntimeError(f"Invalid RCC: {rcc_path}")
        try:
            for rel, _ in SPELL_SHEETS:
                image = QImage(f":/{rel}")
                if image.isNull():
                    raise RuntimeError(f"Missing resource in RCC: :/{rel}")
                self.log(f"OK :/{rel} -> {image.width()}x{image.height()}")
        finally:
            QResource.unregisterResource(str(rcc_path))

    def manual_backup(self) -> None:
        p = self.client_paths()
        backup_dir = p["base"] / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for item in (p["source_rcc"], p["client_exe"]):
            if item.exists():
                dst = backup_dir / f"{item.stem}.manual_{stamp}{item.suffix}"
                shutil.copy2(item, dst)
                self.log(f"Manual backup: {dst}")
        self.progress("Manual backup", "Completed.", 100)

    def embed_spell_jsons(self) -> None:
        import zlib

        p = self.client_paths()
        data = p["client_exe"].read_bytes()
        slots = find_spell_json_resources(data)
        patched = bytearray(data)
        for name in ("spells.json", "spells-previews.json"):
            src = p["spells_dir"] / name
            if not src.exists():
                raise RuntimeError(f"JSON not found for embed: {src}")
            offset, compressed_size, raw_original = slots[name]
            max_uncompressed = len(raw_original)
            raw = minified_json_bytes(src)
            if len(raw) > max_uncompressed:
                raise RuntimeError(f"{name} exceeds max size ({len(raw)} > {max_uncompressed}).")
            raw = raw + (b" " * (max_uncompressed - len(raw)))
            compressed = zlib.compress(raw, level=9)
            if len(compressed) > compressed_size:
                raise RuntimeError(f"{name} compressed exceeds limit ({len(compressed)} > {compressed_size}).")
            replacement = compressed + (b"\x00" * (compressed_size - len(compressed)))
            patched[offset: offset + compressed_size] = replacement
            self.log(f"{name} embedded: {len(compressed)}/{compressed_size}")
        backup = p["client_exe"].with_name("client.original.exe")
        if not backup.exists():
            shutil.copy2(p["client_exe"], backup)
            self.log(f"Backup created: {backup}")
        p["client_exe"].write_bytes(patched)
        self.log("client.exe updated with embedded JSONs.")

    def patch_client_for_embedded_spells(self) -> None:
        p = self.client_paths()
        data = p["client_exe"].read_bytes()
        target = CLIENT_SPELL_PATHS["embedded"]
        path_counts = {name: [data.count(x) for x in values] for name, values in CLIENT_SPELL_PATHS.items()}
        if path_counts["embedded"] == [1, 1]:
            self.log("client.exe already points to embedded JSONs.")
            return
        source_name = None
        for name, counts in path_counts.items():
            if name != "embedded" and counts == [1, 1]:
                source_name = name
                break
        if source_name is None:
            raise RuntimeError(f"Could not find patchable path in client.exe: {path_counts}")
        patched = data
        for old, new in zip(CLIENT_SPELL_PATHS[source_name], target):
            if len(old) != len(new):
                raise RuntimeError("Patch string size mismatch.")
            patched = patched.replace(old, new, 1)
        p["client_exe"].write_bytes(patched)
        self.log("client.exe path patch completed.")

    def install_all(self) -> None:
        p = self.client_paths()
        backup_rcc = p["source_rcc"].with_name("graphics_resources.original.rcc")
        if not backup_rcc.exists():
            shutil.copy2(p["source_rcc"], backup_rcc)
            self.log(f"Backup created: {backup_rcc}")
        shutil.copy2(p["output_rcc"], p["source_rcc"])
        self.log("Custom RCC installed in client.")
        self.embed_spell_jsons()
        self.patch_client_for_embedded_spells()
        self.log("Full installation completed.")

    def compile_and_install(self) -> None:
        self.progress("Validation", "Validating and saving JSONs...", 10)
        self.save_spells_file()
        self.save_previews_file()
        self.validate_jsons()
        self.progress("Compilation", "Compiling RCC...", 40)
        self.compile_rcc()
        self.progress("Installation", "Installing into client...", 80)
        self.install_all()
        self.progress("Completed", "Compilation + installation completed.", 100)




