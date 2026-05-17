import sys
import json
import os
from logic.intent_parser import LLMIntentParser
from logic.pipeline_manager import PipelineManager

# SaaS context: append paths
sys.path.append('/content/blendermcp/src')

class BlenderServer:
    def __init__(self):
        self.parser = LLMIntentParser()
        self.orchestrator = PipelineManager()

    def handle_saas_request(self, user_id, user_prompt, asset_path, output_dir, run_blender_func):
        # A. Register Task in SaaS Orchestrator
        task_id = self.orchestrator.register_task(user_id, user_prompt)
        print(f"\n[SaaS-BlenderServer] New Task Registered: {task_id} for User: {user_id}")
        
        # B. Parse Intent
        self.orchestrator.update_status(task_id, "parsing_intent")
        preview_path = os.path.join(output_dir, f"{task_id}_preview.png")
        intent_data = self.parser.parse_requirement(user_prompt, asset_path, preview_path)

        # C. Dispatch Stage 1: EEVEE
        self.orchestrator.update_status(task_id, "rendering_preview")
        blender_script = f"""
import sys
import json
sys.path.append('/content/blendermcp/src')
from executor_v2 import SmartIntentExecutor
intent = {json.dumps(intent_data)}
executor = SmartIntentExecutor(json.dumps(intent))
print(f'TASK_ID:{task_id}_PREVIEW_DONE:' + str(executor.run(preview_mode=True)))
"""
        run_blender_func(blender_script)
        
        # D. Dispatch Stage 2: Cycles (Final Output)
        self.orchestrator.update_status(task_id, "rendering_hd")
        final_path = os.path.join(output_dir, f"{task_id}_final.png")
        intent_data['output_path'] = final_path
        
        hd_script = f"""
import sys
import json
sys.path.append('/content/blendermcp/src')
from executor_v2 import SmartIntentExecutor
intent = {json.dumps(intent_data)}
executor = SmartIntentExecutor(json.dumps(intent))
print(f'TASK_ID:{task_id}_HD_DONE:' + str(executor.run(preview_mode=False)))
"""
        run_blender_func(hd_script)
        
        self.orchestrator.update_status(task_id, "completed")
        return {"task_id": task_id, "preview": preview_path, "final": final_path}