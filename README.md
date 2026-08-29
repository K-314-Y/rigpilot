# RigPilot

Live2D Cubismの制作・検査を、安全な作業コピー上でオーケストレーションするためのローカル基盤です。

## Phase 1（実装済み・公式サンプル実機確認済み）

現在は実機のCubismを保存・構造編集しない検証フェーズです。

- 指定した元の `.cmo3` は変更せず、`source/` と `working/` にコピーする
- `source/` の SHA-256 を保持し、処理前に一致を検査する
- CubismとPC ControlはAdapterで隔離し、固有MCPツール名を中核へ持ち込まない
- 開いているCubism文書のパスと `working/` を照合できない場合は停止する
- 一時パラメータプレビューは、成功・失敗を問わず開始時の値へ復元し、読取り確認を行う
- `doctor`で実機確認に必要な状態と次の操作を表示し、`verify-live`で安全な接続確認を一括実行する
- `validate --dry-run`でモデル検査の予定を確認し、`validate`で基本動作を一巡検査する
- 検査では既存Parameterの最小値・既定値・最大値を一時的に試し、すべて開始時の値へ戻す
- Emergency Stop検知時は即座に `emergency_stopped` に遷移する
- 監査ログには認証情報やスクリーンショット本体を保存しない

Cubismの保存、書き出し、構造編集、削除は実装しません。2026-08-29に公式サンプルのworkingコピーでPhase 1検査を実行し、10カテゴリのPASS、1カテゴリのSKIPPED、全対象の復元読取り一致、3ファイルのSHA-256不変、利用者による中立状態の目視確認を得ました。別モデル・別環境での結果を保証するものではありません。

## Phase 2A: Safe Edit Transaction Foundation（実装済み・実機Stage 1前）

Phase 2Aは自動修正ではありません。CubismのEdit権限で、描画結果に影響しないPartの`LabelColorType`を一度だけ変更し、読取り、元値へのrollback、Object比較、既存Phase 1検査までを一つの取引として確認する基盤です。

- `edit-test --dry-run`は編集Toolを呼ばず、対象Part・元値・変更予定だけを表示する
- 実行時だけEdit承認を確認し、`validate`・`verify-live`にはEditを要求しない
- 許可する編集は`cubism_edit_part`の`label_color_type`だけ。Save・Export・batch編集・Parameter Key・ArtMesh・Deformerの変更は呼ばない
- Emergency Stop後はrollbackを含む追加の自動編集を送らず、未保存でCubismを閉じるよう案内する

## 文書

- [一般利用者向け使用書（日本語）](USER_GUIDE_JA.md)
- [開発環境のセットアップ](SETUP.md)
- [実装状況](IMPLEMENTATION_STATUS.md)
