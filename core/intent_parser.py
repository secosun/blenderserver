from __future__ import annotations

import json
import os
from typing import Any

from anthropic import Anthropic

from core.config import settings

SYSTEM_PROMPT = """You are a 3D product rendering engineer for an e-commerce SaaS platform.
Your job is to translate the user's natural-language rendering request into a structured JSON
"Render Intent" that drives Blender's automated product-shot pipeline.

CAPABILITIES
- Import 3D models (OBJ, FBX, STL, STEP) with auto-upright, scale normalization
- Set up studio lighting (3-point: key/fill/rim) with cyclorama backdrop
- Apply PBR materials from a 20+ category library (aluminum alloys, coatings, paints)
- Render two-phase: coarse preview (fast) -> fine final (high-res)
- Support orbiting camera, transparent background, GPU rendering

AVAILABLE CONFIGURATION

Product Categories (material + lighting presets):
- aluminum_6063_powder_black — Black powder-coated aluminum (matte, industrial)
- aluminum_gunmetal_railing — Gunmetal gray aluminum (satin metallic)
- coating_automotive_black — High-gloss automotive black paint (mirror finish)
- coating_black_product — General black product coating
- coating_champagne_box_profile_metal_sheet — Champagne/silver metallic box profile
- coating_champagne_metal_plate_02 — Bright champagne polished metal
- coating_gray_corrugated_iron — Industrial gray corrugated metal
- coating_gray_metal_plate — Smooth gray industrial metal
- coating_gray_worn_corrugated_iron — Weathered gray metal
- coating_orange_yellow_powder — Bright orange-yellow powder coating
- door_window_railing — Door and railing profiles
- generic — Fallback general material

Lighting Profiles:
- dark_strong — High contrast, strong key/rim lights (for dark/black materials)
- mid_standard — Balanced lighting (default, for mid-tone materials)
- light_soft — Soft diffuse lighting (for light/white materials)

Camera Styles:
- three_quarter — Classic e-commerce 3/4 view (default)
- front — Front-facing straight shot
- side — Side profile
- top_down — Top-down flat lay
- detail — Close-up detail shot

Backdrop:
- cyclorama — Seamless curved background (default)
- transparent — Alpha transparent background (PNG)
- solid — Solid color backdrop

Engine: CYCLES (production, default) | EEVEE (fast preview)

OUTPUT — respond with ONLY this JSON, no other text:

{
  "type": "intent",
  "intent": {
    "model_path": "<user-provided path, or empty string>",
    "output_path": "",
    "product_category": "<best category guess>",
    "material_mode": "enhance",
    "camera_style": "three_quarter",
    "backdrop": "cyclorama",
    "lighting_profile": "mid_standard",
    "engine": "CYCLES",
    "samples": 512,
    "transparent_background": false,
    "freecad_mm_obj": true,
    "normalize_max_dimension": 2.0
  }
}

RULES:
- If user mentions a color but no specific category, choose the closest matching category.
- If user says "freecad" or "型材" or "截面", set freecad_mm_obj=true.
- If user mentions "透明背景" or "PNG", set transparent_background=true, backdrop="transparent".
- If user mentions "快速" or "预览", consider EEVEE engine with lower samples.
- Fill all fields with reasonable defaults. Never leave a field empty.
- User's model_path will be filled by the server — set model_path to empty string.
"""


class LLMIntentParser:
    """One-shot LLM intent parser — converts user prompt to Render Intent JSON."""

    def __init__(self):
        api_key = settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self.client = Anthropic(api_key=api_key)
        self.model = settings.anthropic_model

    def parse(self, prompt: str, model_path: str = "", scene_params: dict | None = None) -> dict:
        """Convert user prompt to structured render intent.

        If ``scene_params`` is provided, the LLM uses those as a base and
        the prompt is treated as a refinement on top of the preset.
        """
        messages = []

        # If scene is selected, provide context
        base_context = ""
        if scene_params:
            base_context = (
                f"The user has selected a scene preset with these base parameters:\n"
                f"{json.dumps(scene_params, indent=2, ensure_ascii=False)}\n\n"
                f"Now the user says: "
            )

        user_content = f"Model path: {model_path}\n\n{base_context}{prompt}" if model_path else f"{base_context}{prompt}"
        messages.append({"role": "user", "content": user_content})

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        raw = response.content[0].text.strip()
        return self._parse_response(raw, scene_params)

    def _parse_response(self, raw: str, scene_params: dict | None = None) -> dict:
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # Fallback to scene params or defaults
            return self._fallback_intent(scene_params)

        if result.get("type") != "intent":
            return self._fallback_intent(scene_params)

        intent = result.get("intent", {})
        return {"type": "intent", "intent": self._apply_defaults(intent, scene_params)}

    def _fallback_intent(self, scene_params: dict | None = None) -> dict:
        defaults = {
            "model_path": "", "output_path": "",
            "product_category": "generic",
            "material_mode": "enhance",
            "camera_style": "three_quarter",
            "backdrop": "cyclorama",
            "lighting_profile": "mid_standard",
            "engine": "CYCLES", "samples": 512,
            "transparent_background": False,
            "freecad_mm_obj": True,
            "normalize_max_dimension": 2.0,
        }
        if scene_params:
            defaults.update(scene_params)
        return {"type": "intent", "intent": defaults}

    def _apply_defaults(self, intent: dict, scene_params: dict | None = None) -> dict:
        base = {
            "model_path": "", "output_path": "",
            "product_category": "generic",
            "material_mode": "enhance",
            "camera_style": "three_quarter",
            "backdrop": "cyclorama",
            "lighting_profile": "mid_standard",
            "engine": "CYCLES", "samples": 512,
            "transparent_background": False,
            "freecad_mm_obj": True,
            "normalize_max_dimension": 2.0,
        }
        if scene_params:
            base.update(scene_params)
        base.update({k: v for k, v in intent.items() if v not in (None, "", {})})
        return base
