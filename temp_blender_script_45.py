import sys
import json
sys.path.append('/content/blendermcp/src')
from executor_v2 import SmartIntentExecutor
intent = {"asset": {"path": "/content/blendermcp/\u7b80\u6613\u6b3e-BodyPad003.obj", "scale": 1.0}, "output_path": "/content/saas_production_outputs/b1d59714-82f1-4c8a-b616-a1b864f819fb_final.png", "camera": {"focus_mode": "auto", "distance_multiplier": 3.5}, "material": {"base_color": [0.05, 0.05, 0.05, 1.0], "metallic": 0.8, "roughness": 0.2}, "lighting": "studio"}
executor = SmartIntentExecutor(json.dumps(intent))
print(f'TASK_ID:b1d59714-82f1-4c8a-b616-a1b864f819fb_HD_DONE:' + str(executor.run(preview_mode=False)))