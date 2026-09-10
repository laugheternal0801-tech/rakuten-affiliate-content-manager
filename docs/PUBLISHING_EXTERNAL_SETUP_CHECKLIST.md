# 公開・予約・学習システム 最終外部接続チェックリスト

このチェックリストは、ローカル実装・単体テスト・Dry Run・人間承認フローを確認した最後に実施します。アプリ側のX・Instagram・TikTok OAuth交換、本人確認、Target Account自動登録、Token更新、接続解除は実装済みです。残る作業はProvider画面でのApp設定と対象アカウント本人による同意です。

## 0. 現在の安全状態

- `PUBLISHING_ENABLED=false`
- `PUBLISHING_EXTERNAL_API_ENABLED=false`
- `PUBLISHING_DRY_RUN=true`
- Database側も`dry_run=true`
- Production live adapter接続数は4（Xテキスト、Instagram単一JPEG画像、TikTok MP4 Direct Post、Pinterest画像Pin。すべて既定停止）
- YouTube、Reddit、Genericはpayload生成とCapability/Preflight確認だけ
- X OAuth 2.0 PKCE、Instagram Business Login、TikTok Login KitのCallbackを実装済み
- Instagram短期Tokenから長期Tokenへの交換、X/Instagram/TikTokのToken更新を実装済み
- Providerの`/me`応答からTarget Accountを自動登録し、手入力による誤アカウントを防止
- OAuth stateは1回限り・10分で失効し、Token本体はWindows Credential Managerへ保存
- 画面から本人確認・再接続・X/TikTok remote revokeを含む接続解除が可能
- Dry Run成功時も`ExternalPublication`や架空のremote post IDを作らない
- WorkerはDB LeaseとHeartbeatを使い、通知先は`in_app` Outboxだけ
- Browser操作・Cookie流用・非公式自動投稿は実装しない

## 1. 全Provider共通の事前作業

1. `.env`と`.streamlit/secrets.toml`がGit対象外であることを再確認する。
2. 秘密値をチャット、Issue、画面、ログ、DB、スクリーンショットへ載せない。
3. Provider Consoleで本番用と検証用Appを分離する。
4. Redirect URIを完全一致で登録し、`state`、PKCE、token rotation、失効処理を実装する。
5. Access token本体はSecret Storeへ保存し、DBには`secret://...`の参照だけを保存する。
6. 最小Scope、対象アカウント、Rate Limit、Quota、費用上限、データ保持期間を確認する。
7. Platform規約、広告表示、AI生成Media表示、音源・肖像・商標・年齢制限を人が確認する。
8. Callback/Webhookの署名検証、replay防止、request ID、監査Logを実装する。
9. API仕様・審査要件は変更されるため、接続日に公式資料を再確認する。
10. Productionで動画を扱うHostへFFmpegの`ffprobe`を導入し、codec、duration、解像度のPreflightを通す。

## 1.5 あなたが最後に操作する手順

1. X Developer PortalのOAuth 2.0設定で、App種別をClient Secretを保持できるWeb App（confidential client）にし、Callback URLへ`http://localhost:8501/publishing_center`を完全一致で登録する。
2. Meta Appで「Instagram API with Instagram Login / Business Login」を追加し、Instagram Professional accountを接続する。Valid OAuth Redirect URIへ同じ`http://localhost:8501/publishing_center`を完全一致で登録する。Meta側がHTTPSを要求する本番構成では、利用するHTTPS URLへ`.env`の2つのRedirect URIも同時に変更する。
3. `.env`の`X_CLIENT_ID`、`X_CLIENT_SECRET`、`META_APP_ID`、`META_APP_SECRET`を入力する。秘密値をこの文書やチャットへ貼らない。
4. アプリを再起動し、「公開・学習センター」→「Accounts」でXとInstagramの「認証リンクを作成」→「接続を許可」を順に押す。
5. 接続後の台帳に、実際のXユーザー名とInstagram Professional accountが表示されることを確認する。
6. Instagram投稿を承認するときだけ、Metaがログインなしで取得できる公開HTTPS JPEG URLを入力する。このURLも承認Hashへ固定される。
7. TikTok Developer AppのWebsite、Privacy Policy、Terms of Serviceへ以下を登録する。
   - `https://note-to-automation.laugh-eternal0801.workers.dev`
   - `https://note-to-automation.laugh-eternal0801.workers.dev/privacy`
   - `https://note-to-automation.laugh-eternal0801.workers.dev/terms`
8. TikTok AppへLogin KitとContent Posting APIを追加し、`user.info.basic`と`video.publish`を申請する。Desktop Loginなら`http://127.0.0.1:8501/publishing_center`をRedirect URIへ完全一致で登録する。Web Loginを選ぶ場合はTikTokが要求する固定HTTPS callbackへアプリ設定も変更する。
9. `.env`の`TIKTOK_CLIENT_KEY`、`TIKTOK_CLIENT_SECRET`、`TIKTOK_REDIRECT_URI`を入力し、アプリ再起動後にTikTok認証リンクから本人が同意する。
10. 接続後に「TikTok Creator Infoを更新」を押し、公開範囲候補と動画尺上限が同期されたことを確認する。

ここまでの操作では外部投稿は発生しません。`PUBLISHING_ENABLED=false`、`PUBLISHING_EXTERNAL_API_ENABLED=false`、`PUBLISHING_DRY_RUN=true`を維持したまま接続確認できます。

## 2. Provider別の最後の準備

### X

- Developer App、OAuth 2.0、対象ユーザーの同意を準備する。
- `X_CLIENT_ID`、`X_CLIENT_SECRET`、`X_REDIRECT_URI`を設定し、画面の接続ウィザードを使う。認証後のUser Access TokenとRefresh Tokenは自動的にWindows Credential Managerへ保存される。
- 既存の`X_OAUTH_ACCESS_TOKEN`を使う場合も、画面の「既存Tokenを確認して台帳へ登録」で`/2/users/me`との一致を確認する。
- `tweet.read`、`tweet.write`、`users.read`を確認する。Refresh tokenを使う場合は`offline.access`も同意画面へ追加する。
- 調査用App-only `X_BEARER_TOKEN`を投稿用User Access Tokenとして流用しない。
- 現行Live adapterはテキスト投稿だけに限定する。Media、Reply、Threadは各adapter実装と試験が終わるまで有効化しない。
- Create Postのremote post IDを必ず保存し、idempotencyと重複Content Hashを照合する。
- 公式資料: [Create Post](https://docs.x.com/x-api/posts/create-post)、[OAuth 2.0 PKCE](https://docs.x.com/fundamentals/authentication/oauth-2-0/user-access-token)、[Authenticated user lookup](https://docs.x.com/x-api/users/lookup/quickstart/authenticated-lookup)

### Instagram

- Meta AppとInstagram Professional accountを準備し、採用するLogin方式に対応する権限を選ぶ。
- `META_APP_ID`、`META_APP_SECRET`、`META_REDIRECT_URI`、接続日に公式Consoleで確認した`PUBLISHING_META_GRAPH_API_VERSION`を設定し、画面の接続ウィザードを使う。短期Tokenから長期Tokenへの交換と更新はアプリが行う。
- 既存の`META_ACCESS_TOKEN`を使う場合も、画面の「既存Tokenを確認して台帳へ登録」で`/me`との一致を確認する。
- `instagram_business_basic`と`instagram_business_content_publish`を確認する。
- Target Account台帳には`/me`から返された数値のInstagram Professional Account IDが自動登録される。
- 現行Live adapterはMetaから取得可能な公開HTTPS URL上の単一JPEG画像だけに限定する。Local file、PNG、Video、Reels、Carousel、Storyは有効化しない。
- Media container作成後に`media_publish`を実行し、remote media IDを保存する。Video系を追加する場合はcontainer status pollingを別途実装する。
- Media URLの到達性、形式、公開上限をPreflightで再検証する。
- 公式資料: [Meta Instagram API official collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api)

### TikTok

- Login Kit、Content Posting API、`user.info.basic`、`video.publish`、App auditを準備する。
- OAuth TokenはWindows Credential Managerへ保存され、Access Token更新とRefresh Token rotationはアプリが処理する。
- 投稿直前にCreator Infoを再取得し、返却されたprivacy optionだけを画面で本人に選択させる。
- 未審査Clientの公開範囲制約を勝手に別privacyへ変更しない。
- 現行Live adapterは承認済みローカルMP4動画のDirect Postだけに限定する。PhotoとDraft uploadは未接続のままにする。
- 64MB超は公式制約に従い順次Chunk送信し、`publish_id`で非同期処理完了を確認する。
- 商用コンテンツ、AI生成、コメント・デュエット・リミックス、公開範囲を承認版へ固定する。
- 公式資料: [Login Kit Desktop](https://developers.tiktok.com/docs/en/login-kit-desktop)、[Content Posting API getting started](https://developers.tiktok.com/docs/en/content-posting-api-get-started)、[Query Creator Info](https://developers.tiktok.com/docs/en/content-posting-api-reference-query-creator-info)、[Media Transfer Guide](https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide)、[Get Post Status](https://developers.tiktok.com/docs/en/content-posting-api-reference-get-video-status)

### YouTube

- Google Cloud project、OAuth consent、YouTube Data API、upload Scopeを準備する。
- 大きい動画はresumable uploadを実装し、upload完了とprocessing完了を分離する。
- 未監査Projectの公開制約、子ども向け指定、Category、License、Privacyを確認する。
- 公式資料: [`videos.insert`](https://developers.google.com/youtube/v3/docs/videos/insert)、[Resumable upload protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)

### Pinterest

- Pinterest App、OAuth、対象Business accountを準備し、`boards:read`、`boards:write`、`pins:read`、`pins:write`を確認する。
- `PINTEREST_APP_ID`、`PINTEREST_APP_SECRET`、`PINTEREST_REDIRECT_URI`を設定し、tokenはWindows Credential Managerへ保存する。
- Board ID、Link、Title/Description/Alt text、Media sourceを事前確認する。
- 現行Live adapterはJPEG/PNG画像Pinだけに限定する。Video PinはMedia upload adapter実装後まで有効化しない。
- Sandbox/Test環境が利用できる契約では、最初にそこで検証する。
- 公式資料: [Create Pin](https://developers.pinterest.com/docs/api/v5/pins-create/)

### Reddit

- OAuth App、固有User-Agent、投稿に必要な最小Scopeを準備する。
- Subreddit、投稿種別、flair、community rules、rate limitを投稿直前に取得・確認する。
- 自動投稿可否はSubredditごとに異なるため、初回は必ずManual Reviewにする。
- 公式資料: [Reddit API](https://www.reddit.com/dev/api/)

### Generic Web / その他SNS

- 公式投稿API、利用規約、認証方式、remote ID、status/metrics endpointがすべて確認できるまで`disabled`にする。
- Browser自動操作や非公式endpointを公式Publisherの代替にしない。

## 3. Production adapterの実装条件

各Providerは同じProtocolを実装します。

```text
health_check
get_capabilities
validate_credentials
validate_content
publish
get_publish_status
delete（既定OFF・別Human Approval必須）
fetch_metrics
```

さらに次を満たすことを必須にします。

- approved immutable snapshotだけを入力にする
- Target Accountをtokenから再取得してDBのaccount IDと一致確認する
- timeout、429、5xxだけをretry候補にし、permission/content errorは自動retryしない
- 指数Backoff、最大回数、最遅許容時刻を守る
- Provider request ID、remote post ID、status、error codeを保存する
- 1 Platformの失敗で他Platformの成功を取り消さない
- Dry Runではネットワーク送信しない
- deleteは`PUBLISHING_ALLOW_REMOTE_DELETE=false`を既定にし、別承認を要求する

## 4. 段階的な試験順序

1. Unit testとMock Provider test
2. 公式PublisherのDry Run payload snapshot test
3. OAuth callbackとtoken refreshのローカル試験
4. Provider Sandbox/Test environment
5. 非公開・SELF_ONLY・private投稿を1件
6. Remote ID、status polling、metrics、nullと0の区別を照合
7. 同一requestの再送で二重投稿されないことを確認
8. 401/403/429/5xx/timeout/media処理失敗を注入
9. Global pause中に送信されないことを確認
10. 2つのWorkerを同時起動し、DB Leaseにより同じJobが1回だけ処理されることを確認
11. Worker停止・Lease期限切れ・再取得とHeartbeat stale表示を確認
12. Notification Outboxの再試行、重複防止、秘密情報非保存を確認
13. 1 Platform・1 Test account・1投稿だけを限定公開
14. 1週間監視後に複数Platformへ拡大
15. 最後にProduction gateを段階的に有効化

## 5. 最後の有効化

外部接続完了後も、いきなり全Platformを有効化しません。

```dotenv
PUBLISHING_ENABLED=true
PUBLISHING_EXTERNAL_API_ENABLED=true
PUBLISHING_DRY_RUN=true
```

最初は上記のままDry Runを再実行します。その後、Database側のDry Run、対象Platform capability、対象Test accountを限定してから、最後にだけ次へ変更します。

```dotenv
PUBLISHING_DRY_RUN=false
```

有効化後も、Content Approval、Version Lock、Schedule Approval、Preflight、Target Account照合、Global pauseは省略できません。

常駐WorkerのWindows ServiceまたはTask Scheduler登録も、上記Sandbox試験と限定公開試験の完了後に行います。それまでは画面または`python -m app.publishing.worker_cli --once`だけを使用します。
