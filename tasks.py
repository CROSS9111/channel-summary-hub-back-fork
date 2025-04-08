import os
import json
import tempfile
import subprocess
import glob
import logging
from datetime import datetime
import requests
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
import yt_dlp
from azure.storage.blob import BlobServiceClient
from models import SessionLocal, Video, DBTask   # それぞれの ORM モデル
from langchain.text_splitter import CharacterTextSplitter
import json
import os
from fastapi import HTTPException  # 必要に応じてインポート
from openai import AzureOpenAI, OpenAI

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)  # DEBUGレベルも出力する

load_dotenv(".env")

if os.getenv("ENV") == "LOCAL":
    load_dotenv(".env.local", override=True)

def download_audio(video_id: int, youtube_url: str):
    logger.info(f"[download_audio] Start video_id={video_id}, youtube_url={youtube_url}")
    session = SessionLocal()
    try:
        db_task = DBTask(
            video_id=video_id,
            task_type="DOWNLOAD_AUDIO",
            status="IN_PROGRESS",
            priority=9,
        )
        session.add(db_task)
        session.commit()
        logger.debug("DBTask for DOWNLOAD_AUDIO committed.")

        with tempfile.TemporaryDirectory() as tmpdir:
            logger.debug(f"Temporary directory created: {tmpdir}")
            audio_path_template = os.path.join(tmpdir, f"{video_id}.%(ext)s")
            logger.debug(f"Audio path template: {audio_path_template}")
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'outtmpl': audio_path_template,
                'cookiefile': os.getenv('YTDLP_COOKIEFILE', 'cookie.txt'),  # cookie.txt を利用
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'quiet': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                logger.debug("Starting audio download via yt_dlp...")
                ydl.download([youtube_url])
                logger.debug("Audio download finished.")
            audio_file_path = os.path.join(tmpdir, f"{video_id}.mp3")
            logger.debug(f"Checking for audio file at: {audio_file_path}")
            if not os.path.exists(audio_file_path):
                raise FileNotFoundError("Audio file download failed.")

            blob_conn_str = os.getenv("AZURE_BLOB_CONNECTION_STRING")
            container_name = os.getenv("AZURE_BLOB_CONTAINER", "youtube-audio")
            logger.debug(f"Blob connection string exists: {bool(blob_conn_str)}, container: {container_name}")
            if not blob_conn_str:
                raise Exception("Azure Blob connection string not set.")

            blob_service_client = BlobServiceClient.from_connection_string(blob_conn_str)
            container_client = blob_service_client.get_container_client(container_name)
            try:
                container_client.create_container()
                logger.debug("Container created successfully.")
            except Exception as ce:
                logger.debug("Container already exists or creation failed, ignoring: " + str(ce))
            blob_client = container_client.get_blob_client(f"{video_id}.mp3")
            with open(audio_file_path, "rb") as audio_data:
                logger.debug("Uploading audio file to Blob Storage...")
                blob_client.upload_blob(audio_data, overwrite=True)
                logger.debug("Audio file uploaded.")

        db_video = session.query(Video).filter(Video.id == video_id).first()
        if db_video:
            db_video.audio_url = blob_client.url
            session.commit()
            logger.debug(f"Video record updated with audio_url: {blob_client.url}")
        else:
            logger.error(f"Video record not found for video_id={video_id}")

        db_task.status = "COMPLETED"
        session.commit()
        logger.info("[download_audio] Completed successfully.")
    except Exception as e:
        logger.error(f"Error in download_audio: {e}")
        session.rollback()
    finally:
        session.close()

def transcribe_audio(video_id: int, audio_url: str):
    logger.info(f"[transcribe_audio] Start video_id={video_id}, audio_url={audio_url}")
    session = SessionLocal()

    # OpenAI クライアントの初期化
    openai_client = OpenAI()

    try:
        db_task = DBTask(
            video_id=video_id,
            task_type="TRANSCRIBE",
            status="IN_PROGRESS",
            priority=9,
        )
        session.add(db_task)
        session.commit()
        logger.debug("DBTask for TRANSCRIBE committed.")

        blob_conn_str = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
        container_name = os.getenv("AZURE_BLOB_CONTAINER", "youtube-audio")
        if not blob_conn_str:
            raise Exception("No Blob connection string set.")
        logger.debug(f"Blob connection string retrieved: {bool(blob_conn_str)}")

        blob_service_client = BlobServiceClient.from_connection_string(blob_conn_str)
        container_client = blob_service_client.get_container_client(container_name)
        blob_client = container_client.get_blob_client(f"{video_id}.mp3")

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_mp3:
            download_stream = blob_client.download_blob()
            with open(temp_mp3.name, "wb") as f:
                f.write(download_stream.readall())
            temp_mp3_path = temp_mp3.name
            logger.debug(f"Temporary mp3 file created: {temp_mp3_path}")

        file_size_mb = os.path.getsize(temp_mp3_path) / (1024 * 1024)
        logger.debug(f"Downloaded audio file size: {file_size_mb:.2f} MB")
        if file_size_mb > 20:
            logger.info(f"File > 20MB, need splitting. size={file_size_mb:.2f}MB")
            split_dir = tempfile.mkdtemp()
            logger.debug(f"Temporary split directory created: {split_dir}")
            split_pattern = os.path.join(split_dir, "split_%03d.mp3")
            ffmpeg_cmd = [
                "ffmpeg", "-i", temp_mp3_path,
                "-f", "segment",
                "-segment_time", "300",
                "-c", "copy",
                split_pattern
            ]
            logger.debug(f"Running ffmpeg command: {' '.join(ffmpeg_cmd)}")
            subprocess.run(ffmpeg_cmd, check=True)
            transcript_text = ""
            split_files = sorted(glob.glob(os.path.join(split_dir, "split_*.mp3")))
            logger.debug(f"Split files: {split_files}")
            # 分割ファイルごとに書き起こし
            for sf in split_files:
                logger.debug(f"Transcribing split file: {sf}")
                with open(sf, "rb") as audio_file:
                    transcription = openai_client.audio.transcriptions.create(
                        model="gpt-4o-transcribe",
                        file=audio_file,
                        response_format="text"
                    )
                transcript_text += transcription + "\n"
        else:
            logger.info(f"File size is {file_size_mb:.2f}MB, no splitting needed.")
            with open(temp_mp3_path, "rb") as audio_file:
                transcription = openai_client.audio.transcriptions.create(
                    model="gpt-4o-transcribe",
                    file=audio_file,
                    response_format="text"
                )
            transcript_text = transcription

        db_video = session.query(Video).filter(Video.id == video_id).first()
        if db_video:
            db_video.transcript_text = transcript_text
            session.commit()
            logger.debug("Video record updated with transcript text.")
        else:
            logger.error(f"Video record not found for video_id={video_id}")

        db_task.status = "COMPLETED"
        session.commit()
        logger.info("[transcribe_audio] Completed successfully.")
    except Exception as e:
        logger.error(f"Error in transcribe_audio: {e}")
        session.rollback()
    finally:
        session.close()
        
def summarize_text(youtube_video_id: str):
    client = AzureOpenAI(
        api_key = os.getenv("AZURE_OPENAI_KEY"),  
        api_version = os.getenv("AZURE_API_VER"),  
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    )
    logger.info(f"[summarize_text] Start youtube_video_id={youtube_video_id}")
    session = SessionLocal()
    try:
        # YouTube の動画IDで Video レコードを検索
        db_video = session.query(Video).filter(Video.youtube_video_id == youtube_video_id).first()
        if not db_video:
            raise Exception(f"Video record not found for youtube_video_id={youtube_video_id}.")
        logger.info(f"Found Video record with id={db_video.id} (type: {type(db_video.id)})")
        
        if not db_video.transcript_text:
            raise Exception(f"Transcript text not found in DB for video_id={db_video.id}.")
        logger.debug(f"Transcript text (first 500 chars): {db_video.transcript_text[:500]}")
        
        # DBTask を作成
        db_task = DBTask(
            video_id=db_video.id,
            task_type="SUMMARIZE",
            status="IN_PROGRESS",
            priority=9,
        )
        session.add(db_task)
        session.commit()
        logger.debug("DBTask for SUMMARIZE committed.")
        
        transcript_text = db_video.transcript_text
        logger.debug(f"Total transcript text length: {len(transcript_text)}")
        
        # 1000文字単位、100文字オーバーラップで分割する
        splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        chunks = splitter.split_text(transcript_text)
        logger.debug(f"Total chunks created: {len(chunks)}")
        
        summaries = []
        points_list = []
        
        # 各チャンク毎に要約を取得する
        for idx, chunk in enumerate(chunks):
            prompt = f"""次の書き起こしテキストを要約してください。**必ず純粋な JSON 形式のみ**で出力し、余計な説明文、装飾、マークダウンのコードブロックなどは一切含めないでください。以下のフォーマットに厳密に従って出力してください。

            {{
            "summary": "<要約文（マークダウン形式可）>",
            "points": "<重要なポイントを箇条書きにしたもの（各行が1項目）>"
            }}

            書き起こしテキスト:
            {chunk}
            """
            try:
                response = client.chat.completions.create(
                    model="o3-mini",
                    messages=[
                        {"role": "system", "content": "Assistant is a large language model trained by OpenAI."},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"}
                )
                raw_output = response.choices[0].message.content
                logger.debug(f"Chunk {idx} raw output: {raw_output[:200]}")  # 先頭部分をログ出力
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
        logger.debug(f"Merged summary length: {len(final_summary)}")
        
        # 要約結果を DB に保存（必要に応じてポイントも保存）
        db_video.summary_text = final_summary
        db_video.final_points = final_points  # 追加した部分
        session.commit()
        logger.info(f"Merged summary (first 500 chars): {final_summary[:500]}")
        logger.info(f"Final points (first 500 chars): {final_points[:500]}")

        db_task.status = "COMPLETED"
        session.commit()
        logger.info("[summarize_text] Completed successfully.")
    except Exception as e:
        logger.error(f"Error in summarize_text: {e}")
        session.rollback()
        video_pk = db_video.id if 'db_video' in locals() and db_video else None
        db_task = DBTask(
            video_id=video_pk,
            task_type="SUMMARIZE",
            status="FAILED",
            error_message=str(e),
        )
        session.add(db_task)
        session.commit()
    finally:
        session.close()

def process_chain_tasks(video_id: int, youtube_url: str):
    """
    タスクチェーンとして、音声ダウンロード → 書き起こし → 要約を順次実行する
    """
    logger.info(f"[process_chain_tasks] Start for video_id={video_id}, youtube_url={youtube_url}")
    # 1. 音声ダウンロード
    download_audio(video_id, youtube_url)
    
    # 2. ダウンロード後のVideoレコードからaudio_urlとyoutube_video_idを取得
    session = SessionLocal()
    try:
        db_video = session.query(Video).filter(Video.id == video_id).first()
        if not db_video:
            logger.error(f"Video record not found for video_id={video_id}")
            return
        audio_url = db_video.audio_url
        youtube_video_id = db_video.youtube_video_id  # 要約処理ではこちらを使用
        logger.debug(f"Retrieved from DB - audio_url: {audio_url}, youtube_video_id: {youtube_video_id}")
    finally:
        session.close()
    
    # 3. 書き起こし（audio_urlが取得できた場合）
    if audio_url:
        transcribe_audio(video_id, audio_url)
    else:
        logger.error("No audio_url found; skipping transcribe_audio.")
    
    # 4. 要約処理にはYouTubeの動画IDを渡す
    summarize_text(youtube_video_id)
    logger.info("[process_chain_tasks] Completed.")
