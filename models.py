import os
from dotenv import load_dotenv
from sqlalchemy import (
    Enum, Column, BigInteger, String, Text, DateTime, ForeignKey, Integer,
    func
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import BIGINT, UUID,JSONB
from datetime import datetime

load_dotenv(".env.local", override=True)

##MySQL
# DB_HOST = os.getenv("DB_HOST", "localhost")
# DB_USER = os.getenv("DB_USER", "root")
# DB_PASSWORD = os.getenv("DB_PASSWORD", "")
# DB_NAME = os.getenv("DB_NAME", "mydatabase")

#Supabase
USER = os.getenv("sb_user")
PASSWORD = os.getenv("sb_password")
HOST = os.getenv("sb_host")
PORT = os.getenv("sb_port")
DBNAME = os.getenv("sb_dbname")

# Supabaseの接続情報（環境変数から取得するのが望ましい）
DATABASE_URL = f"postgresql+psycopg2://{USER}:{PASSWORD}@{HOST}:{PORT}/{DBNAME}?sslmode=require"

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "auth"}

    id = Column(UUID(as_uuid=True), primary_key=True, index=True)
    # username = Column(String(255), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=True)
    # app_metadata = Column(JSONB, nullable=False, default=dict)
    # user_metadata = Column(JSONB, nullable=False, default=dict)
    # identities = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def username(self):
        # 例えば、user_metadata に "username" キーが存在する場合に取得
        return self.user_metadata.get("username")

class Channel(Base):
    __tablename__ = "channels"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    channel_id = Column(String(255), unique=True, nullable=False)
    channel_name = Column(String(255))
    channel_description = Column(Text)
    channel_thumbnail_url = Column(String(255))
    subscriber_count = Column(BigInteger)
    video_count = Column(BigInteger)
    view_count = Column(BigInteger)
    published_at = Column(DateTime(timezone=True))
    last_checked = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
#Supabase版
class Video(Base):
    __tablename__ = "videos"

    id = Column(BIGINT, primary_key=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id"), nullable=True)
    channel_id = Column(BIGINT, ForeignKey("channels.id"), nullable=False)
    youtube_video_id = Column(String(255), unique=True, nullable=False)
    title = Column(String(255))
    description = Column(Text)
    published_at = Column(DateTime)
    channel_title = Column(String(255))
    channel_youtube_id = Column(String(255))
    thumbnail_default = Column(String(255))
    thumbnail_medium = Column(String(255))
    thumbnail_high = Column(String(255))
    audio_url = Column(String, nullable=True)
    transcript_text = Column(Text)
    summary_text = Column(Text)
    final_points = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tasks = relationship("DBTask", back_populates="video", cascade="all, delete-orphan")


class DBTask(Base):
    __tablename__ = 'tasks'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Video の主キー(id)を参照
    video_id = Column(BigInteger, ForeignKey('videos.id'), nullable=False)

    task_type = Column(Enum('DOWNLOAD_AUDIO', 'TRANSCRIBE', 'SUMMARIZE'), nullable=False)
    status = Column(Enum('PENDING', 'IN_PROGRESS', 'COMPLETED', 'FAILED'), default='PENDING', nullable=False)
    retries = Column(Integer, default=0)
    priority = Column(Integer, default=0)
    scheduled_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    error_message = Column(Text)
    result_data = Column(Text)

    video = relationship("Video", back_populates="tasks")
    # Task（または DBTask）とのリレーションを追加

class UserChannel(Base):
    __tablename__ = "user_channels"

    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id"), primary_key=True)
    channel_id = Column(BigInteger, ForeignKey("channels.id"), primary_key=True)
    created_at = Column(DateTime, server_default=func.current_timestamp())
