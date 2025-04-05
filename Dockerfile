# Dockerfile.worker

# 1. ベースイメージ: Python + Debian slim
FROM python:3.9-slim

# 2. ffmpeg をインストール
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# 3. 作業ディレクトリ
WORKDIR /app

# 4. 依存関係を先にインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. アプリケーションコードをコピー
COPY . .

# 6. デフォルトの実行コマンドを worker.py に
CMD ["python", "worker.py"]
