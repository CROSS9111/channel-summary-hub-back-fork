import os
import redis
import json
import logging
from dotenv import load_dotenv

load_dotenv(".env")

if os.getenv("ENV") == "LOCAL":
    load_dotenv(".env.local", override=True)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

class RedisTaskQueue:
    def __init__(
        self,
        redis_host=os.getenv("REDIS_HOST", "localhost"),
        redis_port=int(os.getenv("REDIS_PORT", "6379")),
        redis_db=int(os.getenv("REDIS_DB", "0")),
        redis_password=os.getenv("REDIS_PASSWORD"),  # ここでパスワードを渡す
        queue_name_high="task_queue_high",
        queue_name_low="task_queue_low",
    ):
        self.r = redis.Redis(host=redis_host, port=redis_port, db=redis_db, password=redis_password)
        self.queue_high = queue_name_high
        self.queue_low = queue_name_low

    def add_task(self, task_func_name: str, priority: str = "low", *args, **kwargs):
        """
        task_func_name: タスクを識別する文字列
        priority: "high" もしくは "low"（デフォルトは low）
        *args, **kwargs: タスク実行時に渡す引数
        """
        task = {"task": task_func_name, "args": args, "kwargs": kwargs}
        task_json = json.dumps(task)
        if priority.lower() == "high":
            self.r.rpush(self.queue_high, task_json)
            logger.info(f"Enqueued high priority task: {task}")
        else:
            self.r.rpush(self.queue_low, task_json)
            logger.info(f"Enqueued low priority task: {task}")
