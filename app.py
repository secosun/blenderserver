import sys
import json
import os

# Ensure internal paths are accessible for the server environment
sys.path.append('/content/blenderserver')
sys.path.append('/content/blendermcp/src')

from logic.intent_parser import LLMIntentParser

class BlenderServer:
    def __init__(self):
        self.parser = LLMIntentParser()

    def handle_request(self, user_prompt, asset_path, output_dir, run_blender_func):
        """
        Main workflow: Parse Intent -> Stage 1: EEVEE Preview -> User Confirmation -> Stage 2: Cycles HD
        """
        print(f"\n[BlenderServer] Processing user request: {user_prompt}")

        # 1. Parse natural language to technical Intent
        preview_path = os.path.join(output_dir, "api_preview.png")
        intent_data = self.parser.parse_requirement(user_prompt, asset_path, preview_path)

        # 2. Stage 1: EEVEE Preview (Intelligent Fallback applied in executor)
        # Construction of the script for the Blender subprocess
        blender_preview_script = f"""
import sys
import json
sys.path.append('/content/blendermcp/src')
from executor_v2 import SmartIntentExecutor

intent_json = {json.dumps(json.dumps(intent_data))}
executor = SmartIntentExecutor(intent_json)
# Run in preview mode using EEVEE
result = executor.run(preview_mode=True)
print(f'STAGE_1_PREVIEW:{{result}}')
"""
        print("[BlenderServer] Initiating Stage 1: EEVEE Preview Render...")
        run_blender_func(blender_preview_script)

        # 3. User Confirmation Simulation
        # In a production environment, this would pause for an API callback or UI event.
        print("[BlenderServer] Preview generated. Simulated User Confirmation: SUCCESS.")

        # 4. Stage 2: Cycles HD Rendering
        hd_path = os.path.join(output_dir, "api_final_hd.png")
        intent_data['output_path'] = hd_path

        blender_hd_script = f"""
import sys
import json
sys.path.append('/content/blendermcp/src')
from executor_v2 import SmartIntentExecutor

intent_json = {json.dumps(json.dumps(intent_data))}
executor = SmartIntentExecutor(intent_json)
# Run in high-definition mode using Cycles
result = executor.run(preview_mode=False)
print(f'STAGE_2_HD:{{result}}')
"""
        print("[BlenderServer] Initiating Stage 2: Cycles HD Production Render...")
        run_blender_func(blender_hd_script)

        return {
            "status": "completed",
            "preview_image": preview_path,
            "final_image": hd_path
        }