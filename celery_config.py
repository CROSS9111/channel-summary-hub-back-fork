import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
BACKEND_URL = os.getenv("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/1")

celery_app = Celery("my_project", broker=BROKER_URL, backend=BACKEND_URL)

# 優先度付きキューの設定例
celery_app.conf.task_queue_max_priority = 10
celery_app.conf.task_default_priority = 5

celery_app.conf.task_routes = {
    "tasks.high_priority.*": {"queue": "high_priority"},
    "tasks.low_priority.*": {"queue": "low_priority"},
}

# 任意: スケジュールを設定したい場合の例 (celery beat 用)
# celery_app.conf.beat_schedule = {
#     'check-new-videos-every-hour': {
#         'task': 'tasks.low_priority.check_new_videos',
#         'schedule': 3600.0,  # 1時間に1回
#         'args': ("UCxxxxxx",)
#     },
# }
