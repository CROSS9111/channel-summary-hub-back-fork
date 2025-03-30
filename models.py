import os
from dotenv import load_dotenv
from sqlalchemy import (
    Enum, Column, BigInteger, String, Text, DateTime, ForeignKey, Integer,
    func
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy import create_engine
from datetime import datetime

load_dotenv(".env.local", override=True)

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "mydatabase")

SQLALCHEMY_DATABASE_URL = (
    f"mysql+mysqlconnector://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}"
)

engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, autoincrement=True)
    username = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    email = Column(String(255), unique=True)
    created_at = Column(DateTime, server_default=func.current_timestamp())
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class Channel(Base):
    __tablename__ = "channels"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    channel_id = Column(String(255), unique=True, nullable=False)
    channel_name = Column(String(255))
    last_checked = Column(DateTime)
    created_at = Column(DateTime, server_default=func.current_timestamp())
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class Video(Base):
    """
    YouTube の動画IDを 'youtube_video_id' カラムに保存する想定。
    'id' は内部DB用の主キー（AUTO_INCREMENT）。
    """
    __tablename__ = "videos"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True)  # ユーザーとの紐付け用カラム
    channel_id = Column(BigInteger, ForeignKey("channels.id"), nullable=False)

    # YouTube の動画IDをここに保存
    youtube_video_id = Column(String(255), unique=True, nullable=False)

    title = Column(String(255))
    description = Column(Text)
    published_at = Column(DateTime)
    channel_title = Column(String(255))
    channel_youtube_id = Column(String(255))
    thumbnail_default = Column(String(255))
    thumbnail_medium = Column(String(255))
    thumbnail_high = Column(String(255))
    transcript_text = Column(Text)   # LONGTEXTにしてもOK
    summary_text = Column(Text)      # 同上
    final_points = Column(Text)  # 追加したカラム

    created_at = Column(DateTime, server_default=func.current_timestamp())
    updated_at = Column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    tasks = relationship("DBTask", back_populates="video")
    channel = relationship("Channel")


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


class UserChannel(Base):
    __tablename__ = "user_channels"

    user_id = Column(String(36), ForeignKey("users.id"), primary_key=True)
    channel_id = Column(BigInteger, ForeignKey("channels.id"), primary_key=True)
    created_at = Column(DateTime, server_default=func.current_timestamp())
