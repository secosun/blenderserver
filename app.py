import sys
import os
# 引用 blendermcp 作为依赖
sys.path.append('/content/blendermcp/src')
from logic.intent_parser import LLMIntentParser
from mcp_client_sdk import BlenderMCPClient

class BlenderServer:
    def __init__(self):
        self.parser = LLMIntentParser()
        self.bridge = BlenderMCPClient() # 中台持有一个 mcp 客户端句柄

    def handle_saas_request(self, user_id, prompt, asset_uri):
        print(f"\n[SaaS-Server] 接收请求: {user_id}")
        # A. 生成意图
        intent = self.parser.parse_requirement(prompt, asset_uri, f"cloud://{user_id}/out.png")
        # B. 通过 blendermcp 桥梁发送
        print("[SaaS-Server] 调用 blendermcp 管道...")
        return self.bridge.call_render(intent)