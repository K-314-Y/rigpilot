# 実装状況

最終確認日: 2026-08-30

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

## Phase 1: Automatic Live2D Model Validation

コード実装済み・Fake MCP確認済み・公式サンプルでの実機確認済みです。

- `rigpilot validate --dry-run`はCubismのParameter一覧と開始時値を読み取り、変更せずに検査予定を表示する
- `rigpilot validate`は顔、まばたき、視線、口、体の既存Parameterだけを検査する。存在しない項目は`SKIPPED`であり失敗ではない
- 各状態で検査対象全体の開始時値と今回の値をまとめて送るため、複数Parameter検査でも他の対象を開始時値に維持する
- 各状態でParameter読取りと画面取得を確認し、各検査後と最終処理で全対象Parameterを開始時値へ復元・読取り確認する
- スクリーンショット失敗、MCP失敗、Identity不一致、Emergency Stop、復元不一致では後続検査を中止し、復元とSHA-256確認を優先する
- `reports/phase-1-validation-*.json`に詳細レポートを保存する。画像本体は保存しない
- Save、Export、構造編集、削除、画像による自動修正は実行しない

2026-08-29に公式サンプルのworkingコピーで実行しました。顔（左右・上下・傾き）、まばたき、視線（左右・上下）、口（開閉・表情）、体（左右・上下）の10カテゴリがPASS、`ParamBodyAngleZ`がない体の傾きはSKIPPEDでした。30回の画面取得、全対象Parameterの復元読取り一致、3ファイルのSHA-256不変、保存・書き出し・構造編集・削除未実行、利用者の中立状態目視確認を記録しました。詳細はプロジェクトの`reports/phase-1-validation-20260829T074217Z.json`にあります。

## Phase 2A: Safe Edit Transaction Foundation

コード実装済み・Fake MCP確認済み・公式サンプルでの実機Stage 1/2/3確認済みです。

- `rigpilot edit-test --dry-run`は、実編集をせずに編集可能なPart、現在の`LabelColorType`、一時値、rollback値を確認する
- `rigpilot edit-test`だけがEdit承認を確認する。許可する実編集はPartの`label_color_type`のみで、`cubism_edit_batch`、Parameter Key、ArtMesh、Deformer、Save、Exportは呼ばない
- 編集前にIdentity、Modeling mode、3ファイルのSHA-256、Allow、Edit、Emergency Stop、Part ObjectのSnapshotを確認する
- 一時編集のreadback、元値へのrollback readback、Object Before/Afterのcanonical比較、3ファイルのSHA-256不変を必須とする
- 通常エラーでは条件付き1回だけrollbackを再試行する。Emergency Stop後はrollbackを含む追加編集を送らず、`emergency_stopped`へ停止する
- rollback成功後だけ既存Phase 1 Validation Engineを1回実行する

2026-08-30に公式サンプルのworkingコピーで`edit-test --dry-run`を実行しました。`Part01HandR`の`LabelColorType`が`undefined`であることを読み取り、一時値`blue`、rollback値`undefined`の取引計画を生成しました。Edit Tool、Save、Exportは呼び出していません。

同日に`edit-test`を実行しました。`Part01HandR`の`LabelColorType`を`undefined → blue → undefined`として一時編集し、Edit Readback MATCH、Rollback Readback MATCH、対象Object Before/After IDENTICALを確認しました。続けてPhase 1 Validation Engineを1回実行し、10 PASS / 1 SKIPPED、全Parameter復元読取り一致、30回の画面取得を確認しました。公式原本、source、workingのSHA-256は不変で、Save・Exportは未実行です。利用者もモデル表示が開始前と同じであることを目視確認しました。詳細は`reports/phase-2a-edit-transaction-20260830T002638Z.json`にあります。

## Phase 2B: Candidate Sandbox Foundation

コード実装済み・Fake MCP確認済みです。実機のCandidate保存は未実施です。

- Candidateは`project/candidates/candidate-.../<working名>.cmo3`にのみ作成し、初期SHA-256がworkingコピーと一致することを確認する
- `CandidateRecord`はCandidateのパス、base/initial/current SHA-256、状態、model/document UID、検査結果、Promote可否を個別に保存する
- Candidate以外のパス、パストラバーサル、既存Candidateディレクトリへの上書きを拒否する
- `candidate-test --dry-run`はCandidateディレクトリ、MCP、Saveを一切呼ばない。通常実行は独立したEmergency Stop（デスクトップの「PC MCP 緊急停止」または`emergency-stop.cmd`）を手動確認して`--confirm-emergency-stop`を指定し、PC Control接続、`control_stopped: false`、停止ファイル不在を確認できない限りCandidate作成前にBLOCKEDとする。F11ホットキーは補助機能であり必須Gateではない
- 保存経路はCandidateだけを開き、Partの`LabelColorType`だけを変更し、保存直前に緊急停止、Cubismの前面化、UID、Candidate文書パス、source/working/公式原本のSHA-256を確認する
- 保存応答だけでは成功とせず、CandidateファイルのSHA-256変化と連続読取りでの安定化を必須とする
- Candidate Validationは`ValidationTarget(role="candidate")`を明示して実行し、ProjectRecordのworkingコピーを置き換えない
- Phase 2Bは自動Promoteをしない。検査済みCandidateは削除せず`REJECTED`として残す

2026-08-30の旧Safety Gate下で作成されたBLOCKED Candidateは監査記録として保持し、再利用しない。新方針では、Emergency Stopボタンと手動resumeの実機確認後に新規Candidateで開始する。source、working、公式原本の保存は行っていない。

未確認: Windows PC Control MCPの独立したEmergency Stopボタンとmanual resume、Candidateの実機Save、Candidate SHA-256安定化、Candidate Validation、利用者の画面目視確認。

## 検証状態

- IMPLEMENTED: MCP client、Adapter、Identity Guard、Probe、setup/doctor/verify-live/validate/edit-test CLI、使用書
- MOCK VERIFIED: Fake MCPによるProbe成功、画面取得失敗、UID変更、緊急停止、復元失敗、復元読取り不一致の1回再試行、source/working SHA-256変化、スクリーンショット待機・一時休止・1回再試行、Phase 1のPlan生成、SKIPPED、複数Parameter復元、dry-run、JSONレポート、Phase 2AのEdit未承認・dry-run・一時readback・rollback・条件付きretry・Emergency Stop・hash変化・最終検査再利用
- WINDOWS VERIFIED: Windows PC Control MCPの接続、Cubismウィンドウ検出、クールダウン適用下の連続スクリーンショット4回、緊急停止OFF
- CUBISM VERIFIED: Allow/Edit承認、workingコピーの識別照合、`ParamAngleX`の一時操作・復元・読取り一致、Phase 1の10カテゴリPASS・30回の画面取得、Phase 2Aの`LabelColorType` round-trip、利用者の中立状態目視確認
- VERIFIED IN THIS RUN: 公式原本、source、workingのSHA-256不変。Phase 2AのObject Before/After一致。保存、書き出し、構造編集、削除は実行していない
- UNVERIFIED: 他のCubismバージョン、別モデル、別Windows環境での再現性

## 未実装

- Cubismの起動、保存、書き出し、自動修正、Parameter Key・ArtMesh・Deformer・Part構造の編集
- GUI、最終確認画面、AIによる自動修正

Probeは一時値をCubismへ送りますが、保存・構造編集は行いません。今回の実機確認は公式サンプル1件に限られます。
