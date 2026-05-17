import json
import uuid

class RenderTaskBroker:
    """模拟分布式消息队列 (如 RabbitMQ/Redis) """
    def __init__(self):
        self.queue = []

    def publish_task(self, task_type, payload):
        message_id = str(uuid.uuid4())
        message = {
            "id": message_id,
            "type": task_type,
            "payload": payload,
            "routing_key": "render_worker_v1"
        }
        self.queue.append(message)
        print(f"[Broker] 任务已发布到队列: {message_id}")
        return message_id

    def consume_task(self):
        if self.queue: return self.queue.pop(0)
        return None