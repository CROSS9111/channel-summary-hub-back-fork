# get_refresh_token.py
import json
from google_auth_oauthlib.flow import InstalledAppFlow

# Google Cloud Console で作成した「ウェブアプリケーション」用 OAuthクライアントID
# の JSON ファイル (client_secret.json) を用意してください
CLIENT_SECRETS_FILE = "service-account-key.json"  

# YouTube Data API (字幕取得・ダウンロードも含む) を使いたい場合は force-ssl が便利
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

def main():
    flow = InstalledAppFlow.from_client_secrets_file(
        client_secrets_file=CLIENT_SECRETS_FILE,
        scopes=SCOPES,
    )

    # --- 実装例 (ローカルサーバーを使うフロー) ---
    # これを実行するとブラウザが起動し、Google の認可画面が表示されます。
    # ローカル環境でポートを開くため、80番などは管理者権限が必要な場合があります。
    # 別ポート (8080 等) を使うか、flow.run_console() に切り替えてもOKです。
    creds = flow.run_local_server(port=8080)

    # 取得したアクセストークン＆リフレッシュトークンを JSON に出力
    creds_data = {
        "token": creds.token,                   # アクセストークン
        "refresh_token": creds.refresh_token,   # リフレッシュトークン
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes
    }

    # ローカルに保存 (本番運用では絶対に外部公開しないこと)
    with open("refresh_token.json", "w", encoding="utf-8") as f:
        json.dump(creds_data, f, ensure_ascii=False, indent=2)

    print("リフレッシュトークンを含む認証情報を refresh_token.json に保存しました。")
    print("リフレッシュトークン:", creds.refresh_token)

if __name__ == "__main__":
    main()