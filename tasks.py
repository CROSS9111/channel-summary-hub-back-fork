import os
import tempfile
import subprocess
import glob
import logging
from celery import Celery
from celery.utils.log import get_task_logger
from azure.storage.blob import BlobServiceClient
from openai import OpenAI
from db_models import SessionLocal, Video, Task as DBTask  # DBTaskはTaskテーブルのORMモデル
from celery_config import celery_app

logger = get_task_logger(__name__)

@celery_app.task(name="tasks.high_priority.transcribe_audio", bind=True, queue="high_priority", priority=9, max_retries=2)
def transcribe_audio(self, video_id: str, audio_url: str):
    """
    Blobから音声ファイルをダウンロードし、OpenAIの音声書き起こしAPI（gpt-4o-transcribe）でテキスト化するタスク。
    20MBを超える場合はffmpegで分割処理し、各分割ファイルに対してSTTを実施します。
    タスク失敗時は最大2回まで自動再試行します。
    """
    logger.info(f"[transcribe_audio] Start video_id={video_id}, audio_url={audio_url}")
    session = SessionLocal()
    try:
        # タスクレコードをDBに登録
        db_task = DBTask(
            video_id=video_id,  # ※Videoテーブルのvideo_idフィールドと一致する値
            task_type="TRANSCRIBE",
            status="IN_PROGRESS",
            priority=9,
        )
        session.add(db_task)
        session.commit()

        # 1. Azure Blob Storageから音声ファイルを一時ディレクトリにダウンロード
        blob_conn_str = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
        container_name = os.getenv("AZURE_BLOB_CONTAINER", "youtube-audio")
        if not blob_conn_str:
            raise Exception("No Blob connection string set.")

        blob_service_client = BlobServiceClient.from_connection_string(blob_conn_str)
        container_client = blob_service_client.get_container_client(container_name)
        blob_client = container_client.get_blob_client(f"{video_id}.mp3")

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_mp3:
            download_stream = blob_client.download_blob()
            download_stream.readinto(temp_mp3)
            temp_mp3_path = temp_mp3.name

        # 2. ファイルサイズチェック → 20MB超ならffmpegで分割（※ここでは分割処理の詳細は省略）
        file_size_mb = os.path.getsize(temp_mp3_path) / (1024 * 1024)
        if file_size_mb > 20:
            logger.info(f"File > 20MB, need splitting. size={file_size_mb:.2f}MB")
            # 例: ffmpegで分割（ここでは簡易例。実際は各分割ファイルごとにSTTを実施する必要があります）
            split_dir = tempfile.mkdtemp()
            split_pattern = os.path.join(split_dir, "split_%03d.mp3")
            ffmpeg_cmd = [
                "ffmpeg", "-i", temp_mp3_path,
                "-f", "segment",
                "-segment_time", "300",  # 300秒ごとに分割
                "-c", "copy",
                split_pattern
            ]
            subprocess.run(ffmpeg_cmd, check=True)
            # 分割されたファイルすべてについて、STTを実施して結果を結合（ここではダミー実装）
            transcript_text = ""
            split_files = sorted(glob.glob(os.path.join(split_dir, "split_*.mp3")))
            for sf in split_files:
                # ※各ファイルについて後述の書き起こし処理を実施（下記と同様の処理をループ）
                with open(sf, "rb") as audio_file:
                    openai_client = OpenAI()
                    transcription = openai_client.audio.transcriptions.create(
                        model="gpt-4o-transcribe",
                        file=audio_file,
                        response_format="text"
                    )
                    transcript_text += transcription.text + "\n"
        else:
            logger.info(f"File size is {file_size_mb:.2f}MB, no splitting needed.")
            # 3. OpenAIの音声書き起こしAPIを呼び出し、transcript_text を得る
            with open(temp_mp3_path, "rb") as audio_file:
                openai_client = OpenAI()
                transcription = openai_client.audio.transcriptions.create(
                    model="gpt-4o-mini-transcribe",
                    file=audio_file,
                    response_format="text"
                )
            transcript_text = transcription.text

        # 4. DBのVideoレコードに書き起こし結果を保存
        db_video = session.query(Video).filter(Video.video_id == video_id).first()
        if db_video:
            db_video.transcript_text = transcript_text
            session.commit()

        # タスク完了ステータスの更新
        db_task.status = "COMPLETED"
        session.commit()
        logger.info("[transcribe_audio] Completed successfully.")

    except Exception as e:
        logger.error(f"Error in transcribe_audio: {e}")
        session.rollback()
        try:
            # タスク失敗時は10秒後に再試行（最大2回まで）
            self.retry(exc=e, countdown=10)
        except self.MaxRetriesExceededError:
            db_task = DBTask(
                video_id=video_id,
                task_type="TRANSCRIBE",
                status="FAILED",
                error_message=str(e),
            )
            session.add(db_task)
            session.commit()
            raise e
    finally:
        session.close()

@celery_app.task(name="tasks.high_priority.summarize_text", bind=True, queue="high_priority", priority=9, max_retries=2)
def summarize_text(self, video_id: str):
    """
    取得済みの書き起こしテキストを分割し、要約（LLM呼び出し）するタスク。
    FastAPI側で実行していた要約処理と同等の内容です。
    タスク失敗時は最大２回まで自動再試行します。
    """
    logger.info(f"[summarize_text] Start video_id={video_id}")
    session = SessionLocal()
    try:
        # タスクレコードをDBに登録
        db_task = DBTask(
            video_id=video_id,
            task_type="SUMMARIZE",
            status="IN_PROGRESS",
            priority=9,
        )
        session.add(db_task)
        session.commit()

        # 1. DBから transcript_text を取得
        db_video = session.query(Video).filter(Video.video_id == video_id).first()
        if not db_video or not db_video.transcript_text:
            raise Exception("Transcript text not found in DB.")

        transcript_text = db_video.transcript_text

        # 2. 分割（例: 1000文字、オーバーラップ=100文字）
        chunk_size = 1000
        overlap = 100
        chunks = []
        start = 0
        while start < len(transcript_text):
            end = min(start + chunk_size, len(transcript_text))
            chunks.append(transcript_text[start:end])
            start = end - overlap
            if start < 0:
                start = 0

        # 3. LLM呼び出しで各チャンクの要約を実施（ここではダミーの要約）
        final_summary = []
        for c in chunks:
            # ※実際のLLM呼び出し処理に置き換えてください
            chunk_summary = f"要約(ダミー): {c[:30]}..."
            final_summary.append(chunk_summary)

        merged_summary = "\n".join(final_summary)

        # 4. DBへ最終要約を保存
        db_video.summary_text = merged_summary
        session.commit()

        # タスク完了ステータスの更新
        db_task.status = "COMPLETED"
        session.commit()
        logger.info("[summarize_text] Completed successfully.")

    except Exception as e:
        logger.error(f"Error in summarize_text: {e}")
        session.rollback()
        try:
            # タスク失敗時は10秒後に再試行
            self.retry(exc=e, countdown=10)
        except self.MaxRetriesExceededError:
            db_task = DBTask(
                video_id=video_id,
                task_type="SUMMARIZE",
                status="FAILED",
                error_message=str(e),
            )
            session.add(db_task)
            session.commit()
            raise e
    finally:
        session.close()


@celery_app.task(name="tasks.high_priority.summarize_text", bind=True, queue="high_priority", priority=9)
def summarize_text(self, video_id: str):
    """
    取得済みの書き起こしテキストを分割し、要約（LLM呼び出し）するタスク。
    FastAPI側で実行していた要約処理と同等の内容です。
    """
    logger.info(f"[summarize_text] Start video_id={video_id}")
    session = SessionLocal()
    try:
        # タスクレコードをDBに登録
        db_task = DBTask(
            video_id=video_id,
            task_type="SUMMARIZE",
            status="IN_PROGRESS",
            priority=9,
        )
        session.add(db_task)
        session.commit()

        # 1. DBから transcript_text を取得
        db_video = session.query(Video).filter(Video.video_id == video_id).first()
        if not db_video or not db_video.transcript_text:
            raise Exception("Transcript text not found in DB.")

        transcript_text = db_video.transcript_text

        # 2. 分割（例: 1000文字、オーバーラップ=100文字）
        chunk_size = 1000
        overlap = 100
        chunks = []
        start = 0
        while start < len(transcript_text):
            end = min(start + chunk_size, len(transcript_text))
            chunks.append(transcript_text[start:end])
            start = end - overlap
            if start < 0:
                start = 0

        # 3. LLM呼び出しで各チャンクの要約を実施（ここではダミーの要約）
        final_summary = []
        for c in chunks:
            # ※実際のLLM呼び出し処理に置き換えてください
            chunk_summary = f"要約(ダミー): {c[:30]}..."
            final_summary.append(chunk_summary)

        merged_summary = "\n".join(final_summary)

        # 4. DBへ最終要約を保存
        db_video.summary_text = merged_summary
        session.commit()

        # タスク完了ステータスの更新
        db_task.status = "COMPLETED"
        session.commit()
        logger.info("[summarize_text] Completed successfully.")

    except Exception as e:
        logger.error(f"Error in summarize_text: {e}")
        session.rollback()
        db_task = DBTask(
            video_id=video_id,
            task_type="SUMMARIZE",
            status="FAILED",
            error_message=str(e),
        )
        session.add(db_task)
        session.commit()
        raise e
    finally:
        session.close()



@celery_app.task(name="tasks.low_priority.check_new_videos", bind=True, queue="low_priority", priority=1)
def check_new_videos(self, channel_id: str):
    """
    定期実行想定のタスク：チャンネルID から新着動画をチェックし、DB登録やタスク投入を行う。
    """
    logger.info(f"[check_new_videos] Checking new videos for channel_id={channel_id}")
    session = SessionLocal()
    try:
        # 1. YouTube Data API などで channel_id の最新動画リストを取得
        # 2. videos テーブルに無いものがあればINSERT
        # 3. tasks テーブルに TRANSCRIBE / SUMMARIZE を追加 (低優先度)
        pass
    except Exception as e:
        logger.error(f"Error in check_new_videos: {e}")
        session.rollback()
        raise e
    finally:
        session.close()
