import os
import json
import logging
import requests
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from urllib.parse import urlparse, parse_qs
from youtube_transcript_api import YouTubeTranscriptApi
from dotenv import load_dotenv
from langchain.text_splitter import CharacterTextSplitter
import dateutil.parser

# モデルとDBセッション（SQLAlchemy）をインポート
from models import SessionLocal, User, Channel, Video, UserChannel  # モデル定義をインポート
from sqlalchemy.orm import Session

# 先ほど作成した RedisTaskQueue クラスをインポート
from redis_queue import RedisTaskQueue

load_dotenv(".env.local", override=True)

# ロギング設定（DEBUG レベルのログをコンソール出力）
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI()

# CORS ミドルウェアの追加（必要に応じて allow_origins 等を設定）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080/","*"],  # 全オリジンを許可（本番環境では制限を設けることを推奨）
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# RedisTaskQueue インスタンスの作成（環境変数からホスト名を取得）
redis_task_queue = RedisTaskQueue(
    redis_host=os.getenv("REDIS_HOST", "localhost"),
    redis_port=int(os.getenv("REDIS_PORT", "6379")),
    redis_db=int(os.getenv("REDIS_DB", "0")),
    redis_password=os.getenv("REDIS_PASSWORD")
)

print("redis_host",os.getenv("REDIS_HOST", "localhost"))

# ユーザー名を含めるようにリクエストモデルを修正
class SummaryRequest(BaseModel):
    youtube_url: str
    userId: str

class SummaryResponse(BaseModel):
    summary: str
    points: str
    video_details: dict

class VideoSummary(BaseModel):
    videoId: str
    title: str
    summary_date: str
    channel_name: str
    thumbnail_high: str
    channel_id:str
    updated_at:datetime
    summary:str
    keyPoints:str  

class UserSummariesResponse(BaseModel):
    username: str
    summaries: list[VideoSummary]

# 追加：チャンネル向けのレスポンスモデル
class ChannelSummariesResponse(BaseModel):
    channel_name: str
    summaries: list[VideoSummary]

def extract_video_id(url: str) -> str:
    parsed_url = urlparse(url)
    hostname = parsed_url.hostname.lower() if parsed_url.hostname else ""
    if hostname == "youtu.be":
        return parsed_url.path[1:]
    if hostname in ["www.youtube.com", "youtube.com"]:
        query = parse_qs(parsed_url.query)
        return query.get("v", [None])[0]
    return None

def get_video_details(video_id: str) -> dict:
    API_KEY = os.getenv("YOUTUBE_API_KEY")
    if not API_KEY:
        raise Exception("YOUTUBE_API_KEY が設定されていません。")
        
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet",
        "id": video_id,
        "key": API_KEY
    }
    response = requests.get(url, params=params)
    if not response.ok:
        raise Exception(f"YouTube Data API エラー: {response.status_code} {response.text}")
    data = response.json()
    if not data.get("items"):
        raise Exception("動画の詳細情報が取得できませんでした。")
    
    snippet = data["items"][0]["snippet"]
    
    # publishedAt の ISO8601 形式を datetime オブジェクトに変換
    published_at_str = snippet.get("publishedAt", "")
    if published_at_str:
        # dateutil.parser.parse を利用すると "Z" も処理可能です
        published_at_dt = dateutil.parser.parse(published_at_str)
        # MySQL 用にフォーマットを変更（例: 'YYYY-MM-DD HH:MM:SS'）
        published_at = published_at_dt.strftime("%Y-%m-%d %H:%M:%S")
    else:
        published_at = None

    channel_id = snippet.get("channelId", "")

    return {
        "id": video_id,
        "snippet": {
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "publishedAt": published_at,
            "channelTitle": snippet.get("channelTitle", ""),
            "channelId": channel_id,
            "thumbnails": {
                "default": {"url": snippet.get("thumbnails", {}).get("default", {}).get("url", "")},
                "medium": {"url": snippet.get("thumbnails", {}).get("medium", {}).get("url", "")},
                "high": {"url": snippet.get("thumbnails", {}).get("high", {}).get("url", "")}
            }
        }
    }

@app.get("/")
def status_check():
    return "ready"

@app.post("/summarize", response_model=SummaryResponse)
def summarize_youtube(request: SummaryRequest):
    youtube_url = request.youtube_url
    userId = request.userId
    video_id = extract_video_id(youtube_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="動画IDが抽出できませんでした。URLを確認してください。")
    
    try:
        video_details = get_video_details(video_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"動画詳細の取得に失敗: {e}")
    
    session = SessionLocal()

    # チャンネル情報の取得・登録
    channel_youtube_id = video_details["snippet"].get("channelId", "")
    if not channel_youtube_id:
        session.close()
        raise HTTPException(status_code=400, detail="チャンネルIDが取得できませんでした。")
    channel = session.query(Channel).filter(Channel.channel_id == channel_youtube_id).first()
    if not channel:
        channel = Channel(
            channel_id=channel_youtube_id,
            channel_name=video_details["snippet"].get("channelTitle", "")
        )
        session.add(channel)
        session.commit()
    
    # Video レコード作成（User の主キーも紐付ける）
    db_video = session.query(Video).filter(Video.youtube_video_id == video_id).first()
    if not db_video:
        db_video = Video(
            user_id=userId,  # ここでユーザー情報を紐付け
            channel_id=channel.id,
            youtube_video_id=video_id,
            title=video_details["snippet"].get("title", ""),
            description=video_details["snippet"].get("description", ""),
            published_at=video_details["snippet"].get("publishedAt"),
            channel_title=video_details["snippet"].get("channelTitle", ""),
            channel_youtube_id=channel_youtube_id,
            thumbnail_default=video_details["snippet"].get("thumbnails", {}).get("default", {}).get("url", ""),
            thumbnail_medium=video_details["snippet"].get("thumbnails", {}).get("medium", {}).get("url", ""),
            thumbnail_high=video_details["snippet"].get("thumbnails", {}).get("high", {}).get("url", "")
        )
        session.add(db_video)
        session.commit()

    try:
        # 字幕取得（優先言語: 日本語, 英語）
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=["ja", "en"])
        transcript_text = " ".join([item["text"] for item in transcript_list])
        db_video.transcript_text = transcript_text
        session.commit()

        # 字幕が取得できた場合は、要約タスクを Redis に登録
        redis_task_queue.add_task("summarize_text", "high", db_video.youtube_video_id)
        response_message = "字幕が取得され、要約タスクを投入しました。"
    except Exception as e:
        print(f"DEBUG: 字幕取得に失敗: {e}")
        # 字幕が取得できなかった場合は、音声取得タスクを登録
        redis_task_queue.add_task("download_audio", "high", db_video.id, youtube_url)
        response_message = "字幕が取得できなかったため、音声取得タスクを投入しました。"

    session.close()

    return SummaryResponse(
        summary=response_message,
        points="",
        video_details=video_details
    )

@app.get("/users/{user_id}/summaries", response_model=UserSummariesResponse)
def get_user_summaries(user_id: str):
    session: Session = SessionLocal()
    try:
        # ユーザーIDでユーザーを取得
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")
        
        # Video テーブルから user_id で直接フィルタリング
        videos = (
            session.query(Video)
            .filter(Video.user_id == user_id, Video.summary_text.isnot(None))
            .order_by(Video.updated_at.desc())
            .all()
        )
        
        summaries = []
        for video in videos:
            summaries.append(VideoSummary(
                videoId=video.youtube_video_id,
                title=video.title,
                summary_date=video.updated_at.isoformat() if video.updated_at else None,
                channel_name=video.channel_title,
                channel_id=str(video.channel_id),  # ここで channel_id を追加
                thumbnail_high=video.thumbnail_high,
                updated_at=video.updated_at,
                summary="",       # 要約情報
                keyPoints= ""         # 重要ポイント
            ))
        return UserSummariesResponse(username=user.username, summaries=summaries)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()

# 動画IDを指定して、要約の詳細情報を返すエンドポイント
@app.get("/videos/{video_id}/summary", response_model=VideoSummary)
def get_video_summary(video_id: str):
    session: Session = SessionLocal()
    try:
        video = session.query(Video).filter(Video.youtube_video_id == video_id, Video.summary_text.isnot(None)).first()
        if not video:
            raise HTTPException(status_code=404, detail="動画の要約が見つかりません。")
        return VideoSummary(
            videoId=video.youtube_video_id,
            title=video.title,
            summary_date=video.updated_at.isoformat() if video.updated_at else None,
            channel_name=video.channel_title,
            channel_id=str(video.channel_id),
            thumbnail_high=video.thumbnail_high,
            updated_at=video.updated_at,  # ここを追加
            summary=video.summary_text or "",       # 要約情報
            keyPoints=video.final_points or ""         # 重要ポイント
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()

@app.get("/channels/{channel_id}/summaries", response_model=ChannelSummariesResponse)
def get_channel_summaries(channel_id: int):
    session: Session = SessionLocal()
    try:
        # チャンネル情報を取得
        channel = session.query(Channel).filter(Channel.id == channel_id).first()
        if not channel:
            raise HTTPException(status_code=404, detail="チャンネルが見つかりません。")
        
        # 当該チャンネルに紐づく、要約済み動画（summary_text が存在する動画）を取得
        videos = (
            session.query(Video)
            .filter(Video.channel_id == channel_id, Video.summary_text.isnot(None))
            .order_by(Video.updated_at.desc())
            .all()
        )
        
        summaries = []
        for video in videos:
            summaries.append(VideoSummary(
                videoId=video.youtube_video_id,
                title=video.title,
                summary_date=video.updated_at.isoformat() if video.updated_at else None,
                channel_name=video.channel_title,
                channel_id=str(video.channel_id),
                thumbnail_high=video.thumbnail_high,
                updated_at=video.updated_at,  # ここを追加
                summary=video.summary_text,      # 要約情報
                keyPoints=video.final_points       # 重要ポイント
            ))
        return ChannelSummariesResponse(
            channel_name=channel.channel_name,
            summaries=summaries
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()