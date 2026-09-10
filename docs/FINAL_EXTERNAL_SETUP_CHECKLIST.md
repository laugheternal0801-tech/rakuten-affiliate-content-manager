# 最終外部接続チェックリスト

この作業は、ローカルのMock/Placeholder工程、テスト、Content Package、Human Approvalを確認した最後に行います。APIキーをチャット、画面、Git、ログへ貼り付けないでください。

## 1. 事前確認

- `.env`が`.gitignore`対象であることを確認する
- Providerごとの利用規約、対象地域、商用利用、保存ポリシーを確認する
- 月額上限・従量課金上限・Rate LimitをProvider側で設定する
- 使用するModel名が契約アカウントで利用可能か公式Consoleで確認する
- `CREATIVE_EXTERNAL_API_ENABLED=false`のまま認証情報を設定する

## 2. Text Provider

必要なProviderだけ設定します。

```dotenv
OPENAI_API_KEY=
OPENAI_MODEL=

ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=

GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.7-flash

CREATIVE_XAI_API_KEY=
CREATIVE_XAI_MODEL=
```

現行実装でCreative Text Arenaへ接続済みなのはOpenAI、Anthropic、Geminiです。xAIは設定欄のみで、Adapterは未接続です。

## 3. Image Provider

```dotenv
OPENAI_API_KEY=
CREATIVE_OPENAI_IMAGE_MODEL=

GOOGLE_API_KEY=
CREATIVE_GOOGLE_IMAGE_MODEL=

CREATIVE_FLUX_API_KEY=
CREATIVE_FLUX_MODEL=
```

現行実装で実リクエスト可能なのはOpenAI Image adapterのみです。Google ImageとFLUXはProvider Interface/Capability表示までで、実接続は次期実装です。Model名はコードに固定せず、利用可能な値を設定してください。

## 4. Video Provider

```dotenv
GOOGLE_API_KEY=
CREATIVE_GOOGLE_VIDEO_MODEL=

CREATIVE_RUNWAY_API_KEY=
CREATIVE_RUNWAY_MODEL=

CREATIVE_XAI_API_KEY=
CREATIVE_XAI_MODEL=
```

Google Video、Runway、xAI Videoは現行版では未接続です。キーを設定しても実動画生成済みとは表示されません。各ProviderのJob API、Webhook/Status、Download、保存期限を確認してAdapterを実装してから有効化します。

## 5. SNS Market Intelligence

Creative ProductionはMarket IntelligenceのEvidenceを入力に使います。必要な取得元だけ設定します。

```dotenv
X_BEARER_TOKEN=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
YOUTUBE_API_KEY=
MARKET_INTELLIGENCE_MANUAL_IMPORT_DIR=
MARKET_INTELLIGENCE_WEB_FEED_URLS=
```

TikTok、Instagram、Pinterestは現行版では承認済み手動Importです。

## 6. 段階的な疎通試験

1. アプリを再起動し、Capability Registryだけ確認する
2. APIキーやTokenの値が画面・ログへ表示されていないことを確認する
3. Quick/Draft、1 Platform、候補1件、画像・動画OFFでTextを試す
4. Cost/Token/Latencyの記録とProvider Consoleの利用量を照合する
5. Imageを1候補だけ生成し、解像度・Content Policy・保存先を確認する
6. Video Adapter実装後は1 ShotだけSubmitし、Status/Download/削除期限を確認する
7. Provider単体の失敗・429・TimeoutでFallback/Partialになることを確認する
8. Placeholderが実Assetとして承認されないことを確認する
9. Human Approval後も自動投稿されないことを確認する

## 7. 最後の有効化

すべての確認後だけ、次を変更します。

```dotenv
CREATIVE_EXTERNAL_API_ENABLED=true
```

さらに制作画面でも「設定済み外部生成APIの実行を許可」を明示的にONにします。環境変数だけで自動的に課金APIを呼ぶことはありません。
