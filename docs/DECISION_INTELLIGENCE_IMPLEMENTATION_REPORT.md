# Decision Intelligence 実装レポート

## 実装済み

- Research → Evidence → Decision → Approval → Prediction → Outcomeの共通フロー
- FACT / CAUSE / HUMAN_INSIGHT / MARKET_STRUCTURE / STRATEGY / DECISION / EXECUTION_PLAN
- Evidenceの出典、URL、時点、信頼性、鮮度、関連性、独立性、品質、反証区分
- 11軸Opportunity Score（需要、成長、競争、収益化、利益、Content、SNS、差別化、参入難易度、Risk、Confidence）
- QUICK / STANDARD / DEEPと1段階Auto Escalation
- DEEPで必要時だけBull / Bear / Skeptic / Red Team / Judgeを3〜6役
- 保存済みSNS市場調査のEvidenceとScore入力へのゼロコスト変換
- 保存済みClaimからのOpportunity Discovery / Hidden Niche候補表示
- Human Approvalの承認、却下、修正依頼
- Prediction、Actual Outcome、Gross/Net Profit、Decision ROI、Error Cause、Lessons
- Provider / Model / Task別のCalls、Cost、Latency、Success、Prediction Accuracy、Approval、Profit
- Confidence帯と実成功率のCalibration
- 5判断以上の時だけ予測精度をRouter品質へ20%反映する保守的Adaptive Routing
- Versioned Decision / Outcome Memoryと応答Cache

## 未実装

- Evidence不足を検知して外部検索を自律反復する完全Research Loop
- Provider請求明細からの`actual_cost_usd`自動取込
- 閾値・Score Weight・Promptの自動更新
- DecisionからCreative / Publishing Jobを自動作成するExecution Orchestrator
- 外部Vector DB、Graph DB、専用Feature Store
- 完全自律の事業運営、無承認の広告購入・投稿・契約

## 使用Provider

- Local Demo: 常時利用可能、外部通信なし、Planning Cost $0
- OpenAI: Responses Adapter、設定済みModelを中央Gateway経由で利用
- Anthropic: Messages Adapter、設定済みModelを中央Gateway経由で利用
- Gemini: Generate Content Adapter、設定済みModelを中央Gateway経由で利用

Model名、Quality Hint、Latency Hint、Planning Reserveは環境設定から差し替えられます。

## モック箇所

- `AI_OS_MOCK_MODE=true`では外部Text Providerを呼ばずLocal Demoへ縮退
- SNS市場調査のMock Dataは既存の`is_mock` / `data_mode`表示を維持
- Opportunity Discoveryは生成Mockではなく保存済みClaimだけを使用
- `actual_cost_usd`はProvider Usage未正規化のため`null`。推定額を実額として表示しない
- External Creative / Publishingは既存のOFF / Dry Runを維持

## テスト結果

- 新規Decision Intelligenceテスト: Auto Escalation、Deep Roles、ROI/Prediction、保存Evidence再利用
- 既存Operating Systemテスト: Router、Budget、Fallback、Cache、Early Stop、Persistence
- 全テスト: 157件成功
- Ruff: 全対象成功
- Mypy: 127 source files、エラー0
- Compileall / DB init: 成功
- Streamlit AppTest: AI Decision Engine、Opportunity Discovery、Decision Track Record、Mainすべて例外0
- Local health endpoint: HTTP 200

## 次フェーズ拡張

1. Provider Usage / Billing Exportを取り込み、推定Costと請求Costを照合
2. Missing Evidence Questionを作り、承認されたSourceだけIncremental Research
3. Decision承認からCreative Brief作成までの明示的Hand-off
4. 予測件数が十分な指標だけでCalibration ErrorとRouter比較実験
5. Outcomeの原因分類を使ったPrompt / Threshold変更案の提示（自動適用はしない）

## ユーザーが最後に設定する外部接続

現時点で追加操作は不要です。実APIテストを行う最後の段階だけ、`.env`で次を確認します。

1. `AI_OS_MOCK_MODE=false`
2. `AI_OS_EXTERNAL_API_ENABLED=true`
3. 日次 / Run / Call / Search上限を希望額へ設定
4. 画面で対象Runの「外部AI APIを許可」をON
5. QUICKを1回だけ実行し、Provider Consoleの使用量とCost Ledgerを照合

SNS公開は別系統です。OAuth、Sandbox、Platform審査、Publishing Dry Runの確認後だけ既存の
Publishingチェックリストに従って有効化します。
