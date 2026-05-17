import sys
import os
from logic.intent_parser import LLMIntentParser
from logic.mcp_network_client import MCPBlenderNetworkClient

class BlenderServer:
    def __init__(self, render_node_ip="standalone-gpu-node"):
        self.parser = LLMIntentParser()
        # 初始化网络客户端，对接 19876 端口
        self.mcp_client = MCPBlenderNetworkClient(host=render_node_ip, port=19876)

    def handle_saas_production_request(self, user_id, user_prompt, asset_uri):
        print(f"\n[SaaS-Middle-Tier] 接收到分布式渲染请求: User={user_id}")
        
        # 1. 意图解析 (中台核心价值：模糊需求 -> 技术意图)
        # 此时 output_path 指向分布式的云存储路径
        intent_data = self.parser.parse_requirement(user_prompt, asset_uri, f"cloud://bucket/renders/{user_id}_final.png")
        
        # 2. 通过 19876 端口跨网络调度独立部署的 Blender
        print("[SaaS-Middle-Tier] 正在跨网络调度独立渲染节点...")
        response = self.mcp_client.execute_render(intent_data)
        
        return {
            "task_status": "dispatched",
            "mcp_response": response,
            "storage_uri": intent_data['output_path']
        }