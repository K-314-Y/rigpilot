# RigPilot 使用書（Phase 0B.1）

Python、Git、MCP、Live2D Cubismに詳しくない方向けの手順です。

## RigPilotとは

RigPilotは、AIがLive2D Cubismを安全な範囲で確認することを助けるローカルツールです。Phase 0B.1は完成製品ではありません。公式サンプルモデルの**作業コピー**をCubismで確認し、既存のParameterを一時的に動かし、画面を取得して、開始時の値へ戻せるかを検証します。

この段階で自動制作、保存、書き出し、Parameter・Part・Deformerの構造変更はしません。実機での検証結果は、まだ「未確認」から始まります。

## 最短で試す

1. Live2D公式サイトで、公式サンプルの利用条件を確認してから、公式サンプルを自分でダウンロードします。RigPilotは規約へ同意したり、サンプルをダウンロードしたりしません。
2. サンプル内の`.cmo3`の場所をRigPilotへ渡します。自分のLive2Dモデルは不要です。
3. RigPilotが`source/`と`working/`のコピーを作ります。
4. Live2D Cubismを起動し、**workingコピーだけ**を開きます。
5. Cubismの外部アプリ連携でAllowを承認します。
6. `verify-live`を実行し、最後にCubism上で元の位置へ戻ったことを確認します。

まだ公式サンプルを用意していない場合は、ここで止めてください。規約への同意とダウンロードは利用者自身の操作です。

## 必要なもの

- Windows
- Live2D Cubism Editor（CubismExternalEditMCPが対応する環境）
- Python 3.11以上（RigPilot）
- RigPilot
- Windows PC Control MCP（同MCPのREADMEではWindows 10/11、Python 3.11〜3.13）
- CubismExternalEditMCP（同MCPの公式READMEではPython 3.10以上、Cubism Editor 5.4 Alphaが対象）

Cubismの対応バージョンは実機で未確認です。手元のCubismが使えるとは、事前に断定しません。

## 初回セットアップ

### 1. RigPilotを準備する

RigPilotフォルダーで一度だけ実行します。

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m rigpilot status
```

`RigPilot: 準備完了（Phase 0B.1）`と表示されれば成功です。失敗した場合は、Python 3.11以上と、RigPilotフォルダーを開いていることを確認してください。

### 2. PC Controlの場所を設定する

次のコマンドはWindows PC Control MCPを探し、`rigpilot.local.json`を作成します。既にある設定は上書きしません。

```powershell
.\.venv\Scripts\python.exe -m rigpilot setup
```

見つからないと表示されたときだけ、そのMCPフォルダーを指定します。

```powershell
.\.venv\Scripts\python.exe -m rigpilot setup --pc-control-root "C:\PC Control MCPのフォルダー"
```

成功すると設定ファイルの場所が表示されます。失敗してもモデルには何も行いません。

### 3. 公式サンプルから安全なコピーを作る

利用条件を確認済みの公式サンプルにある`.cmo3`を指定します。次の`--model`だけ実際のファイルに置き換えます。

```powershell
.\.venv\Scripts\python.exe -m rigpilot init --workspace .\projects --project-id official-sample-check --model "C:\公式サンプルの場所\model.cmo3"
```

成功すると、`projects\official-sample-check\source\`と`working\`に別コピーが作られます。元のダウンロードファイルは直接操作しません。

## Cubism側の準備

1. Live2D Cubismを起動します。
2. 上で作成された`working\`内の`.cmo3`だけを開きます。`source\`や公式ダウンロード原本は開きません。
3. CubismExternalEditMCPの公式手順に従い、「外部アプリケーション連携」を有効にします。
4. Cubismが表示するAllowを承認します。

Allowは接続と一時Parameter操作の許可です。Phase 0B.1ではEdit（モデル構造を永続変更する許可）は要求しません。Cubismを再起動した場合、連携設定とAllowを再確認してください。

## Windows PC Control MCPの準備

Windows PC Control MCPのREADMEどおりに準備します。RigPilotで必要なのは状態確認、Cubismウィンドウの前面化、スクリーンショットだけです。

- `config.json`の`applications.live2d`が実際のCubismを指すことを確認します。
- RigPilotの`working`フォルダーを`allowed_roots`（許可対象の上限フォルダー）に含めます。
- 既存の安全機構を無効にしないでください。

## 接続確認（Doctor）

作業コピーを開いたら、次を実行します。

```powershell
.\.venv\Scripts\python.exe -m rigpilot doctor --project .\projects\official-sample-check\project.json
```

`OK`はその項目を確認できた意味です。`AWAITING USER ACTION`、`待機中`、`要確認`は失敗扱いではなく、表示された「次の操作」を一つだけ行う状態です。Cubismが起動していない、workingモデルが開かれていない、Allowが未承認の場合に、存在しない`OK`は表示しません。

## Phase 0B.1の実行

Doctorが`Safe Probe: READY`になったら、一括検証を実行します。

```powershell
.\.venv\Scripts\python.exe -m rigpilot verify-live --project .\projects\official-sample-check\project.json
```

RigPilotは、model UID、document UID、workingパス、編集モード、既存Parameter、Part構造を確認してから開始します。`ParamAngleX`、`ParamEyeLOpen`、`ParamMouthOpenY`を優先し、なければ安全な既存Parameterを選びます。開始時の値、中央付近、最大値、開始時の値の順に一時操作します。

画面はBaseline、中央付近、最大値、復元後に取得します。画像本体はRigPilotの監査ログに保存しません。復元後は値を読み直し、開始時の値と一致するかを確認します。不一致なら1回だけ再試行し、改善しなければ停止します。

## 正常終了時に確認すること

- Cubism上のモデルが開始時の位置へ戻っている
- `source`と`working`のSHA-256が処理前後で不変
- `.cmo3`を保存していない
- `logs\audit.jsonl`に結果がある（画像・認証情報は含めない）
- エラー表示がない

実機で未実施の項目は、成功したように扱いません。

## エラーと対処

| 表示・状況 | 確認すること |
| --- | --- |
| Cubismに接続できない | Cubismを起動し、workingコピーを開き、外部アプリ連携とAllowを確認します。 |
| PC Control MCPに接続できない | `rigpilot setup`とPC Control MCPのREADMEを確認します。 |
| モデルが見つからない | `--model`で指定した公式サンプルの`.cmo3`を確認します。 |
| model UIDが変わった | 別のモデルへ切り替わった可能性があります。保存せず、workingコピーだけが開かれているか確認します。 |
| Screenshot取得失敗 | Probeは復元を試みます。連続実行せず、CubismウィンドウとPC Controlの状態を確認します。 |
| Restore Readbackが不一致 | 1回の再試行後に停止します。Cubismで開始時の値を確認し、保存せずに状況を確認してください。 |
| Emergency Stop | 自動再開しません。次章を確認します。 |

## 緊急停止

異常を感じたら、Windows PC Control MCPの実装済みの方法で止めます。

1. **Ctrl + Alt + Shift + F12**を押す。
2. 作成済みならデスクトップの「PC MCP 緊急停止」をダブルクリックする。
3. Windows PC Control MCPフォルダーの`emergency-stop.cmd`を実行する。
4. マウスを画面左上へ移動してPyAutoGUIのフェイルセーフを使う。

緊急停止は`EMERGENCY_STOP`として保存され、MCPを再起動しても自動解除されません。原因を確認するまで`resume_control`で再開しないでください。

## 安全について

Phase 0B.1では、元の`.cmo3`を変更・保存せず、モデルを保存せず、Parameter構造、Part、Deformerを変更せず、削除・exportもしません。一時的なParameter値だけを使用し、対象はworkingコピーに限定します。それでもソフトウェアなので絶対の保証はできません。大切なデータは別途バックアップしてください。

## よくある質問

### 元のLive2Dモデルは壊れませんか？

公式サンプル原本、RigPilotの`source`、`working`を分離し、SHA-256で確認します。保存や構造編集はしませんが、バックアップは推奨します。

### Live2Dを知らなくても使えますか？

最終製品ではそれを目標にしています。Phase 0B.1ではCubismの起動、workingコピーを開くこと、Allowの承認、最後の目視確認は利用者が行います。

### AIが勝手に保存しますか？

いいえ。Phase 0B.1には保存Toolを使う処理はありません。

### AIがPC全体を操作できますか？

いいえ。Windows PC Control MCPの許可範囲に限られます。RigPilotは任意のShell実行機能を追加していません。

### インターネットへモデル画像を送りますか？

RigPilotには画像を外部送信する機能はありません。PC Control MCPの画像は通常メモリー上で返りますが、接続先のAIクライアントの履歴・キャッシュの扱いは別途確認してください。

## 将来の完成版（予定）

これは未実装です。

```text
1. キャラクター素材を指定
2. 「Live2Dにして」と依頼
3. RigPilotが自動制作
4. 内部で動作検査
5. モーションを最終確認
6. 普通の日本語で修正指示
7. 完成
```

## 文書の役割と手動操作

- `README.md`：プロジェクト概要
- `SETUP.md`：開発環境セットアップ
- `USER_GUIDE_JA.md`：一般利用者向け使用書
- `IMPLEMENTATION_STATUS.md`：現在の実装状況
- `docs/`：詳細設計

現時点で利用者が行う操作は、公式規約の確認・サンプルのダウンロード、Cubismの起動、workingコピーを開く、Allowと必要時のPC確認、最後の目視確認です。将来は安全を確認しながら、サンプル探索、設定作成、作業コピー作成、診断、Parameter選択、検証実行をさらに減らします。
