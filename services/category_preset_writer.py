"""Write category calibration params into product_presets.json (blenderserver copy)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("blenderserver.category_preset_writer")

_LIGHTING_ROLES = ("key", "fill", "rim")


def presets_path() -> Path:
    from core.config import settings
    return Path(settings.finishes_dir).parent / "product_presets.json"


def write_category_calibration(
    category: str,
    params: dict[str, float],
    *,
    presets_file: Path | None = None,
    baseline_cv_score: float = 0.0,
    baseline_metrics: dict[str, float] | None = None,
    baseline_final_metrics: dict[str, float] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply merged calibration params to one category in product_presets.json."""
    ppath = presets_file or presets_path()
    if not ppath.is_file():
        return {"updated": False, "error": f"File not found: {ppath}"}

    with open(ppath, encoding="utf-8") as f:
        presets = json.load(f)

    categories = presets.get("categories", {})
    cat_cfg = categories.get(category)
    if cat_cfg is None:
        return {"updated": False, "error": f"Category {category!r} not found in presets"}

    changes: dict[str, Any] = {}
    bp = {k: float(v) for k, v in params.items()}

    _pos_keys = (
        ("kx", "ky", "kz", "key"),
        ("rx", "ry", "rz", "rim"),
        ("fz", None, None, "fill"),
    )
    _pos_offsets: dict[str, dict[str, float]] = {}
    for xk, yk, zk, role in _pos_keys:
        off: dict[str, float] = {}
        if xk and xk in bp and abs(bp[xk]) > 1e-4:
            off["x"] = round(bp[xk], 4)
        if yk and yk in bp and abs(bp[yk]) > 1e-4:
            off["y"] = round(bp[yk], 4)
        if zk and zk in bp and abs(bp[zk]) > 1e-4:
            off["z"] = round(bp[zk], 4)
        if off:
            _pos_offsets[role] = off
    if _pos_offsets:
        cat_cfg["light_position_offsets"] = _pos_offsets
        changes["light_position_offsets"] = {"to": dict(_pos_offsets)}

    if "render" not in cat_cfg:
        cat_cfg["render"] = {}
    if "preview" not in cat_cfg["render"]:
        cat_cfg["render"]["preview"] = {}
    _ve = bp.get("exposure_delta", 0.0)
    old_exp = cat_cfg["render"]["preview"].get("view_exposure")
    new_exp = (float(old_exp) + _ve) if old_exp is not None else _ve
    cat_cfg["render"]["preview"]["view_exposure"] = round(new_exp, 3)
    changes["view_exposure"] = {"from": old_exp, "to": round(new_exp, 3)}

    if "studio" not in cat_cfg:
        cat_cfg["studio"] = {}
    for role in _LIGHTING_ROLES:
        mult = bp.get(f"{role}_mult")
        if mult is not None and role in cat_cfg["studio"]:
            old = cat_cfg["studio"][role].get("energy_mult")
            clamped_mult = max(float(mult), 0.1)
            new_val = float(old) * clamped_mult if old else clamped_mult
            if role == "fill" and "key" in cat_cfg.get("studio", {}):
                key_val = cat_cfg["studio"]["key"].get("energy_mult", new_val)
                new_val = min(new_val, key_val)
                new_val = max(new_val, key_val * 0.2)
            cat_cfg["studio"][role]["energy_mult"] = round(new_val, 3)
            changes[f"studio.{role}.energy_mult"] = {"from": old, "to": round(new_val, 3)}

    for role in _LIGHTING_ROLES:
        size_key = f"{role}_size_mult"
        size_mult = bp.get(size_key)
        if size_mult is None or abs(float(size_mult) - 1.0) <= 0.01:
            continue
        if role not in cat_cfg["studio"]:
            cat_cfg["studio"][role] = {}
        old_sm = cat_cfg["studio"][role].get("size_mult")
        new_sm = float(old_sm) * float(size_mult) if old_sm is not None else float(size_mult)
        cat_cfg["studio"][role]["size_mult"] = round(new_sm, 4)
        old_sym = cat_cfg["studio"][role].get("size_y_mult", old_sm)
        if old_sym is not None:
            cat_cfg["studio"][role]["size_y_mult"] = round(float(old_sym) * float(size_mult), 4)
        changes[f"studio.{role}.size_mult"] = {"from": old_sm, "to": round(new_sm, 4)}

    world = cat_cfg.setdefault("world", {})
    _wsm = bp.get("world_strength_mult")
    if _wsm is not None and abs(float(_wsm) - 1.0) > 0.01:
        base_ws = world.get("strength", 0.14)
        old_ws = world.get("strength", base_ws)
        new_ws = round(float(base_ws) * float(_wsm), 5)
        world["strength"] = new_ws
        changes["world.strength"] = {"from": old_ws, "to": new_ws}
    _hsm = bp.get("hdri_strength_mult")
    if _hsm is not None and abs(float(_hsm) - 1.0) > 0.01:
        base_hs = world.get("hdri_strength", 1.0)
        old_hs = world.get("hdri_strength", base_hs)
        new_hs = round(float(base_hs) * float(_hsm), 5)
        world["hdri_strength"] = new_hs
        changes["world.hdri_strength"] = {"from": old_hs, "to": new_hs}

    if "compositor" not in cat_cfg.get("render", {}).get("preview", {}):
        cat_cfg.setdefault("render", {}).setdefault("preview", {})["compositor"] = {}
    comp = cat_cfg["render"]["preview"]["compositor"]
    _ct = bp.get("contrast_strength", 1.08)
    old_ct = comp.get("contrast_strength")
    comp["contrast_strength"] = round(float(_ct), 4)
    changes["compositor.contrast_strength"] = {"from": old_ct, "to": round(float(_ct), 4)}
    _gl = bp.get("glow_intensity", 0.04)
    old_gl = comp.get("glow_intensity")
    comp["glow_intensity"] = round(float(_gl), 4)
    changes["compositor.glow_intensity"] = {"from": old_gl, "to": round(float(_gl), 4)}
    _ao = bp.get("ao_overlay")
    if _ao is not None and float(_ao) > 0.5:
        old_ao = comp.get("ao_overlay")
        comp["ao_overlay"] = True
        changes["compositor.ao_overlay"] = {"from": old_ao, "to": True}

    overlay_patch: dict[str, Any] = {}
    if baseline_cv_score > 0:
        overlay_patch["baseline_cv_score"] = baseline_cv_score
        if baseline_metrics:
            overlay_patch["baseline_metrics"] = dict(baseline_metrics)
        if baseline_final_metrics:
            overlay_patch["baseline_final_metrics"] = dict(baseline_final_metrics)

    if dry_run:
        return {"updated": False, "dry_run": True, "changes": changes, "path": str(ppath)}

    with open(ppath, "w", encoding="utf-8") as f:
        json.dump(presets, f, ensure_ascii=False, indent=2)
        f.write("\n")

    logger.info("Category preset updated: %s (%d changes)", category, len(changes))
    return {"updated": True, "changes": changes, "path": str(ppath), "overlay_patch": overlay_patch}
