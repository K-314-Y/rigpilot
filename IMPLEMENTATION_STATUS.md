# 実装状況

最終確認日: 2026-08-29

## Phase 0A: 基盤

実装済みです。

- `.cmo3` の元ファイルを読み取り、`source/` と `working/` に別コピーを作成
- 元コピーのSHA-256検証
- JSONによるプロジェクト状態・チェックポイント・監査ログの保存
- CubismとWindows PC Controlを分離するAdapter境界
- 対象不一致とEmergency Stopの状態遷移
- テスト用の一時Parameterプレビューと必ず解除する経路

## Phase 0B: 利用者導線

実装済みです。

- `rigpilot status` または `python -m rigpilot status`
- `rigpilot init` による安全な作業コピー作成
- `rigpilot project-status` による元コピーの整合性確認
- [一般利用者向け使用書](USER_GUIDE_JA.md)

## Phase 0B: Real MCP Integration

コード実装済み・Fake MCP確認済みです。

- `rigpilot status --live`によるCubism MCP、PC Control MCP、Allow、緊急停止、Cubismウィンドウの読み取り確認
- `rigpilot probe`による既存Parameterの一時値設定、画面取得、元値復元、値クリア
- workingコピーとCubism文書のパス、model UID、document UID、編集モードの照合
- 例外・スクリーンショット失敗・UID変更・緊急停止時の復元試行と停止
- 編集・保存・書き出し・削除Toolを使わない制限

WindowsおよびCubism実機での確認は未実施です。

## 検証状態

- IMPLEMENTED: MCP client、Adapter、Identity Guard、Probe、CLI、使用書
- MOCK VERIFIED: Fake MCPによるProbe成功、画面取得失敗、UID変更、緊急停止、復元失敗
- WINDOWS VERIFIED: 未確認（読み取り専用`status --live`は実行を試行したが、接続結果を取得できていない）
- CUBISM VERIFIED: 未確認
- UNVERIFIED: 実機でのAllow承認、PC側確認、3地点の画面取得、元値復元、`.cmo3`未保存

## 未実装

- Cubismの起動、保存、書き出し、モデル構造の編集
- GUI、最終確認画面、AIによる自動修正

Probeは一時値をCubismへ送りますが、保存・構造編集は行いません。`--live`や`probe`の実機結果は、Cubism側のAllowとPC側の確認を完了するまで未検証です。
