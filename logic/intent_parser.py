import json
import re

class LLMIntentParser:
    @staticmethod
    def parse_requirement(user_prompt, asset_path, output_path):
        """
        Translates fuzzy user requirements into a structured Render Intent JSON.
        """
        # Initialize standard structure with safe defaults
        intent = {
            "asset": {
                "path": asset_path,
                "scale": 1.0
            },
            "output_path": output_path,
            "camera": {
                "focus_mode": "auto",
                "distance_multiplier": 3.5
            },
            "material": {
                "base_color": [0.8, 0.8, 0.8, 1.0],
                "metallic": 0.0,
                "roughness": 0.5
            },
            "lighting": "studio"
        }

        prompt_low = user_prompt.lower()

        # Keyword-based mapping logic (Simulating LLM intent extraction)
        if any(kw in prompt_low for kw in ["premium", "high-end", "luxury", "sleek"]):
            intent["material"] = {"base_color": [0.05, 0.05, 0.05, 1.0], "metallic": 0.9, "roughness": 0.1}
            intent["lighting"] = "studio"
            intent["camera"]["distance_multiplier"] = 4.0
        
        elif any(kw in prompt_low for kw in ["industrial", "rugged", "heavy duty", "metal"]):
            intent["material"] = {"base_color": [0.4, 0.4, 0.42, 1.0], "metallic": 0.85, "roughness": 0.35}
            intent["lighting"] = "outdoor"
            
        elif "glass" in prompt_low:
            intent["material"] = {"base_color": [0.9, 0.95, 1.0, 0.2], "metallic": 0.0, "roughness": 0.02}
            intent["lighting"] = "studio"

        return intent