# 実装状況

最終確認日: 2026-08-29

## Phase 0A: 基盤

実装済みです。

- `.cmo3` の元ファイルを読み取り、`source/` と `working/` に別コピーを作成
- 元コピーのSHA-256検証
- JSONによるプロジェクト状態・チェックポイント・監査ログの保存
- CubismとWindows PC Controlを分離するAdapter境界
- 対象不一致とEmergency Stopの状態遷移
- テスト用の一時Parameterプレビュー、開始時値への復元、読取り確認の経路

## Phase 0B: 利用者導線

実装済みです。

- `rigpilot status` または `python -m rigpilot status`
- `rigpilot init` による安全な作業コピー作成
- `rigpilot project-status` による元コピーの整合性確認
- [一般利用者向け使用書](USER_GUIDE_JA.md)

## Phase 0B: Real MCP Integration

コード実装済み・Fake MCP確認済みです。

- `rigpilot status --live`によるCubism MCP、PC Control MCP、Allow、緊急停止、Cubismウィンドウの読み取り確認
- `rigpilot probe`による既存Parameterの一時値設定、画面取得、元値復元、読取り確認
- workingコピーとCubism文書のパス、model UID、document UID、編集モードの照合
- 例外・スクリーンショット失敗・UID変更・緊急停止時の復元試行と停止
- 編集・保存・書き出し・削除Toolを使わない制限

## Phase 0B.1: Official Sample Model Live Verification

コード実装済み・Fake MCP確認済み・公式サンプルでの実機確認済みです。

- `rigpilot setup`は既存設定を上書きせず、見つけられたWindows PC Control MCPをローカル設定へ記録
- `rigpilot doctor`は安全な作業コピー、MCP、Cubism、Allow、緊急停止を実際に確認できた項目だけ表示し、待機時は次の1操作を案内
- `rigpilot open-working`はPC Control MCPの既存許可範囲とWindows承認を通して、workingコピーだけを既定アプリで開く
- `rigpilot verify-live`はDoctor、read-only preflight、Baseline/Probe/復元後の画面取得、開始時値への復元と読取り確認、SHA-256の前後比較、監査ログを一括実行
- 連続スクリーンショットはWindows PC Control MCPの`get_pc_status`を確認し、クールダウン・一時休止中は上限付きで待機する
- 画像応答なしは状態確認後に1回だけ再試行し、復旧しない場合は復元を優先して停止する
- 復元後に`ClearParameterValues`は呼ばず、開始時値を一時Parameter値として保持して読取り確認する。復元試行の時刻・要求値・読取り値を監査記録へ残す
- 公式サンプル原本、`source`、`working`をSHA-256で監視し、変更があれば`needs_human_review`として停止
- Edit権限、保存、書き出し、構造編集、削除を要求・実行しない

2026-08-29に公式サンプルのworkingコピーで実機検証しました。`ParamAngleX`を`0.0 → 15.0 → 30.0 → 0.0`として一時操作し、Baseline、2回のProbe、復元後の4枚の画面取得、復元読取り一致、3ファイルのSHA-256不変、利用者の画面目視確認を確認しました。

## 検証状態

- IMPLEMENTED: MCP client、Adapter、Identity Guard、Probe、setup/doctor/verify-live CLI、使用書
- MOCK VERIFIED: Fake MCPによるProbe成功、画面取得失敗、UID変更、緊急停止、復元失敗、復元読取り不一致の1回再試行、source/working SHA-256変化、スクリーンショット待機・一時休止・1回再試行
- WINDOWS VERIFIED: Windows PC Control MCPの接続、Cubismウィンドウ検出、クールダウン適用下の連続スクリーンショット4回、緊急停止OFF
- CUBISM VERIFIED: Allow承認、workingコピーの識別照合、`ParamAngleX`の一時操作・復元・読取り一致、4枚の画面取得、利用者の中立状態目視確認
- VERIFIED IN THIS RUN: 公式原本、source、workingのSHA-256不変。保存、書き出し、構造編集、削除は実行していない
- UNVERIFIED: 他のCubismバージョン、別モデル、別Windows環境での再現性

## 未実装

- Cubismの起動、保存、書き出し、モデル構造の編集
- GUI、最終確認画面、AIによる自動修正

Probeは一時値をCubismへ送りますが、保存・構造編集は行いません。今回の実機確認は公式サンプル1件に限られます。
