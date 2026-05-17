import os
import sys
from logic.intent_parser import LLMIntentParser
from logic.mcp_client import MCPBlenderClient

class BlenderServer:
    def __init__(self):
        self.parser = LLMIntentParser()
        # 核心：不再引用本地渲染代码，改用网络客户端
        self.mcp_portal = MCPBlenderClient(host="standalone-blender-server", port=19876)

    def handle_mcp_production_request(self, user_id, user_prompt, asset_uri):
        print(f"\n[SaaS-Middle-Tier] 接收到用户 {user_id} 的生产请求")
        
        # 1. 意图解析
        intent_data = self.parser.parse_requirement(user_prompt, asset_uri, f"cloud://output/{user_id}_final.png")
        
        # 2. 通过 19876 端口下发指令到独立服务器
        response = self.mcp_portal.send_render_intent(intent_data)
        
        return {
            "user_id": user_id,
            "mcp_server_response": response,
            "instruction": "Please check port 19876 logs on the rendering node."
        }