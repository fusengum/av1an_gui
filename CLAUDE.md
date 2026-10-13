# av1an_gui 開発メモ（Claude Code 用）

av1an を画面（デスクトップ / Web）で操作する分割並列エンコードのツール。GitHub で公開する。
Windows / Linux 対応。素の VM から Python 不要で全自動セットアップできることが前提。

## 守ること
- **公開リポジトリ**。特定ベンダー独自形式の音声・HDR メタデータの処理や、その名前は書かない。そうした処理は別リポジトリの拡張からフックで差し込む（下記）。フックの形を変えるときは互換性を保つ
- `shared/` は汎用の基盤。アプリ固有の処理・外部フォルダの定義は各アプリの `app.json` / パッケージ側に置く
- `.ps1` は UTF-8 **BOM 付き**（Windows PowerShell 5.1 は BOM なしを Shift_JIS で読み、日本語コメントで構文が壊れる）。`.bat` は ASCII のみ
- PowerShell は変数名の大文字小文字を区別しない。`$C` と `$c` のような衝突に注意（`install_gui.ps1` の色表は `$Clr`、DPI 倍率は `$script:DpiScale`）
- 利用者に見える文言（GUI・Web・CLI・インストーラー・README）は です・ます体。ボタン名・項目名などの短いラベルは体言のまま。コード内コメントは問わない
- 状態語（待機・処理中・完了・失敗・中止、手動・アシスト・自動 など）はコード内で比較しているので変えない
- 並列数は特定のコア数を前提にしない（8 コア〜128 コア以上まで同じ計算で決まること）
- インストーラーは入っているものを入れ直さない：uv・Python（3.12 以上 3.14 未満）・ライブラリ・外部ツールは、このフォルダ内 → PATH の順に探し、条件を満たせば使う。`--force` / `-Force` のときだけ取り直す

## 構成
- `shared/`: 共通基盤。`app_infos()` が `<pkg>/app.json` を探してアプリを見つける。`python -m shared setup|check|apps|folders`
  - 拡張用フック: `shared.core.probe` の ENRICHERS / VIDEO_KIND_HOOKS / VIDEO_DETAIL_HOOKS / AUDIO_NAME_HOOKS、`shared.core.tools` の INSTALLERS / EXTRA_SEARCH / EXTRA_CHECKS
  - `core/av1an.py`: av1an・エンコーダの検出と導入（Windows は MSYS2 ucrt64 の依存閉包、Linux は Docker `masterofzen/av1an:master`）、並列数計算（`WORKER_COST` / `auto_workers` / `plan_workers` / `resolve_workers`）
  - `gui/worker_assist.py`: 並列数アシスト
  - `web/`: FastAPI + データ駆動の `static/index.html`。各アプリの `webapp.py` の `make()` が WebApp を返す
  - キューを持つのは Web サーバーだけ。デスクトップ版は `web/client.py` でサーバーにジョブを送り、`/api/state` を見て表示する（サーバーは `logs/web.json` にポートを書く。無ければデスクトップ版が起動する）
  - 設定画面: 各アプリの `webapp.py` の `settings_sections`（見出し付きの項目定義）から、デスクトップ版（`gui/settings_form.py`）と Web 画面の設定タブの両方を作る。`main=True` はデスクトップ版ではメイン画面に置く項目
  - Web サーバーの待ち受けは `settings_web.json`（`web/config.py`）。終わった後の電源操作は `core/power.py` をサーバーのキューが実行する
  - Web サーバーはフォルダごとに 1 つだけ（`logs/web.lock` を排他で持つ）。Windows は 0.0.0.0 と 127.0.0.1 で同じポートを別プロセスが待ち受けられるので、ポートの重なりでは防げない
- `av1gui/`: アプリ本体（`app.json`、`env.py`、`webapp.py`、`config.py`、`encode.py`、`encoders.py`、`gui/`）
- インストーラー: `install.bat` → `install_gui.ps1`（WinForms）→ `install.ps1 -Gui` → `python -m shared setup --progress`。進捗行は `@@STEP id state label` / `@@PROG frac msg` / `@@INFO` / `@@DONE` / `@@FAIL`
- Linux: `install.sh`（systemd の Web サービス、ランチャー）

## 確認
- `python -m py_compile` を全 .py に
- `.ps1` は BOM の有無と構文（pwsh の `[System.Management.Automation.Language.Parser]::ParseFile`）
- GUI は `QT_QPA_PLATFORM=offscreen` で起動確認できる
