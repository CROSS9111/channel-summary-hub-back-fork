import mysql.connector
import os
from dotenv import load_dotenv


# .env.local から環境変数をロード
load_dotenv(".env.local", override=True)


DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),  # デフォルト値は "localhost"
    "user": os.getenv("DB_USER", "root"),       # デフォルト値は "root"
    "password": os.getenv("DB_PASSWORD", ""),   # デフォルト値は空
    "database": os.getenv("DB_NAME", "mydatabase"),  # デフォルト値 "mydatabase"
}

# 実行したい CREATE TABLE のSQL文をまとめる
CREATE_TABLE_QUERIES = [
    """
    CREATE TABLE IF NOT EXISTS `users` (
      `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
      `username` VARCHAR(255) NOT NULL UNIQUE,
      `password_hash` VARCHAR(255) NOT NULL,
      `email` VARCHAR(255) UNIQUE,
      `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
      `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    """
    CREATE TABLE IF NOT EXISTS `channels` (
      `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
      `channel_id` VARCHAR(255) NOT NULL UNIQUE,
      `channel_name` VARCHAR(255),
      `last_checked` DATETIME,
      `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
      `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    """
    CREATE TABLE IF NOT EXISTS `videos` (
      `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
      `channel_id` BIGINT NOT NULL,
      `video_id` VARCHAR(255) NOT NULL UNIQUE,
      `title` VARCHAR(255),
      `description` TEXT,
      `published_at` DATETIME,
      `channel_title` VARCHAR(255),
      `channel_youtube_id` VARCHAR(255),
      `thumbnail_default` VARCHAR(255),
      `thumbnail_medium` VARCHAR(255),
      `thumbnail_high` VARCHAR(255),
      `transcript_text` LONGTEXT,
      `summary_text` LONGTEXT,
      `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
      `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      CONSTRAINT `fk_videos_channels`
        FOREIGN KEY (`channel_id`) REFERENCES `channels`(`id`)
        ON DELETE RESTRICT
        ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    """
    CREATE TABLE IF NOT EXISTS `user_channels` (
      `user_id` BIGINT NOT NULL,
      `channel_id` BIGINT NOT NULL,
      `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (`user_id`, `channel_id`),
      CONSTRAINT `fk_user_channels_user`
        FOREIGN KEY (`user_id`) REFERENCES `users`(`id`)
        ON DELETE RESTRICT
        ON UPDATE CASCADE,
      CONSTRAINT `fk_user_channels_channel`
        FOREIGN KEY (`channel_id`) REFERENCES `channels`(`id`)
        ON DELETE RESTRICT
        ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    """
    CREATE TABLE IF NOT EXISTS `tasks` (
      `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
      `video_id` BIGINT NOT NULL,
      `task_type` VARCHAR(50) NOT NULL,
      `status` VARCHAR(20) NOT NULL,
      `retries` INT DEFAULT 0,
      `priority` INT DEFAULT 0,
      `scheduled_at` DATETIME,
      `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
      `started_at` DATETIME,
      `completed_at` DATETIME,
      `error_message` TEXT,
      `result_data` LONGTEXT,
      CONSTRAINT `fk_tasks_videos`
        FOREIGN KEY (`video_id`) REFERENCES `videos`(`id`)
        ON DELETE RESTRICT
        ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
]


def init_db():
    """データベースの既存テーブルを削除し、指定のテーブルを再作成する。"""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    try:
        # 外部キー制約を一時的に無効化 → テーブル削除を簡易化
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
        conn.commit()

        # 削除対象のテーブルを指定。順番に気をつけなくてもよいよう、全部まとめて削除
        tables_to_drop = ["tasks", "user_channels", "videos", "channels", "users"]
        for table in tables_to_drop:
            drop_sql = f"DROP TABLE IF EXISTS `{table}`;"
            print(f"Executing: {drop_sql}")
            cursor.execute(drop_sql)
        
        # 外部キー制約を再度有効化
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
        conn.commit()

        # CREATE TABLE を順に実行
        for query in CREATE_TABLE_QUERIES:
            print("Executing CREATE TABLE...")
            cursor.execute(query)
        
        conn.commit()
        print("All tables have been created successfully.")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    init_db()
