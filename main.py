import os
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs
from openai import AzureOpenAI
from dotenv import load_dotenv
from langchain.text_splitter import CharacterTextSplitter

# .env.local から環境変数をロード
load_dotenv(".env.local", override=True)

app = FastAPI()

class SummaryRequest(BaseModel):
    youtube_url: str

class SummaryResponse(BaseModel):
    summary: str
    points: str

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
    
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=["en"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"書き起こしの取得に失敗しました: {e}")
    
    if not transcript_list:
        raise HTTPException(status_code=404, detail="書き起こしが見つかりませんでした。")
    
    # transcript_list の各 dict の "text" キーを連結して全文を作成
    transcript_text = " ".join([item["text"] for item in transcript_list])
    
    # 1000文字ごと、100文字オーバーラップでテキストを分割する
    splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
    chunks = splitter.split_text(transcript_text)
    
    summaries = []
    points_list = []
    
    # 各チャンクごとに AzureOpenAI を呼び出して要約・ポイントを取得
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
                model=os.getenv("AZURE_MODEL") or "o3-mini",  # デプロイ済みモデル名（環境変数で設定）
                messages=[
                    {"role": "system", "content": "Assistant is a large language model trained by OpenAI."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}  # 構造化出力を指定
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
    
    # 複数チャンクの結果を結合
    final_summary = "\n\n".join(summaries)
    final_points = "\n".join(points_list)
    
    return SummaryResponse(summary=final_summary, points=final_points)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
