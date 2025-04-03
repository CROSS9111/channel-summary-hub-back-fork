import os
import json
import logging
import requests
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
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
import yt_dlp
import re


# .env.local を優先して読み込み
load_dotenv(".env.local", override=True)

# ロギング設定（DEBUG レベルのログをコンソール出力）
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI()

# DBセッションの依存関係
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# CORS ミドルウェアの追加（必要に応じて allow_origins 等を設定）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080/", "*"],  # 全オリジンを許可（本番環境では制限を推奨）
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# RedisTaskQueue インスタンスを作成（環境変数からホスト名などを取得）
redis_task_queue = RedisTaskQueue(
    redis_host=os.getenv("REDIS_HOST", "localhost"),
    redis_port=int(os.getenv("REDIS_PORT", "6379")),
    redis_db=int(os.getenv("REDIS_DB", "0")),
    redis_password=os.getenv("REDIS_PASSWORD")
)

logger.debug(f"redis_host={os.getenv('REDIS_HOST', 'localhost')}")

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
    channel_id: str
    updated_at: datetime
    summary: str
    keyPoints: str

class UserSummariesResponse(BaseModel):
    userId: str
    summaries: list[VideoSummary]

# チャンネル向けのレスポンスモデル
class ChannelSummariesResponse(BaseModel):
    channel_name: str
    summaries: list[VideoSummary]

# --- POST エンドポイント: ユーザーとチャンネルの紐付け（UserChannel の作成） ---
class UserChannelCreate(BaseModel):
    user_id: str  # UUID を文字列として受け取る
    channel_id: str

# 出力用の Pydantic モデル（Channel の一部を返す）
class ChannelOut(BaseModel):
    id: int
    channel_id: str
    channel_name: Optional[str] = None
    last_checked: Optional[datetime] = None

    class Config:
        orm_mode = True

# レスポンス用のPydanticモデル
class ChannelResponse(BaseModel):
    channel_id: str
    channel_name: str
    channel_description: Optional[str] = None
    channel_thumbnail_url: Optional[str] = None
    subscriber_count: Optional[int] = None
    video_count: Optional[int] = None
    view_count: Optional[int] = None
    published_at: Optional[datetime] = None

class ChannelSummariesResponse(BaseModel):
    channel_name: str
    channel_description: Optional[str] = None
    channel_thumbnail_url: Optional[str] = None
    subscriber_count: Optional[int] = None
    video_count: Optional[int] = None
    view_count: Optional[int] = None
    published_at: Optional[datetime] = None
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
        # dateutil.parser.parse を利用すると "Z" も処理可能
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

def fetch_channel_details(channel_id: str):
    """
    YouTube Data API からチャンネルの詳細情報を取得する関数
    """
    API_KEY = os.getenv("YOUTUBE_API_KEY")
    url = "https://www.googleapis.com/youtube/v3/channels"
    params = {
        "part": "snippet,statistics",
        "id": channel_id,
        "key": API_KEY
    }
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()

    if not data.get("items"):
        raise ValueError("チャンネルが見つかりませんでした。")

    item = data["items"][0]
    snippet = item["snippet"]
    stats = item["statistics"]

    return {
        "channel_id": channel_id,
        "channel_name": snippet["title"],
        "channel_description": snippet.get("description"),
        "channel_thumbnail_url": snippet["thumbnails"]["high"]["url"],
        "subscriber_count": int(stats.get("subscriberCount", 0)),
        "video_count": int(stats.get("videoCount", 0)),
        "view_count": int(stats.get("viewCount", 0)),
        "published_at": dateutil.parser.parse(snippet["publishedAt"])
    }

@app.get("/")
def status_check():
    return "ready"

@app.post("/summarize", response_model=SummaryResponse)
def summarize_youtube(request: SummaryRequest):
    youtube_url = request.youtube_url
    userId = request.userId
    
    try:
        # yt-dlpを使用してビデオ情報を取得
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'skip_download': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            
        if not info:
            raise HTTPException(status_code=400, detail="動画情報が取得できませんでした。URLを確認してください。")
            
        video_id = info.get('id')
        if not video_id:
            raise HTTPException(status_code=400, detail="動画IDが抽出できませんでした。URLを確認してください。")
            
        # video_detailsをyt-dlpの結果から作成
        video_details = {
            "snippet": {
                "channelId": info.get('channel_id', ''),
                "channelTitle": info.get('channel', ''),
                "title": info.get('title', ''),
                "description": info.get('description', ''),
                "publishedAt": info.get('upload_date', ''),
                "thumbnails": {
                    "default": {"url": info.get('thumbnail', '')},
                    "medium": {"url": info.get('thumbnail', '')},
                    "high": {"url": info.get('thumbnail', '')}
                }
            }
        }
        
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
            user_id=userId,
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

    # 字幕の取得を試みる
    try:
        transcript_text = ""
        # yt-dlpで利用可能な字幕一覧を取得
        available_subtitles = info.get('subtitles', {})
        automatic_subtitles = info.get('automatic_captions', {})
        
        # 優先順位: 日本語の手動字幕 -> 英語の手動字幕 -> 日本語の自動字幕 -> 英語の自動字幕
        subtitle_text = None
        
        # 手動字幕の確認
        if 'ja' in available_subtitles and available_subtitles['ja']:
            subtitle_url = available_subtitles['ja'][0]['url']
            subtitle_text = _download_and_parse_subtitle(subtitle_url)
        elif 'en' in available_subtitles and available_subtitles['en']:
            subtitle_url = available_subtitles['en'][0]['url']
            subtitle_text = _download_and_parse_subtitle(subtitle_url)
        # 自動字幕の確認
        elif 'ja' in automatic_subtitles and automatic_subtitles['ja']:
            subtitle_url = automatic_subtitles['ja'][0]['url']
            subtitle_text = _download_and_parse_subtitle(subtitle_url)
        elif 'en' in automatic_subtitles and automatic_subtitles['en']:
            subtitle_url = automatic_subtitles['en'][0]['url']
            subtitle_text = _download_and_parse_subtitle(subtitle_url)
            
        if subtitle_text:
            db_video.transcript_text = subtitle_text
            session.commit()
            
            # 字幕が取得できた場合は、要約タスクを Redis に登録
            redis_task_queue.add_task("summarize_text", "high", db_video.youtube_video_id)
            response_message = "字幕が取得され、要約タスクを投入しました。"
        else:
            # 字幕が取得できなかった場合は、音声取得タスクを登録
            redis_task_queue.add_task("download_audio", "high", db_video.id, youtube_url)
            response_message = "字幕が取得できなかったため、音声取得タスクを投入しました。"
            
    except Exception as e:
        logger.warning(f"DEBUG: 字幕取得に失敗: {e}")
        # 字幕が取得できなかった場合は、音声取得タスクを登録
        redis_task_queue.add_task("download_audio", "high", db_video.id, youtube_url)
        response_message = "字幕が取得できなかったため、音声取得タスクを投入しました。"

    session.close()

    return SummaryResponse(
        summary=response_message,
        points="",
        video_details=video_details
    )

def _download_and_parse_subtitle(subtitle_url):
    try:
        response = requests.get(subtitle_url)
        if response.status_code == 200:
            subtitle_content = response.text
            content_type = response.headers.get('Content-Type', '').lower()

            # Content-Type に 'json' が含まれていれば JSON とみなす
            if 'json' in content_type:
                try:
                    subtitle_json = json.loads(subtitle_content)
                    # JSONパース後の処理
                    return _parse_json_subtitle(subtitle_json)
                except json.JSONDecodeError:
                    # JSON と判断したがパースに失敗した場合、素のテキストを返す
                    return subtitle_content

            # Content-Type に 'text/vtt' が含まれていれば VTT とみなす
            if 'text/vtt' in content_type:
                return _parse_vtt_subtitle(subtitle_content)

            # 上記に該当しない場合は拡張子で判定する(後方互換用)
            if subtitle_url.endswith('.vtt'):
                return _parse_vtt_subtitle(subtitle_content)
            elif subtitle_url.endswith('.json'):
                try:
                    subtitle_json = json.loads(subtitle_content)
                    return _parse_json_subtitle(subtitle_json)
                except json.JSONDecodeError:
                    return subtitle_content

            # いずれにも該当しなければ、そのまま返す
            return subtitle_content

    except Exception as e:
        logger.error(f"字幕のダウンロードまたはパースに失敗: {e}")
    return ""

def _parse_vtt_subtitle(subtitle_content):
    lines = subtitle_content.split('\n')
    text_lines = []
    for line in lines:
        # タイムスタンプ行や空行、メタデータ行をスキップ
        if re.match(r'\d{2}:\d{2}:\d{2}', line):
            continue
        if not line.strip():
            continue
        if line.startswith('WEBVTT'):
            continue
        text_lines.append(line.strip())
    return ' '.join(text_lines)

def _parse_json_subtitle(subtitle_json):
    if 'events' not in subtitle_json:
        # events がなければそのまま
        return json.dumps(subtitle_json, ensure_ascii=False)

    text_segments = []
    for event in subtitle_json['events']:
        segs = event.get('segs')
        if not segs:
            continue
        for seg in segs:
            seg_text = seg.get('utf8', '').strip()
            # 改行のみや [音楽] などの注釈を除外
            if not seg_text:
                continue
            if seg_text == '\n':
                continue
            if seg_text.startswith('[') and seg_text.endswith(']'):
                continue
            text_segments.append(seg_text)
    return ' '.join(text_segments)

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
                channel_id=str(video.channel_id),
                thumbnail_high=video.thumbnail_high,
                updated_at=video.updated_at,
                summary="",
                keyPoints=""
            ))
        return UserSummariesResponse(userId=str(user.id), summaries=summaries)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()

# 動画IDを指定して、要約の詳細情報を返すエンドポイント
@app.get("/videos/{video_id}/summary", response_model=VideoSummary)
def get_video_summary(video_id: str):
    session: Session = SessionLocal()
    try:
        video = session.query(Video).filter(
            Video.youtube_video_id == video_id,
            Video.summary_text.isnot(None)
        ).first()
        if not video:
            raise HTTPException(status_code=404, detail="動画の要約が見つかりません。")
        return VideoSummary(
            videoId=video.youtube_video_id,
            title=video.title,
            summary_date=video.updated_at.isoformat() if video.updated_at else None,
            channel_name=video.channel_title,
            channel_id=str(video.channel_id),
            thumbnail_high=video.thumbnail_high,
            updated_at=video.updated_at,
            summary=video.summary_text or "",
            keyPoints=video.final_points or ""
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()

@app.get("/channels/{channel_id}/summaries", response_model=ChannelSummariesResponse)
def get_channel_summaries_by_string_id(channel_id: str):
    """
    文字列の YouTube チャンネルIDを route param で受け取り、
    channels テーブルの channel_id カラム(VARCHAR)と照合する。
    """
    session: Session = SessionLocal()
    try:
        # 1) channels テーブルを YouTubeの文字列ID で検索
        channel = session.query(Channel).filter(Channel.channel_id == channel_id).first()
        if not channel:
            raise HTTPException(status_code=404, detail="チャンネルが見つかりません。")

        # 2) channel.id を利用し、要約済み動画を取得
        videos = (
            session.query(Video)
            .filter(Video.channel_id == channel.id, Video.summary_text.isnot(None))
            .order_by(Video.updated_at.desc())
            .all()
        )

        # 3) Pydantic用に VideoSummary のリストを作成
        summaries = []
        for video in videos:
            summaries.append(VideoSummary(
                videoId=video.youtube_video_id,
                title=video.title,
                summary_date=video.updated_at.isoformat() if video.updated_at else None,
                channel_name=video.channel_title,
                channel_id=str(video.channel_id),
                thumbnail_high=video.thumbnail_high,
                updated_at=video.updated_at,
                summary=video.summary_text or "",
                keyPoints=video.final_points or ""
            ))

        # 4) チャンネルの詳細情報をセットして返却
        return ChannelSummariesResponse(
            channel_name=channel.channel_name or "",
            channel_description=channel.channel_description,
            channel_thumbnail_url=channel.channel_thumbnail_url,
            subscriber_count=channel.subscriber_count,
            video_count=channel.video_count,
            view_count=channel.view_count,
            published_at=channel.published_at,
            summaries=summaries
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()

@app.post("/user_channels")
def create_user_channel(req: UserChannelCreate, db: Session = Depends(get_db)):
    """
    user_id: UUID (auth.users.id)
    channel_id: str (YouTube channelId)
    """
    # 1) 既存の user_channels を数値の channel.id で検索しないこと
    #    → まずは channels テーブルを find-or-create する

    # 1. YouTubeのチャンネル詳細を取得
    details = fetch_channel_details(req.channel_id)

    # 2. channels テーブルにあるか検索（channel_id 列と照合）
    channel = db.query(Channel).filter(Channel.channel_id == req.channel_id).first()

    if not channel:
        # ない場合は新規作成
        channel = Channel(
            channel_id=details["channel_id"],
            channel_name=details["channel_name"],
            channel_description=details["channel_description"],
            channel_thumbnail_url=details["channel_thumbnail_url"],
            subscriber_count=details["subscriber_count"],
            video_count=details["video_count"],
            view_count=details["view_count"],
            published_at=details["published_at"],
        )
        db.add(channel)
        db.commit()
        db.refresh(channel)  # channel.id (数値) が発行される

    # 3. user_channels テーブルで、(user_id, channel.id) の組を探す
    assoc = db.query(UserChannel).filter(
        UserChannel.user_id == req.user_id,
        UserChannel.channel_id == channel.id
    ).first()

    if assoc:
        raise HTTPException(status_code=400, detail="指定のユーザーはすでにこのチャンネルに紐付いています。")

    # 4. 新しい紐付けを作成
    new_assoc = UserChannel(
        user_id=req.user_id,
        channel_id=channel.id
    )
    db.add(new_assoc)
    db.commit()
    db.refresh(new_assoc)

    # 保存したチャンネルの詳細を返却
    return details

# --- GET エンドポイント: ユーザーIDを元に登録チャンネル一覧を取得 ---
@app.get("/users/{user_id}/channels", response_model=list[ChannelResponse])
def get_user_channels(user_id: str, db: Session = Depends(get_db)):
    associations = db.query(UserChannel).filter(UserChannel.user_id == user_id).all()
    if not associations:
        return []

    channel_list = []
    for assoc in associations:
        ch = db.query(Channel).filter(Channel.id == assoc.channel_id).first()
        if ch:
            channel_list.append(ChannelResponse(
                channel_id=ch.channel_id,
                channel_name=ch.channel_name or "",
                channel_description=ch.channel_description,
                channel_thumbnail_url=ch.channel_thumbnail_url,
                subscriber_count=ch.subscriber_count,
                video_count=ch.video_count,
                view_count=ch.view_count,
                published_at=ch.published_at
            ))
    return channel_list
