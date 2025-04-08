import os
import redis
import json
import time
import logging
from dotenv import load_dotenv
from tasks import summarize_text, process_chain_tasks

load_dotenv(".env")

if os.getenv("ENV") == "LOCAL":
    load_dotenv(".env.local", override=True)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# タスク名と実行関数のマッピング
task_mapping = {
    "summarize_text": summarize_text,
    "process_chain_tasks": process_chain_tasks,
    # 他のタスクを追加する場合はここに記述
}

def worker(
    redis_host=os.getenv("REDIS_HOST", "localhost"),
    redis_port=int(os.getenv("REDIS_PORT", "6379")),
    redis_db=int(os.getenv("REDIS_DB", "0")),
    redis_password=os.getenv("REDIS_PASSWORD"),
    queue_high="task_queue_high",
    queue_low="task_queue_low"
    ):
    r = redis.Redis(host=redis_host, port=redis_port, db=redis_db, password=redis_password)
    logger.info("Worker started, waiting for tasks...")
    while True:
        # 高優先度と低優先度のリストを順に指定
        task_data = r.brpop([queue_high, queue_low], timeout=10)
        if task_data:
            _, task_json = task_data
            try:
                task = json.loads(task_json)
                func_name = task.get("task")
                args = task.get("args", [])
                kwargs = task.get("kwargs", {})
                func = task_mapping.get(func_name)
                if func:
                    logger.info(f"Executing task {func_name} with args={args} kwargs={kwargs}")
                    func(*args, **kwargs)
                else:
                    logger.error(f"Unknown task: {func_name}")
            except Exception as e:
                logger.error(f"Error processing task: {e}")
        else:
            logger.info("No task received, waiting...")
            logger.info(f"redis_host;{os.getenv('REDIS_HOST', 'localhost')}")
            time.sleep(1)

if __name__ == '__main__':
    worker(
        redis_host=os.getenv("REDIS_HOST", "localhost"),
        redis_port=int(os.getenv("REDIS_PORT", "6379")),
        redis_db=int(os.getenv("REDIS_DB", "0")),
        redis_password=os.getenv("REDIS_PASSWORD"),
        queue_high="task_queue_high",
        queue_low="task_queue_low"
    )


