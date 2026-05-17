import uuid
import datetime

class PipelineManager:
    def __init__(self):
        self.active_tasks = {}

    def register_task(self, user_id, prompt):
        task_id = str(uuid.uuid4())
        self.active_tasks[task_id] = {
            "user_id": user_id,
            "prompt": prompt,
            "status": "queued",
            "created_at": datetime.datetime.now().isoformat(),
            "logs": []
        }
        return task_id

    def update_status(self, task_id, status, log_msg=None):
        if task_id in self.active_tasks:
            self.active_tasks[task_id]["status"] = status
            if log_msg:
                self.active_tasks[task_id]["logs"].append(log_msg)