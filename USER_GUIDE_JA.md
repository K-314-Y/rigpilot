# RigPilot 使用書（Phase 0B）

この文書は、Python、MCP、Git、Live2D Cubismに詳しくない方向けです。

## 最初に: いまできること

RigPilotは、AIがLive2D Cubismの制作・確認・修正を支援するためのツールです。

Phase 0Bは完成製品ではありません。元の`.cmo3`から安全なコピーを作り、設定済みならCubismへ読み取り接続し、一時的なParameter（モデルを動かすための値）操作・画面取得・元値復元を行えます。実機Cubismでの確認はまだ完了していません。

```text
現在
元モデルを指定 → 安全なコピーを作成 → 状態を確認

将来
Cubismを起動 → RigPilotを起動 → モデルを指定 → 一時的に動作確認
→ 画面を確認 → 元の状態へ復元
```

「未接続」「未実装」と表示されるのは、Phase 0Bでは正常です。Cubismを勝手に起動・保存・編集しないための明示です。

## 1. 必要なもの

| 必要なもの | Phase 0Bでの用途 | 確認できた条件 |
| --- | --- | --- |
| Windows | RigPilotを使うPC | Windowsを基準に案内します。 |
| Python | RigPilotを起動するため | RigPilotはPython 3.11以上を必要とします。 |
| RigPilot | 安全なコピーと状態確認 | このリポジトリです。 |
| Live2D Cubism Editor | 将来の実機確認 | Phase 0Bでは起動・操作しません。 |
| Windows PC Control MCP | 将来の画面確認・緊急停止 | 既存READMEではWindows 10/11、Python 3.11〜3.13です。 |
| CubismExternalEditMCP | 将来のCubism連携 | 公式READMEではPython 3.10以上、Cubism Editor 5.4 Alphaが対象です。 |

CubismExternalEditMCPのREADMEには、Cubism Editor 5.4 Alphaの有効期限が**2026-09-14**と記載されています。Phase 0Bでは実機連携をまだ検証していないため、手元のCubism 5.3などが対応すると判断しないでください。

## 2. 初回セットアップ

### 2.1 RigPilotを準備する

RigPilotのフォルダーを開き、次を上から順に実行します。一度だけ必要です。

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

成功すると、`.venv`という補助フォルダーが作成されます。失敗した場合は、Python 3.11以上が入っているか、RigPilotのフォルダーで実行しているかを確認してください。

次に、RigPilotの現在の状態を表示します。

```powershell
.\.venv\Scripts\python.exe -m rigpilot status
```

`RigPilot: 準備完了（Phase 0B）`と表示されれば成功です。これはローカル基盤の状態です。実機接続は次の設定後に確認します。

### 2.2 Windows PC Control MCPを準備する（将来のため）

Phase 0BのRigPilotを使うだけなら、PC Control MCPの準備は不要です。将来の画面確認に備える場合は、PC Control MCPのフォルダーにある`install.cmd`をダブルクリックしてインストールします。

Codexへ登録する場合は、同じフォルダーで次を実行し、Codexを完全に再起動します。

```powershell
.\register-codex.ps1
codex mcp list
```

成功すると、`codex mcp list`にPC Control MCPが表示されます。表示されない場合は、PC Control MCPのREADMEにある「MCPが表示されない」を確認してください。

### 2.3 CubismExternalEditMCPを準備する（将来のため）

CubismExternalEditMCPは[公式README](https://github.com/nana7chi/CubismExternalEditMCP)の「Quick Start」に従って準備します。MCPは、AIアプリとCubismの間をつなぐ小さな連携プログラムです。

## 3. Cubism側の準備（将来の実機連携時）

実機Probeの前に、CubismExternalEditMCPの公式READMEに従って次を確認します。

1. Cubism Editor 5.4 Alphaを起動し、モデルを開きます。
2. 「ファイル」→「外部アプリケーション連携の設定」を開きます。
3. ポートが`22033`であることを確認し、「使用」をオンにします。
4. 承認画面で連携プログラムを確認します。

`Allow`は、読み取り・一時的な値の設定を許可する権限です。`Edit`は、Parameter、Part、Deformerなどのモデル構造を永続的に変更する権限です。CubismExternalEditMCPの公式READMEは編集機能を含む通常設定としてAllowとEditの両方を案内しています。

Phase 0BのProbeは、モデル構造を編集しません。Allowは必要ですが、Editは`NOT REQUIRED`です。Cubismを再起動すると、外部連携の設定・承認をやり直す必要があります。

## 4. Windows PC Control MCPの準備（将来の画面確認時）

PC Control MCPは、許可されたアプリとフォルダーだけを操作する安全装置です。Phase 0Bで必要なのは、将来使うモデルの場所が`config.json`の`allowed_roots`（開く・許可対象にできる上限フォルダー）に入ることの確認だけです。

- Cubismの実行ファイルが`applications.live2d`に設定されていることを確認します。
- 作業フォルダーだけを許可対象にします。元のモデルを置いた広いフォルダー全体を無条件に許可しないでください。
- Phase 0Bでは編集許可、Cubism Trusted Mode、Persistent Trusted Authorizationを有効にする必要はありません。

PC Control MCPは、スクリーンショットを通常はメモリー上で返します。ただし、MCPクライアント側が会話履歴やキャッシュとして画像を保持する可能性があります。

## 5. RigPilotの起動

Phase 0Bで使えるコマンドは次のとおりです。Cubismを起動・保存・編集するコマンドはありません。

```powershell
# RigPilot自体の状態を確認する
.\.venv\Scripts\python.exe -m rigpilot status

# 元の.cmo3を読み取り、RigPilot用の安全なコピーを作る
.\.venv\Scripts\python.exe -m rigpilot init --workspace .\projects --project-id mia-check --model "C:\モデルの場所\model.cmo3"

# 作成済みコピーの状態を確認する
.\.venv\Scripts\python.exe -m rigpilot project-status --project .\projects\mia-check\project.json

# 実MCPへの読み取り接続を確認する（rigpilot.local.jsonが必要）
.\.venv\Scripts\python.exe -m rigpilot status --live

# Cubismでworkingコピーを開いてから、一時Probeを実行する
.\.venv\Scripts\python.exe -m rigpilot probe --project .\projects\mia-check\project.json
```

2つ目のコマンドでは、`C:\モデルの場所\model.cmo3`を実際の元モデルの場所に置き換えます。空白を含むパスは、必ず引用符で囲みます。

成功すると、`projects\mia-check`の中に次が作成されます。

```text
source/       元モデルを読み取って作った保護用コピー
working/      将来RigPilotが作業するコピー
checkpoints/  将来の復元地点
exports/      将来の書き出し先
logs/         操作記録
project.json  プロジェクトの状態
```

## 6. 接続確認

実MCPの設定前は、次の表示が正しい状態です。

```text
RigPilot: 準備完了（Phase 0B）
Cubism MCP: 未接続（実機連携は未実装）
Windows PC Control MCP: 未接続（実機連携は未実装）
Cubism Model: 未確認
Parameter Probe: 未実装
```

実機接続を確認するには、リポジトリの`rigpilot.local.example.json`を`rigpilot.local.json`へコピーし、`pc_control_mcp_root`を実際のWindows PC Control MCPフォルダーへ書き換えます。その後、`status --live`を実行します。`OK`／`要確認`は実機の状態を示します。

## 7. Phase 0Bでの安全な確認

`probe`は、Cubismで**RigPilotのworkingコピー**を開いた状態で実行します。Parameter ID、model UID、document UIDを手入力する必要はありません。RigPilotが既存Parameterを優先順で選び、`default → midpoint → maximum → 元値`を実行します。

Probe中、PC Control MCPがCubismを前面化するときは、Windows側の確認が表示される場合があります。許可しない場合、Probeは中断し、元値復元を試みます。

Probeは次の順序で動きます。画像本体はRigPilotの監査ログへ保存しません。

```text
対象モデル確認
↓
Parameterを選択
↓
一時的に動かす
↓
スクリーンショットで確認
↓
Neutral（通常の位置）へ戻す
↓
保存しないで終了
```

実装後は、Cubism上で「顔などが一時的に動き、数秒後に元の位置へ戻る」ことを確認します。

## 8. 正常終了時に確認すること

Phase 0Bでコピー作成が成功したら、次を確認します。

- 元の`.cmo3`が元の場所に残っている
- `project-status`の`元データコピー: OK`が表示される
- `projects\<プロジェクト名>\logs\audit.jsonl`が作成されている
- 元の`.cmo3`をRigPilotが保存した形跡がない
- エラー表示がない

まだ実機Probeをしていないため、「モデルがNeutralへ戻った」「スクリーンショットがある」はPhase 0Bの正常終了条件ではありません。

## 9. エラー時

エラーが出た場合、まず元のモデルを閉じたり保存し直したりせず、表示と次の表を確認してください。

| 表示・状況 | まず確認すること |
| --- | --- |
| `RigPilotを実行できませんでした` | コマンドをRigPilotフォルダーで実行しているか、`.cmo3`のパスが正しいかを確認します。元のモデルは変更されていません。 |
| Cubismに接続できない | Cubismを起動し、workingコピーを開き、外部アプリ連携をオンにしてAllowを確認後、`status --live`を再実行します。 |
| Windows PC Control MCPに接続できない | `rigpilot.local.json`のフォルダーを確認し、PC Control MCPを再起動してから`status --live`を再実行します。 |
| モデルが見つからない | `--model`に指定した場所と拡張子`.cmo3`を確認します。 |
| model UIDが変わった | UIDはCubismで開いているモデルを識別する番号です。Phase 0Bではまだ取得しません。将来この表示が出たら、別のモデルが開かれている可能性があるため処理を止めます。 |
| Screenshot取得失敗 | Probeは元値復元を試みます。連続実行せず、PC Control MCPの状態とCubismウィンドウを確認します。 |
| Neutralへ戻せなかった | Probeは`needs_human_review`として失敗します。その場で保存せず、緊急停止を使い、Cubism上の値を確認します。 |
| emergency stopが作動した | 次章に従い、原因を確認するまで再開しません。 |

## 10. 緊急停止

何かおかしいと感じたら、Windows PC Control MCPでは次のいずれかで停止できます。

1. **Ctrl + Alt + Shift + F12**を押す。
2. デスクトップの「PC MCP 緊急停止」をダブルクリックする（PC Control MCP側で作成済みの場合）。
3. PC Control MCPのフォルダーにある`emergency-stop.cmd`を実行する。
4. マウスを画面左上へ移動する。

停止すると、PC Control MCPの操作許可と編集フォルダー許可が取り消され、`EMERGENCY_STOP`状態が保存されます。MCPを再起動しても自動では解除されません。原因が分かるまで、`resume_control`による再開を急がないでください。

RigPilot Phase 0B自身はPC操作をまだ開始しないため、ここにある停止方法は将来PC Control MCPと連携する段階のための案内です。

## 11. 安全について

Phase 0Bでは、次を行いません。

- 元の`.cmo3`を変更しない
- 元のモデルを保存しない
- Parameter構造を変更しない
- Partを変更しない
- Deformerを変更しない
- ファイルを削除しない
- exportしない
- Parameter、Part、Deformerの永続編集をしない

`init`は、元モデルを読み取ってRigPilotフォルダー内へコピーするだけです。ソフトウェアである以上、絶対に問題が起きないとは約束できません。大切なモデルは、別の場所へバックアップしてから使ってください。

## 12. よくある質問

### Q. 元のLive2Dモデルは壊れませんか？

Phase 0Bの`init`は元ファイルを読み取り、別コピーを作ります。Probeはworkingコピーに一時値を送りますが、永続保存や構造編集は行いません。ただし、利用前にバックアップを作ることをおすすめします。

### Q. Live2Dを知らなくても使えますか？

最終製品では、Live2Dの専門知識がなくても使えることを目標にしています。Phase 0Bはそのための安全な基礎段階で、まだ自動制作機能はありません。

### Q. AIが勝手に保存しますか？

Phase 0BのRigPilotは保存しません。Probeは一時Parameter値を送りますが、終了時に元値復元と一時値クリアを試みます。

### Q. AIがPC全体を操作できますか？

Windows PC Control MCPは許可されたアプリとフォルダーに限定する設計で、任意のPowerShell、CMD、Pythonコードを実行する機能は追加していません。Phase 0BのRigPilotは、画面確認に必要な最小Toolだけを使用します。

### Q. インターネットへモデル画像を送りますか？

Phase 0BのRigPilotには、モデル画像をインターネットへ送る機能は実装していません。将来PC Control MCPで画面を取得するときも、MCP側は通常メモリー上で画像を返しますが、接続するAIクライアントが会話履歴やキャッシュとして保持する可能性はあります。

## 13. 将来の完成版の使い方

これは予定です。Phase 0Bで実装済みの機能ではありません。

```text
1. キャラクター素材を指定
2. 「Live2Dにして」と依頼
3. RigPilotが自動制作
4. 内部で動作検査
5. モーションを最終確認
6. 問題があれば普通の日本語で修正指示
7. 完成
```

## 14. 文書の役割

- `README.md` → プロジェクト概要
- `SETUP.md` → 開発環境セットアップ
- `USER_GUIDE_JA.md` → 一般利用者向け使用書
- `IMPLEMENTATION_STATUS.md` → 現在の実装状況
- `docs/` → 詳細設計（将来追加）

## 15. 手動で必要な操作

Phase 0Bで利用者が手動で行うことは次のとおりです。

1. PythonとRigPilotを一度だけ準備する。
2. Cubismを起動し、RigPilotのworkingコピーを開き、Allowを承認する。
3. PC Control MCPの設定を確認し、必要時のPC側確認を承認する。
4. `status --live`と`probe`を実行する。
5. 大切なモデルのバックアップを保管する。

将来削減する予定の手動操作は、MCPの登録、Cubism起動、モデルを開く、外部連携の許可、Parameter選択、画面確認です。各段階で安全に自動化できることを確認してから減らします。
