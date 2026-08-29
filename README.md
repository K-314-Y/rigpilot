# RigPilot

Live2D Cubismの制作・検査を、安全な作業コピー上でオーケストレーションするためのローカル基盤です。

## Phase 0

現在は実機のCubismを編集しない検証フェーズです。

- 指定した元の `.cmo3` は変更せず、`source/` と `working/` にコピーする
- `source/` の SHA-256 を保持し、処理前に一致を検査する
- CubismとPC ControlはAdapterで隔離し、固有MCPツール名を中核へ持ち込まない
- 開いているCubism文書のパスと `working/` を照合できない場合は停止する
- 一時パラメータプレビューは、成功・失敗を問わず必ず解除する
- Emergency Stop検知時は即座に `emergency_stopped` に遷移する
- 監査ログには認証情報やスクリーンショット本体を保存しない

実機のCubism接続・GUI操作・ファイル保存は、Phase 0 の初期コードには含めません。

## 文書

- [一般利用者向け使用書（日本語）](USER_GUIDE_JA.md)
- [開発環境のセットアップ](SETUP.md)
- [実装状況](IMPLEMENTATION_STATUS.md)
