import sys
import json
import os
from logic.intent_parser import LLMIntentParser
from logic.pipeline_manager import PipelineManager
from logic.task_broker import RenderTaskBroker

class BlenderServer:
    def __init__(self):
        self.parser = LLMIntentParser()
        self.orchestrator = PipelineManager()
        self.broker = RenderTaskBroker() # 引入任务经纪人

    def handle_distributed_request(self, user_id, user_prompt, asset_uri):
        """
        分布式模式：解析意图并发布消息，不直接执行渲染
        """
        task_id = self.orchestrator.register_task(user_id, user_prompt)
        
        # 1. 意图解析 (Middle-tier 逻辑)
        # 此时 output_path 变为远程存储占位符
        intent_data = self.parser.parse_requirement(user_prompt, asset_uri, f"cloud://bucket/renders/{task_id}.png")
        
        # 2. 分布式发布 (解耦的核心)
        message_id = self.broker.publish_task("RENDER_JOB", {
            "task_id": task_id,
            "user_id": user_id,
            "render_intent": intent_data
        })
        
        return {"task_id": task_id, "message_id": message_id, "status": "dispatched_to_worker"}