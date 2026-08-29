# 開発環境のセットアップ

この文書はRigPilotを開発・検証する人向けです。一般利用者は[使用書](USER_GUIDE_JA.md)を参照してください。

Windowsでリポジトリのルートを開き、Python 3.11以上で次を実行します。

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

検証は次の4項目です。

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check . --no-cache
.\.venv\Scripts\python.exe -m compileall -q src tests
git diff --check
```

実機のCubism、Windows PC Control MCP、CubismExternalEditMCPはこのリポジトリとは別コンポーネントです。設定や実機接続を追加する前に、[実装状況](IMPLEMENTATION_STATUS.md)の未実装項目を確認してください。
