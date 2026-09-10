# Rakuten Affiliate Content Manager

楽天市場の商品候補取得、評価、投稿下書き、本人承認、成果分析を支援するWindows向けローカルWebアプリです。公開前に必ず人間が確認するhuman-in-the-loop型です。初期状態はDry Runかつ外部投稿ゲートOFFで、外部SNSへ送信しません。

既存のCloudflare Worker版「Note to Automation」案内ページ（`src/`・`test/`）は削除せず、そのまま残しています。本アプリは独立したPython/Streamlitアプリとして追加されています。

## 自動化する範囲

- 楽天市場商品検索APIからの商品情報取得
- 候補商品の100点評価と項目別根拠表示
- 国内向けの商品候補、複数販売先、調査根拠、実測記録の管理
- 根拠を並べる商品比較、企画版の保存、記事作成用Markdownブリーフ
- 所有・使用経験の記録
- note、X、Pinterest、Instagram、楽天ROOM向け下書き
- 危険表現、広告表記、在庫、期限、根拠のない使用体験表現などの確認
- 投稿ステータス、週次計画、作業時間の管理
- 手動ダウンロードした楽天成果CSVの列マッピングと分析
- Markdown、CSV、JSONのローカル出力

## 自動化しない範囲

- 人間の明示承認を経ないSNS・note・楽天ROOMへの投稿
- Browser操作、Cookie流用、非公式Endpointによる投稿
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
- インターネット接続（初回インストールと外部API利用時）

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

## `.env` 設定

`.env.example` をコピーします。

```powershell
Copy-Item .env.example .env
notepad .env
```

必要な外部APIの認証情報を設定します。認証情報は画面やアプリログへ表示しません。
`.env` は `.gitignore` の対象です。`.streamlit/secrets.toml` もコミットしないでください。

楽天の商品検索には、楽天Web Serviceで発行した次の3項目を設定します。楽天のログイン
パスワードや二段階認証コードは入力しません。

```dotenv
RAKUTEN_APPLICATION_ID=
RAKUTEN_ACCESS_KEY=
RAKUTEN_AFFILIATE_ID=
```

## 起動

```powershell
.venv\Scripts\Activate.ps1
streamlit run app/main.py
```

ブラウザーで `http://localhost:8501` を開きます。初回起動時に `data/app.db` が作成されます。

デスクトップの「楽天アフィ」ショートカットからも起動できます。起動スクリプトは、この
プロジェクトのStreamlitプロセスだけを再利用し、8501番ポートを別アプリが使っている場合は
上書き起動しません。終了するときは次を実行します。PIDが別プロセスへ再利用されていた場合も
停止を拒否します。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_rakuten_affi.ps1
```

認証情報がない場合は、架空のコーヒー商品を使うサンプルモードで起動します。画面にサンプルであることを表示し、コンプライアンスチェックはサンプル商品の承認・公開を「投稿不可」にします。

## 基本操作

1. 「商品検索」でキーワードを入力し、候補行を選択して保存します。
2. 「コンテンツ作成」で媒体、商品、テーマ、リンク方式を選び、下書きを生成します。
3. 警告と修正候補を確認して、確認待ちとして保存します。
4. 「投稿管理」で本人が本文・リンク・広告表記を最終確認し、確認者を入力して承認します。
5. 実際の投稿は各媒体で手動実行し、アプリでは投稿URLを入力して「published」へ変更します。

商品検索はキーワード中心の簡単な画面です。保存済み商品の削除は、同じ画面の
「保存済み商品を整理」から行えます。旧「商品・体験情報」画面は廃止しましたが、
既存DBの過去データは削除せず保持します。

自動評価点は候補整理の補助です。評価点だけで商品を自動決定しません。

## 商品・調査管理から制作への引き継ぎ

この機能は国内の消費者向けに、ジャンルを先に固定せず、調査結果から商品とターゲットを選ぶための管理機能です。コーヒー、省スペース収納、机周り用品は入力例であり、採用済みジャンルではありません。外部APIやAI会議が未設定でも利用できます。

1. 「商品・調査管理」で商品ID、メーカー、型番、サイズ、自由ジャンル、想定読者、悩み、利用場面を登録します。同一商品の異なる型番・サイズは別の商品IDにします。
2. 商品ごとに複数の販売先を追加し、通常URLとアフィリエイトURL、価格、送料、報酬率、税区分、報酬対象額、確認日時を保存します。「未確認」は空値のまま保存され、数値の`0`とは区別されます。URLのクエリ文字列は並べ替えません。
3. 調査記録でメーカー公式、販売店、第三者情報、自分の実測を分け、確認済みの事実、仮説、未確認を指定します。実測ではURLは不要ですが、実際に使用した場合は使用条件と観察内容を記録します。
4. 「比較・企画」で2商品以上を選び、共通条件を見比べます。容量、寸法、手入れなどのジャンル固有軸を1行ずつ追加し、値を入力します。企画判断は点数ではなく、6つの観点ごとの評価メモと根拠を保存します。空欄は「判定材料不足」です。
5. 企画を保存すると、その時点の商品、販売条件、アフィリエイトURL、根拠のスナップショットと企画の改訂版が残ります。「登録情報からブリーフを生成・保存」で、APIを使わずMarkdownの「記事作成用ブリーフ」を作成できます。これは記事生成済みを意味しません。
6. ブリーフは画面からコピーまたは`.md`保存できます。「note記事制作へ」で既存の投稿文作成画面に入力を引き継げます。制作機能で使うには、商品・調査管理の商品を既存の保存商品へリンクしてください。
7. Pinterestへ引き継ぐ場合は、note公開後に実在するHTTPS URL、実タイトル、要約を入力し、企画との一致を確認します。未公開記事のURLを自動生成することはありません。

販売条件の確認期限は画面で1〜365日に設定でき、期限切れ、未確認、確認済み根拠なしを一覧で
絞り込めます。商品、比較値、評価メモ、根拠が不足した企画は「制作可能」へ変更できず、
不足項目を「判定材料不足」として表示します。

日時はDBではUTC、商品・調査管理画面では日本時間（JST）で表示します。商品調査の操作だけで外部投稿が始まることはありません。

## テンプレート生成とClaude Sonnet 5

初期状態では外部AIを使わない `TemplateContentGenerator` を使用します。Claude Sonnet 5を使う場合は、Anthropic ConsoleでAPIキーを発行し、ローカル版は `.env`、Streamlit Community Cloud版はアプリの「Settings」→「Secrets」に次を追加します。

```dotenv
ANTHROPIC_API_KEY=ここにAnthropicのAPIキー
ANTHROPIC_MODEL=claude-sonnet-5
```

APIキーはGitへコミットせず、チャットにも貼り付けないでください。LLM拡張はAnthropic Messages APIの構造化出力を使い、商品情報と調査ブリーフを参照して日本語の下書きを作成します。APIキーが未設定の場合も、標準テンプレート生成は利用できます。Claude APIの利用料はAnthropicアカウント側で発生します。

## AI会議エージェント

「AI会議」では、次のパイプラインを実行できます。

1. OpenAI、Anthropic、Geminiが互いの回答を見ず、それぞれ市場調査→企画→提案を完結します。
2. 3案が揃ってから、3社全員が各案を公平に批評します。
3. 2ラウンド目以降は前ラウンドの3社の合意点・対立点へ応答します。
4. 統合責任者AIが根拠の強さで判断し、一つの最終結果へまとめます。
5. 3社の途中成果、議論、最終結果を画面で確認し、全結果をJSONで保存できます。

AI会議は実API専用です。会議を開始するとDBへジョブを保存し、Streamlitとは別のバックグラウンドプロセスが処理します。ブラウザやAI会議画面を閉じても処理は継続します。各工程・議論ラウンド・最終統合の出力は受信直後に逐次保存されるため、後続工程が失敗した場合も、それまでの成果物を実行履歴から確認できます。

```dotenv
OPENAI_API_KEY=
AI_COUNCIL_OPENAI_MODEL=gpt-5.6-sol

ANTHROPIC_API_KEY=
AI_COUNCIL_ANTHROPIC_MODEL=claude-fable-5-1

GEMINI_API_KEY=
AI_COUNCIL_GEMINI_MODEL=gemini-3.1-pro-preview

AI_COUNCIL_TIMEOUT_SECONDS=900
AI_COUNCIL_MAX_OUTPUT_TOKENS=16000
```

OpenAIはGPT-5.6 Solのmax reasoning＋pro mode、AnthropicはClaude Fable 5.1のmax effort、GeminiはGemini 3.1 Pro PreviewのHIGH thinkingを使います。3社の市場調査では、それぞれOpenAI Web Search、Anthropic Web Search、Google Search groundingを必須にします。既定の2ラウンドではAI APIを16回呼び出し、検索利用料が別途発生する場合があります。会議は提案の提出までで、外部送信・公開・購入・削除などの副作用は行いません。

## AI Operating System

「AI Operating System」は、市場・事業候補を`QUICK / STANDARD / DEEP`で評価し、中央Model Router、Run予算、LLM Cache、Provider fallback、Opportunity Score、Evidence Score、早期終了を通して、`GO / NO_GO / INVESTIGATE_MORE`の共通Decisionを保存します。初期状態は外部AIロック中で、ローカルデモだけを使うため費用は発生しません。

```dotenv
AI_OS_EXTERNAL_API_ENABLED=false
AI_OS_LLM_CACHE_TTL_SECONDS=86400
AI_PROVIDER_COST_RESERVES_USD={"demo":0.0,"openai":0.03,"anthropic":0.04,"gemini":0.01}
```

外部AIを使うには、全体ロックを有効にしたうえでRun単位の許可も必要です。費用表示は予算保護用の概算で、各社の請求額ではありません。各DecisionはAI Cost、Evidence、Source、使用モデル、理由、リスク、次の行動、人間の承認状態を保持します。詳細と現在の制約は[AI Operating System設計](docs/AI_OPERATING_SYSTEM.md)を参照してください。

## SNS Market Intelligence Multi-Agent System

「SNS市場調査」は、X、Reddit、YouTube、TikTok、Instagram、Pinterest、Web/RSSを同じ調査単位で扱うEvidence-first型の市場調査機能です。Sourceごとの取得可否を最初に表示し、利用できないAPIがあっても他Sourceだけで処理を継続します。

処理は次の順序で実行されます。

```text
Research Request
  → Research Director（市場定義・仮説・Source別Query）
  → Source Registry / Connector（並列収集・個別失敗を隔離）
  → Immutable Raw Data保存
  → 共通SocialItemへ正規化
  → 重複・Spam・Bot・広告・関連度・品質スコア
  → Balanced Sampling
  → Trend / Consumer / Competitor / Content / Quantitative / Opportunity Agent
  → Critic（SUPPORTED / PARTIALLY_SUPPORTED / INSUFFICIENT_EVIDENCE / CONTRADICTED）
  → Synthesis Report + Evidence Viewer
```

AIは数値集計を行わず、件数、Engagement、キーワード頻度、競合言及、日別集計はPythonで決定論的に計算します。各ClaimはEvidence IDと元の正規化行へ紐付きます。Likes等が取得できない場合は`0`にせず`null`として保持します。信頼度はEvidence量、Source/Platform多様性、期間整合性、モデル一致、データ品質、反証を設定可能な重みで合成します。

### Live DataとMock Data

- X、Reddit、YouTubeは認証情報がある場合に公式APIを使います。
- TikTok、Instagram、Pinterestは現行版では承認済み手動Importへ縮退します。トークン欄は将来の公式Connector拡張用で、設定しただけでは取得済み扱いにしません。
- Webは`MARKET_INTELLIGENCE_WEB_FEED_URLS`へ明示したRSS/Atomだけを読みます。任意サイトのスクレイピングはしません。
- 認証情報がないSourceは`not_configured`として明示され、データを捏造しません。
- Mock Modeは全7 Sourceの決定論的な合成データでパイプラインを確認します。Raw Data、画面、レポート、出力のすべてに`MOCK DATA`を表示し、実データへ混入させません。Mock Modeでは外部LLMも呼びません。

APIキーを実際に設定して疎通するまでは、Capability Registryの「設定済み」は認証情報の存在を示すだけです。権限、契約プラン、Quota、API側の仕様変更によって検索時に縮退する場合があります。

### 環境変数

`.env.example`の次の項目を必要なSourceだけ設定します。秘密値はGit、画面、ログ、チャットへ載せないでください。

```dotenv
X_BEARER_TOKEN=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=sns-market-intelligence/0.1
YOUTUBE_API_KEY=
MARKET_INTELLIGENCE_MANUAL_IMPORT_DIR=
MARKET_INTELLIGENCE_WEB_FEED_URLS=
```

複数LLMで専門Agentを動かす場合は、既存の`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GEMINI_API_KEY`と対応モデル名を設定します。1社だけでも実行でき、未設定時はEvidenceに基づく決定論的なPython Agentだけで完走します。StandardはDirector 1回と専門Agent最大5回、Deepは設定済みProvider最大3社を役割ごとに独立実行するため、開始前に画面の推定呼び出し回数を確認してください。

### 手動Import Schema

`MARKET_INTELLIGENCE_MANUAL_IMPORT_DIR`にSource名のJSONまたはCSVを置きます。1ファイル25MB以下です。

```text
imports/
├─ x.json
├─ reddit.csv
├─ youtube.json
├─ tiktok.json
├─ instagram.json
├─ pinterest.json
└─ web.json
```

JSONは行オブジェクトの配列、または`{"items": [...]}`です。CSVはヘッダー行が必要です。共通して`id`または`source_id`、`url`または`source_url`、`title`、`text`、`created_at`、`author_name`、`likes`、`comments`、`shares`、`views`、`language`を利用できます。欠けた任意項目は未知値として扱います。入力行は信頼できない外部データとして処理し、投稿本文に含まれる命令はAgentへの命令として扱いません。

### 保存・出力・制約

Research Run、計画、Source Query、Raw Data、正規化Data、Agent出力、Evidence、Claim、Reportは既存DBへ追加テーブルとして保存します。Dashboardでは過去Runを再表示でき、JSON、Markdown、HTMLを保存できます。HTMLはブラウザーの印刷機能でPDF化できるPDF-ready出力です。

この機能は市場全体を保証する統計調査ではありません。SNS利用者・投稿者の偏り、削除済み投稿、非公開コンテンツ、API検索範囲、ランキングアルゴリズム、言語判定、重複判定の限界があります。重要な投資・商品化・広告判断では、一次調査、販売データ、規約、法務確認を追加してください。

## AI Creative Intelligence & Production Platform

「AI制作スタジオ」は、保存済みMarket Intelligence ReportからEvidenceを失わずに、Campaign、Content Brief、媒体別制作、複数案審査、Revision、画像、Storyboard、Human Approval、公開後Performanceまでを管理します。

```text
Market Intelligence Report / Evidence
  → Campaign Planner
  → Content Brief / Claim Guard
  → Platform Router
  → Independent Generation Arena
  → Objective QA + Platform Judge + Editorial Judge
  → Creative Critic / Revision Loop
  → Image Visual + deterministic Typography/Layout
  → Shot-based Storyboard / Caption / Video Job
  → Final QA
  → Human Approval
  → Content Package / Performance Learning
```

### 初期状態では外部APIを呼びません

外部生成APIは、画面の「設定済み外部生成APIの実行を許可」を利用者が明示的に有効化した場合だけ候補になります。初期状態では、TextはEvidence接続を確認する決定論的Local Writer、Imageは背景とTypography/Safe Areaを分離したLocal Placeholder、VideoはShot manifestとStoryboardだけで全工程を検証できます。

Local画像は`DRAFT PLACEHOLDER`、動画設計は`STORYBOARD PLACEHOLDER`と明示され、実画像・実動画として公開承認できません。APIキー、利用権限、モデル、予算上限、疎通試験は[最終外部接続チェックリスト](docs/FINAL_EXTERNAL_SETUP_CHECKLIST.md)にまとめています。

### 制作品質モード

- `draft`: 低コストな工程・アイデア確認
- `standard`: 複数案、3系統審査、最大1回の修正
- `premium`: 候補数増加、複数Provider前提、最大2回の修正
- `flagship`: 品質閾値を上げ、複数外部Model/ProviderとHuman Approvalを前提

Premium/Flagshipで必要な外部Provider数を満たさない場合、処理は停止せず`DEGRADED QUALITY MODE`になります。

### EvidenceとClaim Guard

Content BriefにはMarket Intelligenceの許可ClaimとEvidence IDだけが渡されます。Writerは使用したClaimを構造化して返し、Claim Guardが許可Claimとの完全一致、Evidence、数字表現を検査します。未裏付け・矛盾したClaimはBlocking issueになります。外部SNS・Web本文は`UNTRUSTED EXTERNAL DATA`として扱い、本文中の命令をSystem instructionとして実行しません。

### 画像と動画

画像はVisual生成とTypography/Layoutを分離します。決定論的LayoutではFont、文字サイズ、行間、Margin、Safe Area、CTA位置を管理し、解像度、Aspect ratio、破損、File size、文字Overflowを客観検査します。OpenAI Image adapterは設定済みモデルを`/v1/images/generations`へ渡す構造です。Google ImageとFLUXはCapability Registry上に存在しますが、現行版では未接続です。

動画はScriptをStoryboardとShotへ分解し、各ShotへCamera、Lens、Lighting、Motion、Continuity、Caption timingを保存します。Google Video、Runway、xAI VideoのProvider Interfaceは存在しますが、実AdapterとDownload/FFmpeg結合は未接続です。現行Local Providerは実動画を生成しません。

### 追跡・承認・学習

Candidate、Revision親子関係、Judge Score、Critic Report、Provider/Model、Prompt version、Cost、Asset親子関係、Content Brief、Campaign、Research Run、Evidenceを保存します。Content PackageはJSON、Markdown、ZIPで出力できます。公開後のImpressions、Views、Clicks、CTR、Completion、Conversion、Revenueを手動保存し、3件以上の実績がある場合はRule-based Model RouterがAI Judgeより実Performanceを優先します。

## AI Publishing, Scheduling & Performance Learning System

「公開・学習センター」は、AI制作スタジオのContent Packageをそのまま投稿せず、人の承認、版ロック、対象アカウント、日時承認、安全検査を経てから公開Jobへ変換します。

```text
Content Package
  → Human Approval（Platform / Asset / Target Accountを明示）
  → Immutable Approved Snapshot（Content / Asset / Integrity Hash）
  → Schedule Approval（Timezone-aware）
  → DB Worker / Lease / Queue / Preflight / Idempotency / Global Pause
  → Platform Publisher
  → Remote ID / Status / Notification Outbox / Performance
  → ObservationとHypothesisを分離したLearning
  → 次回Creative BriefとResearch PlanのPerformance Signal
```

### 現在の接続状態

- X、Instagram、TikTok、YouTube、Pinterest、Reddit、Genericを別Publisherとして実装しています。
- XはOAuth 2.0 User Contextで`POST /2/tweets`を使うテキスト投稿Live adapterを実装済みです。Media、Reply、Threadは未接続です。
- InstagramはInstagram LoginのProfessional accountを対象に、公開HTTPS URL上の単一JPEG画像を`/media`→`/media_publish`で投稿するLive adapterを実装済みです。Video、Reels、Carousel、Storyは未接続です。
- 「Accounts」の接続ウィザードで、X OAuth 2.0 PKCEとInstagram Business Loginの開始・Callback・本人確認・Target Account自動登録・Token更新・接続解除を完結できます。
- Instagramは短期Tokenを長期Tokenへ自動交換し、期限前のPreflightでX/Instagram Tokenを安全側に自動更新します。更新失敗時は投稿しません。
- Instagramの公開JPEG URLはHuman Approval時に入力し、本文・Asset・Target Accountと同じ承認Hashへ固定します。
- Pinterest画像Pinは公式Create Pin APIへ接続できるLive adapterまで実装済みです。認証情報と本番ゲートが未設定の間は送信しません。
- TikTok動画は公式Content Posting APIのLive adapterまで実装済みです。Creator Info同期、本人の公開範囲選択、明示承認が必要です。YouTube、Reddit、Genericは現時点でDry Run payload生成専用です。
- 初期値は`PUBLISHING_ENABLED=false`、`PUBLISHING_EXTERNAL_API_ENABLED=false`、`PUBLISHING_DRY_RUN=true`です。
- Dry Runでは外部APIを呼ばず、架空のremote post IDやExternal Publicationを保存しません。
- Test専用Mock Publisherだけが、remote ID、retry、partial failure、metricsの結合試験に使われます。
- DB WorkerはJob LeaseとHeartbeatを保存し、同じJobの多重取得を防ぎます。通知は現在`in_app` Outboxだけで、メールやSlackへは送信しません。
- Provider PortalでのApp設定と対象アカウント本人の同意だけを、[公開システム最終外部接続チェックリスト](docs/PUBLISHING_EXTERNAL_SETUP_CHECKLIST.md)にまとめています。
- OAuth token本体はDBへ保存せず、Windows Credential Managerまたは環境変数のSecret referenceだけをTarget Account台帳へ保存します。
- Xの調査用`X_BEARER_TOKEN`と投稿用`X_OAUTH_ACCESS_TOKEN`は別の認証情報として扱います。

### Workerのローカル実行

画面の「Worker・通知」から1バッチだけ安全に実行できます。CLIでは次のコマンドを使います。`--once`を外すと常駐ループになりますが、本番Service登録はOAuthとSandbox試験後に行ってください。

```powershell
.venv\Scripts\python.exe -m app.publishing.worker_cli --once
```

Global pause中はDue JobをQueueへ残し、外部Publisherを呼びません。WorkerのHeartbeat、現在のLease、処理結果通知は公開・学習センターで確認できます。

### 安全性

- 承認後に本文、Asset bytes、Target Account、Snapshot JSONが変わると再承認が必要です。
- 同じIdempotency Keyの再実行と、同一Accountへの同一Content Hashの短期重複を防止します。
- 画像はPillowで実際にdecodeし、形式と寸法を検査します。動画は`ffprobe`が利用できる場合にcodec、duration、解像度を実測し、未導入のProduction実行は手動確認へ止めます。
- 予約時刻前の直接実行、停止中Job、Global pause中の実行を拒否します。
- retryableな一時障害だけを指数Backoffし、権限・内容エラーは自動再試行しません。
- 外部送信開始前に`PUBLISHING`状態を確定保存します。送信後のタイムアウト、5xx、応答欠落、
  Worker停止は「要照合」へ隔離し、自動再送しません。管理画面で外部サービスの投稿有無を
  確認し、担当者名と明示確認を入力した場合だけ「投稿済み」確定または再キューできます。
- 1 Platformの失敗を他Platformから隔離し、Campaignを`partial_failure`として扱えます。
- Metric未取得は`null`、実測0は`0`のまま保存します。
- LearningはObservationとInterpretation/Hypothesisを別項目にし、3件以上の実Performanceが揃うまでWinning Patternへ昇格しません。
- 過去PerformanceはResearch Planへ仮説として渡しますが、各Signalを`NOT market evidence`と明記し、市場需要の根拠には混ぜません。

## 楽天成果CSVの読み込み

1. 楽天アフィリエイトの管理画面から成果レポートCSVを手動でダウンロードします。
2. アプリの「成果レポート」でCSVをアップロードします（`.csv`、10MB以下）。
3. 日付、クリック数、注文件数、売上金額、成果報酬などを実際のCSV列へ割り当てます。
4. 「マッピングを保存」を押すと、次回以降も同じ対応関係を利用できます。
5. 「CSVを取り込む」を押し、期間別・媒体別・商品別・テーマ別の成果を確認します。

UTF-8 BOM、UTF-8、CP932（一般的なWindows Shift-JIS）を読み込めます。クリック率はインプレッション列がない場合には算出せず、画面に理由を表示します。
取り込んだ行は、クエリを保持した完全一致URL、または商品名と販売店名の完全一致だけで商品候補へ
自動関連付けします。同名候補が複数ある行や一致しない行は推測で結び付けず、画面に件数を表示します。
関連付け済みの実績は「比較・企画」で企画単位に確認できますが、市場需要の根拠や架空の順位には
変換しません。

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

バックアップはSQLiteのオンラインバックアップ機能を使うため、アプリを停止せずに実行できます。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1
```

`backups/YYYYMMDD-HHmmss-ffffff/` に、整合性を確認した `app.db`、`manifest.json`、
`app.db.sha256` を作成します。`integrity_check` または `foreign_key_check` が失敗した場合、
不完全なバックアップは公開されません。

復元時はアプリとワーカーを停止し、バックアップフォルダーを指定して次を実行します。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\restore.ps1 `
  -BackupDirectory backups\YYYYMMDD-HHmmss-ffffff `
  -ConfirmAppStopped
```

復元元はこのプロジェクトの `backups` 直下に限定され、manifest、SHA-256、SQLite整合性、
外部キーを再検証します。現在のDBがある場合は `pre-restore-*` へ退避してから、同一
ボリューム上で原子的に置き換えます。DBが破損している場合も、元のバイト列を
`recovery_only` の未検証スナップショットとして保全してから復元できます。DB自体が欠損して
いる場合は、検証済みバックアップから再作成します。`app.db-wal`、`app.db-shm`、
`app.db-journal` が残っている場合は一体での復旧が必要なため、安全側に自動復元を拒否します。

商品・調査管理のテーブルは既存テーブルと分離した追加スキーマです。アプリ起動時に不足テーブルだけを作成し、同じ更新を再実行しても既存の投稿履歴を初期化しません。更新前は上記の`backup.ps1`を実行してください。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1
streamlit run app/main.py
```

企画保存時の販売条件はスナップショットとして残りますが、実際の価格、送料、料率、在庫は変動します。公開直前には楽天市場の商品ページと適用条件を再確認してください。

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

## 商品調査機能に残る範囲外・注意事項

- 商品調査画面は市場データ、成約、報酬実績を無制限に自動収集しません。根拠は運営者が登録・確認します。
- 実績がない商品を「売れる」「高成約率」と判定せず、不透明な総合ランキングも作りません。
- 楽天ROOMや全SNSへの機械的な自動投稿は、商品調査・企画から実行しません。
- アフィリエイトURLは計測パラメーターを変えないため自動アクセス・正規化しません。公開前に通常の商品ページと楽天アフィリエイト管理画面で、リンク先と適用条件を手動確認してください。
- 投稿結果が不明な場合の自動再送は停止しました。ただし外部サービス側が提供する冪等キーではないため、管理画面の「要照合」で実際の投稿有無を確認してから結果を確定してください。
- 公開日が決まっていない振り返り通知は予約しません。

## トラブルシューティング

### `python` または `streamlit` が見つからない

仮想環境を有効にしてから実行します。

```powershell
.venv\Scripts\Activate.ps1
python -m streamlit run app/main.py
```

### 429エラーになる

アクセス上限です。アプリは自動再試行しますが、解消しない場合は時間をおいて検索してください。同じ検索条件の連打は避けてください。

### CSVが読めない

`.csv` 形式か、10MB以下かを確認してください。Excelから再保存する場合は「CSV UTF-8」を推奨します。列名が異なる場合はマッピング画面で手動指定します。

### DBがロックされる

同じ `data/app.db` を使うアプリを複数起動していないか確認してください。`scripts\backup.ps1` は
稼働中でも実行できますが、手動コピーや復元の前にはアプリとワーカーを停止してください。

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
