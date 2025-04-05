import mysql.connector
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv(".env.local", override=True)

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "mydatabase"),
}

# CREATE TABLE のクエリ（users テーブルを UUID 主キーに変更し、videos テーブルの user_id も CHAR(36) に変更）
CREATE_TABLE_QUERIES = [
    """
    CREATE TABLE IF NOT EXISTS `users` (
      `id` CHAR(36) PRIMARY KEY,
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
      `user_id` CHAR(36), 
      `channel_id` BIGINT NOT NULL,
      `youtube_video_id` VARCHAR(255) NOT NULL UNIQUE,
      `title` VARCHAR(255),
      `description` TEXT,
      `published_at` DATETIME,
      `channel_title` VARCHAR(255),
      `channel_youtube_id` VARCHAR(255),
      `thumbnail_default` VARCHAR(255),
      `thumbnail_medium` VARCHAR(255),
      `thumbnail_high` VARCHAR(255),
      `audio_url` VARCHAR(255),
      `transcript_text` LONGTEXT,
      `summary_text` LONGTEXT,
      `final_points` LONGTEXT,
      `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
      `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      CONSTRAINT `fk_videos_channels`
        FOREIGN KEY (`channel_id`) REFERENCES `channels`(`id`)
        ON DELETE RESTRICT
        ON UPDATE CASCADE,
      CONSTRAINT `fk_videos_users`
        FOREIGN KEY (`user_id`) REFERENCES `users`(`id`)
        ON DELETE RESTRICT
        ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """,
    """
    CREATE TABLE IF NOT EXISTS `user_channels` (
      `user_id` CHAR(36) NOT NULL,
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
      `task_type` ENUM('DOWNLOAD_AUDIO','TRANSCRIBE','SUMMARIZE') NOT NULL,
      `status` ENUM('PENDING','IN_PROGRESS','COMPLETED','FAILED') NOT NULL DEFAULT 'PENDING',
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
    """
]

def init_db():
    """既存テーブルを削除・再作成し、適当なダミーユーザーを登録する。"""
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    try:
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
        conn.commit()

        tables_to_drop = ["tasks", "user_channels", "videos", "channels", "users"]
        for table in tables_to_drop:
            drop_sql = f"DROP TABLE IF EXISTS `{table}`;"
            print(f"Executing: {drop_sql}")
            cursor.execute(drop_sql)
        
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
        conn.commit()

        for query in CREATE_TABLE_QUERIES:
            print("Executing CREATE TABLE...")
            cursor.execute(query)
        
        conn.commit()
        print("All tables have been created successfully.")

        # ダミーユーザーの登録（UUID を指定）
        dummy_user_id = "1558c67b-8562-4fed-ae17-cc38dff7bf9d"
        dummy_username = "dummy_user"
        dummy_password_hash = "dummy_hash"  # 実際はハッシュ化されたパスワードを保存する
        dummy_email = "dummy@example.com"
        insert_user_sql = """
            INSERT INTO `users` (id, username, password_hash, email)
            VALUES (%s, %s, %s, %s)
        """
        cursor.execute(insert_user_sql, (dummy_user_id, dummy_username, dummy_password_hash, dummy_email))
        conn.commit()
        print("Dummy user has been registered successfully.")
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    init_db()
