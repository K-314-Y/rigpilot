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

## 未実装

- CubismExternalEditMCPとの実接続
- Windows PC Control MCPとの実接続
- 実機のParameter Probe、スクリーンショット、Neutral復元
- Cubismの起動、保存、書き出し、モデル構造の編集
- GUI、最終確認画面、AIによる自動修正

したがって、Phase 0BのCLIはCubismを起動・操作・保存しません。`status` に表示される「未接続」「未実装」はエラーではなく、現在の正しい状態です。
