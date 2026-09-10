# Business Decision Intelligence / AI Decision Engine

## 目的

市場調査、Evidence、複数AI、意思決定、Human Approval、制作、配信、実績を1つの
Decision ID / Run IDでつなぐローカル優先の意思決定レイヤーです。AIの文章量ではなく、
根拠・不確実性・予測・利益・実績を追跡できることを優先します。

## Decision Flow

```text
保存済みSNS市場調査または手動Evidence
  -> Evidence Score / Opportunity Score（決定論的）
  -> Adaptive Compute（QUICK -> STANDARD -> DEEP）
  -> Global / Daily / Run Budget Guardrail
  -> Model Router（実績5件未満は設定値、5件以上は予測精度を20%反映）
  -> AI Gateway（cache -> provider -> fallback）
  -> 必要時だけBull / Bear / Skeptic / Red Team / Judge
  -> GO / NO_GO / INVESTIGATE_MORE
  -> APPROVE / REJECT / MODIFICATION_REQUESTED
  -> Prediction -> Actual Outcome -> Profit / Decision ROI / Calibration
```

AI出力は助言です。外部制作・SNS公開は、このDecision承認だけでは実行されません。
Publishing側のHuman Approval、Dry Run、Kill Switchが別に必要です。

## Research Modes

- `QUICK`: 候補探索・分類。通常は低コストProvider 1件。
- `STANDARD`: 重要度、不確実性、必要品質に応じて独立検証を追加。
- `DEEP`: 必要時だけ3〜6役。Bull / Bear / Skeptic / Red Team / JudgeがEvidence、
  前提、予測、反証を比較する。

重要度、不確実性、潜在損失、可逆性、Evidence Scoreが閾値を超えると1段階だけ自動深掘り
します。ユーザーは自動深掘りをRun単位で無効にできます。

## Evidence Contract

Decision Evidenceには次を保存します。

- `claim`, `source`, `source_type`, `source_url`
- `published_at`, `retrieved_at`, `observed_at`
- `reliability`, `recency_score`, `relevance`
- `agreement_between_sources`, `independence_of_sources`, `data_quality`
- `sample_size`, `support_or_contradict`, `official_source`

Decision Outputは`facts / inferences / assumptions / predictions`を別フィールドで保持し、
7段階（FACTからEXECUTION_PLAN）で表示します。

## Cost Protection

外部AIの実行には、全体ロックとRun承認の両方が必要です。さらにMock Mode、日次予算、
Run予算、モデル呼び出し回数、検索回数で制限します。

```dotenv
AI_OS_MOCK_MODE=true
AI_OS_EXTERNAL_API_ENABLED=false
AI_OS_DAILY_BUDGET_USD=1.0
AI_OS_MAX_RUN_COST_USD=0.25
AI_OS_MAX_MODEL_CALLS_PER_RUN=5
AI_OS_MAX_SEARCH_CALLS_PER_RUN=1
```

`estimated_cost_usd`は計画用の安全側見積りで、請求額ではありません。Provider使用量を
正規化できない呼び出しの`actual_cost_usd`は`null`のまま保持し、推定と実績を混同しません。

## Persistence

- `os_runs`: Request、実行Mode、Route、Budget、所要時間、終了状態
- `os_model_calls`: 既存互換のモデル呼び出しログ
- `os_cost_ledger`: Provider、Model、Task、Tokens、推定/実コスト、Latency、Success
- `os_decisions`: Decision OutputとHuman Approval
- `os_predictions`: 指標、予測値/範囲、Confidence、実績値、誤差
- `os_decision_outcomes`: Revenue、全Cost、Gross/Net Profit、Decision ROI、原因、Lessons
- `os_memories`: Decision / Outcomeのハッシュ付きVersioned Memory
- `os_cache`: LLM応答キャッシュ
- `os_outcomes`: Market / Creative / Publishingをつなぐ既存成果Bridge

既存DBへは追加テーブルだけを作成し、既存テーブルのALTERを要求しません。

## Current Limits

- 外部検索を自律反復する完全Research Loopは未実装。保存済み調査の再利用を優先。
- Provider請求APIとの照合は未実装。実請求額はProvider Consoleで確認が必要。
- CalibrationとAdaptive Routerは記録済み実績だけを使い、自動学習・重み自動更新はしない。
- Opportunity Discoveryは保存済みClaimの再ランキングであり、Evidenceのない市場を生成しない。
- Execution PlanはHuman Approval後の提案であり、自動執行しない。
