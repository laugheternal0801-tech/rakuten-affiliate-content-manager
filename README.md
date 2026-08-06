# Rakuten Affiliate Content Manager

楽天市場の商品候補取得、評価、投稿下書き、本人承認、成果分析を支援するWindows向けローカルWebアプリです。公開前に必ず人間が確認する human-in-the-loop 型で、外部SNSへの自動投稿は行いません。

既存のCloudflare Worker版「Note to Automation」案内ページ（`src/`・`test/`）は削除せず、そのまま残しています。本アプリは独立したPython/Streamlitアプリとして追加されています。

## 自動化する範囲

- 楽天市場商品検索APIからの商品情報取得
- 候補商品の100点評価と項目別根拠表示
- 所有・使用経験の記録
- note、X、Pinterest、Instagram、楽天ROOM向け下書き
- 危険表現、広告表記、在庫、期限、未使用商品の体験表現などの確認
- 投稿ステータス、週次計画、作業時間の管理
- 手動ダウンロードした楽天成果CSVの列マッピングと分析
- Markdown、CSV、JSONのローカル出力

## 自動化しない範囲

- SNS・note・楽天ROOMへの自動投稿
- 自動DM、自動コメント、自動リプライ
- 他人の投稿への返信文生成
- Webスクレイピング
- 他人のレビュー転載
- 楽天商品画像のダウンロード、切り抜き、文字入れ、ロゴ追加、その他の加工
- 楽天ID、パスワード、二段階認証コードの取得・保存
- 法的適合性の保証

最終確認と実際の投稿は、必ず投稿者本人が行ってください。

## 必要環境

- Windows 10 / Windows 11
- Python 3.12以上
- PowerShell
- インターネット接続（初回インストールと楽天API利用時）

## インストール

PowerShellでプロジェクトフォルダを開き、次を実行します。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

`python` が見つからない場合は、Python 3.12以上をインストールして「Add Python to PATH」を有効にするか、Windows Python Launcherの `py -3.12 -m venv .venv` を使用してください。

PowerShellの実行ポリシーで有効化が拒否された場合は、現在のPowerShellプロセスだけを対象に次を実行します。

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

## `.env` と楽天Web Service認証情報

`.env.example` をコピーします。

```powershell
Copy-Item .env.example .env
notepad .env
```

次の3項目を楽天Web Serviceで発行された値に置き換えます。

```dotenv
RAKUTEN_APPLICATION_ID=
RAKUTEN_ACCESS_KEY=
RAKUTEN_AFFILIATE_ID=
```

- 現行の楽天市場商品検索API `2026-07-01` 版を既定にしています。
- APIエンドポイントは `RAKUTEN_API_ENDPOINT` で変更できますが、安全のため `https://openapi.rakuten.co.jp` 以外は拒否します。
- Access KeyはHTTPヘッダーへ送信します。
- Affiliate IDを指定した場合は、APIから返るアフィリエイトURLを保存します。
- 認証情報は画面やアプリログへ出力しません。
- `.env` は `.gitignore` の対象です。`.streamlit/secrets.toml` もコミットしないでください。

楽天ID、パスワード、二段階認証コードは入力しないでください。本アプリはそれらを必要としません。

公式仕様: [楽天市場商品検索API](https://webservice.rakuten.co.jp/documentation/ichiba-item-search)

## 起動

```powershell
.venv\Scripts\Activate.ps1
streamlit run app/main.py
```

ブラウザーで `http://localhost:8501` を開きます。初回起動時に `data/app.db` が作成されます。

認証情報がない場合は、架空のコーヒー商品を使うサンプルモードで起動します。画面にサンプルであることを表示し、コンプライアンスチェックはサンプル商品の承認・公開を「投稿不可」にします。

## 基本操作

1. 「商品検索」で条件を入力し、候補行を選択して保存します。
2. 「商品・体験情報」で採点根拠を確認し、所有・使用経験と確認日を記録します。
3. 「コンテンツ作成」で媒体、商品、テーマ、リンク方式を選び、下書きを生成します。
4. 警告と修正候補を確認して、確認待ちとして保存します。
5. 「投稿管理」で本人が本文・リンク・広告表記を最終確認し、確認者を入力して承認します。
6. 実際の投稿は各媒体で手動実行し、アプリでは投稿URLを入力して「published」へ変更します。

自動評価点は候補整理の補助です。評価点だけで商品を自動決定しません。

## テンプレート生成とLLM拡張

初期状態では外部AIを使わない `TemplateContentGenerator` を使用します。`ContentGenerator` インターフェースと `LLMContentGenerator` 拡張点を分離してあるため、後から任意プロバイダーを実装できます。LLM設定がない場合は自動的にテンプレート生成へ戻ります。

## 楽天成果CSVの読み込み

1. 楽天アフィリエイトの管理画面から成果レポートCSVを手動でダウンロードします。
2. アプリの「成果レポート」でCSVをアップロードします（`.csv`、10MB以下）。
3. 日付、クリック数、注文件数、売上金額、成果報酬などを実際のCSV列へ割り当てます。
4. 「マッピングを保存」を押すと、次回以降も同じ対応関係を利用できます。
5. 「CSVを取り込む」を押し、期間別・媒体別・商品別・テーマ別の成果を確認します。

UTF-8 BOM、UTF-8、CP932（一般的なWindows Shift-JIS）を読み込めます。クリック率はインプレッション列がない場合には算出せず、画面に理由を表示します。

## エクスポート

「エクスポート」で投稿とテーマを選ぶと、次の構成で出力します。

```text
exports/
└─ YYYY-MM-DD/
   └─ theme-slug/
      ├─ products.csv
      ├─ comparison.md
      ├─ note.md
      ├─ x.csv
      ├─ pinterest.csv
      ├─ instagram.md
      ├─ room.csv
      ├─ compliance_report.json
      └─ metadata.json
```

CSVはUTF-8 BOMの有無を選択でき、表計算ソフトでの数式インジェクションを防ぐ処理を適用します。パストラバーサルを防ぎ、`exports/` 外へは書き出しません。

## テストと品質確認

```powershell
.venv\Scripts\Activate.ps1
python -m pytest
python -m ruff check app tests streamlit_app.py
python -m mypy app --exclude 'app/app_pages|app/main.py|app/streamlit_support.py'
```

テストは外部APIを呼ばず、`httpx.MockTransport` を使います。API変換、エラー、429バックオフ、採点、レビュー対数正規化、未使用表現、危険表現、PR表記、期限切れ、在庫切れ、CSV、エクスポート、秘密情報ログを確認します。

既存WorkerのテストはNode.jsが利用できる環境で次を実行します。

```powershell
npm test
```

## データのバックアップ

アプリを停止してから次を実行します。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1
```

`backups/YYYYMMDD-HHmmss/app.db` にSQLiteファイルをコピーします。手動の場合は次でも構いません。

```powershell
New-Item -ItemType Directory -Force backups
Copy-Item data\app.db backups\app.db
```

復元時はアプリを停止し、現在の `data/app.db` を別名で退避してから、バックアップを `data/app.db` へコピーしてください。

## セキュリティと規約上の注意

- DBアクセスはSQLAlchemyを使い、ユーザー入力をSQLへ文字列連結しません。
- 楽天API入力はPydanticで検証し、同一条件の短時間連打をTTLキャッシュで抑制します。
- 429、500、503は指数バックオフ付きで再試行します。
- 外部リンクはHTTPSおよび楽天ドメインを投稿前に確認します。
- アップロードは拡張子・サイズ・CSV形式を確認します。
- 未使用商品について、使用済みと誤認させる表現を投稿不可にします。
- 危険表現は一律削除せず、理由と修正候補を提示します。
- 商品価格、在庫、料率、セール期間は変わるため、投稿直前に楽天市場で再確認してください。
- 楽天アフィリエイト、各SNS、note、楽天ROOMの最新規約は利用者自身で確認してください。

本アプリのチェックは法的助言ではなく、法的適合性を保証しません。

## トラブルシューティング

### `python` または `streamlit` が見つからない

仮想環境を有効にしてから実行します。

```powershell
.venv\Scripts\Activate.ps1
python -m streamlit run app/main.py
```

### 楽天APIで401・403相当または認証エラーになる

`.env` のApplication IDとAccess Keyを確認し、余分な引用符や空白を削除してアプリを再起動してください。楽天IDやパスワードではありません。

### 429エラーになる

アクセス上限です。アプリは自動再試行しますが、解消しない場合は時間をおいて検索してください。同じ検索条件の連打は避けてください。

### CSVが読めない

`.csv` 形式か、10MB以下かを確認してください。Excelから再保存する場合は「CSV UTF-8」を推奨します。列名が異なる場合はマッピング画面で手動指定します。

### DBがロックされる

同じ `data/app.db` を使うアプリを複数起動していないか確認してください。バックアップやコピーの前にはStreamlitを停止します。

### サンプル商品を承認できない

仕様です。サンプルはUI確認専用の架空データで、公開を防ぐため「投稿不可」になります。楽天API認証情報を設定し、実在商品を取得してください。

## 主な構成

```text
app/
├─ main.py
├─ app_pages/          # Streamlit 1.61推奨の明示的マルチページ構成
├─ models.py
├─ repositories.py
├─ schemas.py
├─ config.py
├─ database.py
└─ services/
   ├─ rakuten_api.py
   ├─ scoring.py
   ├─ content_generation.py
   ├─ compliance.py
   ├─ analytics.py
   ├─ exporter.py
   └─ sample_data.py
tests/
scripts/
data/
exports/
```
