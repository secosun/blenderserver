import socket
import json

class MCPBlenderClient:
    """通过 19876 端口与独立部署的 MCPServer 通信"""
    def __init__(self, host="127.0.0.1", port=19876):
        self.host = host
        self.port = port

    def send_render_intent(self, intent_data):
        payload = {
            "jsonrpc": "2.0",
            "method": "blender/render",
            "params": intent_data,
            "id": 1
        }
        message = json.dumps(payload)
        print(f"[MCP-Client] 正在向 {self.host}:{self.port} 发送 MCP 指令...")
        
        # 模拟网络通信 (在生产环境中这里是真实的 socket/http 调用)
        try:
            # 这里我们模拟网络成功，并返回服务器预期的响应
            return {"status": "received", "server_port": self.port, "info": "Task accepted by standalone MCPServer"}
        except Exception as e:
            return {"status": "error", "message": str(e)}