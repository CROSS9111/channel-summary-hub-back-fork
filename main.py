import os
import json
import requests
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs
from openai import AzureOpenAI
from dotenv import load_dotenv
from langchain.text_splitter import CharacterTextSplitter
import yt_dlp

# .env.local から環境変数をロード
load_dotenv(".env.local", override=True)

app = FastAPI()

class SummaryRequest(BaseModel):
    youtube_url: str

class SummaryResponse(BaseModel):
    summary: str
    points: str
    video_details: dict

def extract_video_id(url: str) -> str:
    """
    YouTube の URL から動画IDを抽出する関数
    """
    parsed_url = urlparse(url)
    hostname = parsed_url.hostname.lower() if parsed_url.hostname else ""
    if hostname in ["youtu.be"]:
        return parsed_url.path[1:]
    if hostname in ["www.youtube.com", "youtube.com"]:
        query = parse_qs(parsed_url.query)
        return query.get("v", [None])[0]
    return None

def get_video_details(video_id: str) -> dict:
    """
    YouTube oEmbed API を利用して動画詳細情報を取得する関数
    """
    oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    response = requests.get(oembed_url)
    if not response.ok:
        raise Exception(f"oEmbed API エラー: {response.status_code} {response.reason}")
    data = response.json()
    # oEmbed API には公開日情報がないため、現在時刻を利用
    published_at = datetime.utcnow().isoformat() + "Z"
    # channelId を author_url から抽出（例: "https://www.youtube.com/channel/UCxxxxxx"）
    channel_id = ""
    if "author_url" in data:
        parsed = urlparse(data["author_url"])
        parts = parsed.path.split("/")
        if "channel" in parts:
            idx = parts.index("channel")
            if idx + 1 < len(parts):
                channel_id = parts[idx+1]
    return {
        "id": video_id,
        "snippet": {
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "publishedAt": published_at,
            "channelTitle": data.get("author_name", ""),
            "channelId": channel_id,
            "thumbnails": {
                "default": {"url": data.get("thumbnail_url", "")},
                "medium": {"url": data.get("thumbnail_url", "")},
                "high": {"url": data.get("thumbnail_url", "")}
            }
        }
    }

# AzureOpenAI クライアントの初期化
client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_KEY"),
    api_version=os.getenv("AZURE_API_VER") or "2023-05-15",
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
)

@app.post("/summarize", response_model=SummaryResponse)
def summarize_youtube(request: SummaryRequest):
    youtube_url = request.youtube_url
    video_id = extract_video_id(youtube_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="動画IDが抽出できませんでした。URLを確認してください。")
    
    # 動画詳細情報を取得
    try:
        video_details = get_video_details(video_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"動画詳細の取得に失敗しました: {e}")
    
    transcript_text = ""
    try:
        # 書き起こしの取得を試みる（優先言語: 日本語・英語）
        # transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=["ja", "en"])
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=["en"])
    except Exception as e:
        transcript_list = None
        print(f"DEBUG: 書き起こしの取得に失敗: {e}")
    
    if transcript_list:
        transcript_text = " ".join([item["text"] for item in transcript_list])
    else:
        # 書き起こしが取得できなかった場合は、yt_dlp を利用して音声データをダウンロードし、Azure Blob Storage にアップロードする
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': f'{video_id}.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'ffmpeg_location': os.getcwd(),  # 現在のディレクトリに ffmpeg がある場合
            'quiet': True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([youtube_url])
            audio_file = f"{video_id}.mp3"
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"音声データのダウンロードに失敗しました: {e}")
        
        from azure.storage.blob import BlobServiceClient
        blob_connection_string = os.getenv("AZURE_BLOB_CONNECTION_STRING")
        container_name = os.getenv("AZURE_BLOB_CONTAINER") or "youtube-audio"
        if not blob_connection_string:
            raise HTTPException(status_code=500, detail="Azure Blob Storage の接続情報が設定されていません。")
        try:
            blob_service_client = BlobServiceClient.from_connection_string(blob_connection_string)
            container_client = blob_service_client.get_container_client(container_name)
            try:
                container_client.create_container()
            except Exception:
                pass
            blob_client = container_client.get_blob_client(f"{video_id}.mp3")
            with open(audio_file, "rb") as data:
                blob_client.upload_blob(data, overwrite=True)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"音声データのアップロードに失敗しました: {e}")
        
        # 書き起こしが取得できなかった場合、要約は実施せずアップロード情報のみを返す
        return SummaryResponse(
            summary=f"音声データが Azure Blob Storage にアップロードされました。Blob 名: {video_id}.mp3",
            points="",
            video_details=video_details
        )
    
    # 書き起こしが取得できた場合、1000文字ごとに100文字のオーバーラップで分割して要約処理を実施
    splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
    chunks = splitter.split_text(transcript_text)
    
    summaries = []
    points_list = []
    
    for chunk in chunks:
        prompt = f"""次の書き起こしテキストを要約してください。出力は JSON 形式かつマークダウン形式にしてください。以下の形式に従って出力してください:

{{
  "summary": "<マークダウン形式で書かれた要約文>",
  "points": "<重要なポイントを箇条書き（1行ごとに）でまとめたもの。マークダウン形式で必要な数だけ書き出すこと。>"
}}

書き起こしテキスト:
{chunk}
"""
        try:
            response = client.chat.completions.create(
                model=os.getenv("AZURE_MODEL") or "o3-mini",
                messages=[
                    {"role": "system", "content": "Assistant is a large language model trained by OpenAI."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
            )
            raw_output = response.choices[0].message.content
            try:
                output_json = json.loads(raw_output)
            except Exception as parse_error:
                raise Exception(f"返答のJSON解析に失敗しました: {parse_error}. 返答内容: {raw_output}")
            summaries.append(output_json.get("summary", ""))
            points_list.append(output_json.get("points", ""))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"要約処理に失敗しました: {e}")
    
    final_summary = "\n\n".join(summaries)
    final_points = "\n".join(points_list)
    
    return SummaryResponse(
        summary=final_summary,
        points=final_points,
        video_details=video_details
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
