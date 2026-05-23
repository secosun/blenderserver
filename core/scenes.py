"""Predefined rendering scenes / presets.

Each scene maps to a set of parameters for blenderworker's
``ecommerce_product_shot_pipeline`` MCP tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ScenePreset:
    """A named rendering preset — the user's "选择场景"."""

    id: str
    name: str
    description: str
    params: dict[str, Any]


# ---------------------------------------------------------------------------
# Scene catalogue
# ---------------------------------------------------------------------------

SCENES: dict[str, ScenePreset] = {}

def _reg(id: str, name: str, desc: str, **params) -> ScenePreset:
    s = ScenePreset(id=id, name=name, description=desc, params=params)
    SCENES[id] = s
    return s


# --- Studio (industrial / e-commerce standard) ---

_reg(
    "studio_champagne",
    "香槟金 工作室标准",
    "香槟色金属质感，三点布光，3/4视角，透明背景PNG。适用于铝型材、门窗框等。",
    product_category="coating_champagne_box_profile_metal_sheet",
    lighting_profile="mid_standard",
    camera_style="three_quarter",
    backdrop="cyclorama",
    material_mode="enhance",
    engine="CYCLES",
    samples=512,
    resolution_x=2560,
    resolution_y=2560,
    transparent_background=True,
)

_reg(
    "studio_black_matte",
    "哑光黑 工作室标准",
    "哑光黑色产品，强光突出轮廓，3/4视角，透明背景PNG。适用于黑色粉末涂层铝型材。",
    product_category="aluminum_6063_powder_black",
    lighting_profile="dark_strong",
    camera_style="three_quarter",
    backdrop="cyclorama",
    material_mode="enhance",
    engine="CYCLES",
    samples=512,
    resolution_x=2560,
    resolution_y=2560,
    transparent_background=True,
)

_reg(
    "studio_gunmetal",
    "枪灰色 金属质感",
    "枪灰色金属，标准布光，3/4视角，透明背景PNG。适用于栏杆、工业件。",
    product_category="aluminum_gunmetal_railing",
    lighting_profile="mid_standard",
    camera_style="three_quarter",
    backdrop="cyclorama",
    material_mode="enhance",
    engine="CYCLES",
    samples=512,
    resolution_x=2560,
    resolution_y=2560,
    transparent_background=True,
)

_reg(
    "studio_automotive",
    "汽车烤漆 高光泽",
    "高光汽车漆面效果，镜面反射，强光勾勒，透明背景PNG。适用于涂层表面件。",
    product_category="coating_automotive_black",
    lighting_profile="mid_standard",
    camera_style="three_quarter",
    backdrop="cyclorama",
    material_mode="enhance",
    engine="CYCLES",
    samples=640,
    resolution_x=2560,
    resolution_y=2560,
    transparent_background=True,
)

_reg(
    "studio_white_soft",
    "柔光白 简洁风",
    "柔和白光，正面视角，透明背景PNG。适用于白色/浅色产品。",
    product_category="coating_gray_metal_plate",
    lighting_profile="light_soft",
    camera_style="front",
    backdrop="cyclorama",
    material_mode="enhance",
    engine="CYCLES",
    samples=384,
    resolution_x=2560,
    resolution_y=2560,
    transparent_background=True,
)

_reg(
    "studio_orange",
    "橙色粉末涂层 亮色系",
    "亮橙色产品，色彩突出，标准布光，透明背景PNG。",
    product_category="coating_orange_yellow_powder",
    lighting_profile="mid_standard",
    camera_style="three_quarter",
    backdrop="cyclorama",
    material_mode="enhance",
    engine="CYCLES",
    samples=512,
    resolution_x=2560,
    resolution_y=2560,
    transparent_background=True,
)


# --- Special ---

_reg(
    "detail_closeup",
    "局部特写",
    "产品局部细节特写，微距视角，细腻材质表现，透明背景PNG。适用于展示表面处理工艺。",
    product_category="generic",
    lighting_profile="mid_standard",
    camera_style="detail",
    backdrop="cyclorama",
    material_mode="enhance",
    engine="CYCLES",
    samples=640,
    resolution_x=3840,
    resolution_y=2160,
    transparent_background=True,
)

_reg(
    "transparent_black",
    "黑底透明背景",
    "黑色产品，透明背景（PNG），适合电商主图后期合成。",
    product_category="aluminum_6063_powder_black",
    lighting_profile="dark_strong",
    camera_style="three_quarter",
    backdrop="transparent",
    material_mode="enhance",
    engine="CYCLES",
    samples=512,
    resolution_x=2560,
    resolution_y=2560,
    transparent_background=True,
)

_reg(
    "transparent_champagne",
    "香槟金 透明背景",
    "香槟色产品，透明背景（PNG），电商白底图替代。",
    product_category="coating_champagne_box_profile_metal_sheet",
    lighting_profile="mid_standard",
    camera_style="three_quarter",
    backdrop="transparent",
    material_mode="enhance",
    engine="CYCLES",
    samples=512,
    resolution_x=2560,
    resolution_y=2560,
    transparent_background=True,
)


# --- FreeCAD-specific ---

_reg(
    "freecad_profile_preview",
    "型材截面 快速预览",
    "FreeCAD 型材快速预览，EEVEE 引擎快速出图，低采样。适用于设计迭代阶段。",
    product_category="generic",
    lighting_profile="mid_standard",
    camera_style="three_quarter",
    backdrop="cyclorama",
    material_mode="studio_pbr",
    engine="EEVEE",
    samples=64,
    resolution_x=1920,
    resolution_y=1920,
    transparent_background=False,
    freecad_mm_obj=True,
)


def get_scene(scene_id: str) -> ScenePreset | None:
    return SCENES.get(scene_id)


def list_scenes() -> list[dict]:
    return [
        {"id": s.id, "name": s.name, "description": s.description}
        for s in SCENES.values()
    ]
