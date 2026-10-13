# av1an_gui

[av1an](https://github.com/rust-av/Av1an) を画面で操作する、分割並列エンコードのツールです。
映像を場面ごとに細かく分け、エンコーダを CPU のコア数に合わせて何本も同時に動かします。8 コアの PC でも、128 コア以上のサーバーでも、並列数は自動で決まります。

- エンコーダ: **SVT-AV1** / aomenc / rav1e（AV1）、x265（HEVC）、x264（H.264）
- 実写・アニメ向けの設定を同梱（ファイルごとに切り替え。追加パラメータで上書きも可能）
- 並列数とスレッド数を CPU 数・空きメモリから自動決定（上限なし）
- 目標画質（VMAF）モード、途中で止めても続きから再開
- 黒帯の自動切り取り、1080p への縮小
- 音声はそのまま / Opus に変換。元の音声・字幕・チャプターはすべて引き継ぎ、出来上がったファイルで欠けていないか確かめます（入れられなかったトラックがあれば「完了（注意あり）」として知らせます）
- HDR10 / HLG の色情報とマスタリング情報を引き継ぎます
- デスクトップ版（Qt）と Web 画面（ブラウザから操作。VM・サーバー向け）
- Windows / Linux。**Python も入っていない状態から全自動で環境を作ります**

## インストール

### Windows

1. このフォルダを好きな場所に置いてください（`C:\tools\av1an_gui` など。Program Files は不可です）
2. `install.bat` を実行すると、画面付きのインストーラーが開きます
   - Python（uv で取得し、このフォルダ内に置く）、ライブラリ、FFmpeg、MKVToolNix、av1an、各エンコーダを自動で取得します
   - 既に入っているものは入れ直しません。PC に Python 3.12〜3.13 があればそれを使い、FFmpeg などのツールも PATH にあればそれを使います。取り直すときは「変換ツールを取り直す」にチェックを入れます
   - 管理者権限は不要です。Web 画面のファイアウォール許可のときだけ確認（UAC）が 1 回出ます
   - コンソールで入れる場合は `install_cli.bat` を使います（`-NoWeb` / `-Port 9000` / `-Force`）
3. デスクトップの `av1an_gui` から起動します

### Linux（Debian / Ubuntu / Fedora 系）

```
./install.sh            # デスクトップ版 + Web 画面
./install.sh --no-gui   # 画面のないサーバー（Web 画面だけ）
./install.sh --nvidia   # NVIDIA GPU のドライバも入れる
```

- apt / dnf で MKVToolNix・Docker・Qt の依存ライブラリを入れます（sudo を使います）
- av1an とエンコーダは Docker イメージ `masterofzen/av1an:master` を使います
- Web 画面は systemd のサービスとして常駐します

### Proxmox などの VM で多コアを使う場合

- CPU の種類は `host` にしてください（既定の `x86-64-v2-AES` だと AVX2 が使えず、エンコーダが大幅に遅くなります）
- 2 CPU のホストでは NUMA を有効にし、ソケット数をホストに合わせてください
- Windows で 64 を超える論理プロセッサでも、エンコーダは別々のプロセスで動くため全コアを使えます

## 使い方

- **デスクトップ版**: ファイルやフォルダをドロップし、エンコーダ・画質・速度を選んで「エンコード開始」を押します。各項目の ⓘ にマウスを乗せると説明が出ます
- **Web 画面**: `http://<この PC の IP>:8765/`。キュー（ファイル追加も同じ画面）/ 設定 / 環境 の 3 画面です。**認証はないため、LAN 内だけで使ってください**
- **キューは共通です**: キューと進捗は Web 画面のサーバーが持ち、デスクトップ版と Web 画面のどちらにも同じものが出ます。デスクトップ版で追加したファイルも、追加した時点でキューに入ります。サーバーが動いていなければデスクトップ版が起動します。デスクトップ版を閉じてもエンコードは続きます
  - キュー全体に「開始 / 停止」があり、どちらの画面でも「エンコード開始」で上から順に処理します。全部終わると停止に戻ります。「停止」で止めたものは待機に戻り、次に開始すると続きから再開します。始まる前なら、デスクトップ版で種類・解像度などを変えられます
  - キューは `logs/queue.json` に保存するので、サーバーを起動し直しても残ります
  - プログラムを更新した後、サーバーが古いプログラムのまま動いていれば、デスクトップ版がエンコード中でないときに自動で起動し直します
  - 手動で起動: `web.bat`（Windows）/ `.venv/bin/python -m shared.web`（Linux）。自動起動時のログは `logs/web.log` です
- **設定**: デスクトップ版の「設定」にタブでまとめています（一般 / 追加時の既定 / 分割 / Web 画面 / 外部ツール）。Web 画面の「設定」タブにも同じ項目が出ます
  - **Web 画面**タブ: 他の PC のブラウザから使うか（LAN に公開）・ポート・ログオン時の自動起動を変えられます。保存すると Web サーバーを起動し直します（エンコード中は変えられません）。Web 画面のブラウザからは変えられません（管理者の確認などがこの PC の画面で必要なため）
  - **分割**: 通常は「自動」のままで大丈夫です。1 つ最大 10 秒を基本に、短い映像では並列数が埋まるよう短くし、最小の長さは 1 秒分のフレーム数にします
- **終わったら**: キューがすべて終わった後に、スリープ・休止状態・シャットダウンができます（デスクトップ版の下、Web 画面のキューの上で選びます。両方で共通です）。実行の 60 秒前から取り消せます。停止ボタン（すべて中止）で止めた場合や、最後のファイルを中止した場合は実行しません
- **更新**: 新しい版が出ていると、デスクトップ版の見出しに「更新 x.y.z」が出ます（開いたときと 1 日 1 回確認します）。押すと新しい版を取ってきて入れ替え、終わると自動で開き直します。Web 画面の「環境」タブからも更新できます
  - 設定（`settings*.json`）・キュー・ログ・取得した外部ツール・Python の環境はそのまま残ります。ライブラリは `requirements.txt` が変わったときだけ入れ直します
  - エンコード中は更新できません。途中で失敗したら元の版に戻します。経過は `logs/update.log` に残ります
  - Linux で `install.sh` が変わった版では、必要に応じて `sudo ./install.sh` を実行してください
- **コマンドライン**:

```
python -m av1gui check
python -m av1gui encode FILE_OR_DIR... [--encoder svt-av1|aom|rav1e|x265|x264] [--quality high|standard|small|N]
                                       [--speed slow|medium|fast] [--content film|anime|standard]
                                       [--res keep|1080p] [--crop auto|none] [--audio copy|opus]
                                       [--workers N] [--threads N] [--target-vmaf 95] [--extra "..."] [--out DIR]
```

## 種類ごとの設定

| エンコーダ | 実写 | アニメ |
|---|---|---|
| SVT-AV1 | `--tune 0 --enable-qm 1 --qm-min 0 --enable-variance-boost 1 --film-grain 8 --ac-bias 1.0` | `--tune 0 --enable-qm 1 --qm-min 0 --enable-variance-boost 1 --variance-boost-strength 3 --film-grain 0` |
| aomenc | `--tune-content=film --enable-qm=1` + 粒子の合成（photon noise 8） | `--enable-qm=1` |
| rav1e | 粒子の合成（photon noise 8） | — |
| x265 | `--aq-mode 3 --psy-rd 2 --psy-rdoq 1.5 --no-sao --deblock -1:-1 --no-strong-intra-smoothing --bframes 6 --rc-lookahead 40` | `--bframes 8 --psy-rd 1 --psy-rdoq 1 --aq-mode 3 --aq-strength 0.8 --deblock 1:1 --rc-lookahead 60` |
| x264 | `--tune film --aq-mode 3` | `--tune animation --aq-mode 3` |

- 「標準」は調整しません。画質（CRF など）と速度（preset など）はエンコーダごとの尺度に変換して渡します
- SVT-AV1 は版によって使えるオプションが違うため、その版の `--help` にないオプションは自動で外します
- 派生版（SVT-AV1-PSY など）を使うときは「エンコーダの場所」に実行ファイルを指定してください（ファイル名は標準と同じにしておいてください）
- 値は `av1gui/encoders.py` と `shared/core/tuning.py` にまとめてあります

## 並列数の決め方

「CPU 数 ÷ 1 本あたりのスレッド数」と「空きメモリ × 0.9 ÷ 1 本あたりのメモリ」の小さい方です。余ったコアは各エンコーダのスレッドに回します。

| エンコーダ | 4K（スレッド / メモリ） | 1080p 以下 |
|---|---|---|
| SVT-AV1 | 8 / 7 GB | 4 / 2.5 GB |
| x265 | 4 / 3.5 GB | 3 / 1.5 GB |
| aomenc・rav1e | 4 / 3 GB | 3 / 1.2 GB |
| x264 | 4 / 2 GB | 3 / 0.8 GB |

例: SVT-AV1 で 4K、64 コア・空きメモリ 90 GB → 8 本 × 8 スレッド。並列数・スレッド数は手動でも指定できます。

### 並列数アシスト

画面の「アシスト…」（Web 画面は設定タブ）から、主に扱う解像度（4K / 1080p 以下 / 両方）、CPU の使い方（全部 / 1 割残す / 半分）、OS 用に残すメモリを答えると、エンコーダごと・解像度ごとに並列数 × スレッド数を決めて保存します。並列数が 0（自動）のとき、入力の横幅 3200 以上なら 4K 用、それ未満なら 1080p 用の値を使います。優先順位は 手動指定 → アシスト → 自動 です。

## リリースの作り方（開発者向け）

1. フォルダ直下の `VERSION` を新しい番号にします（例 `0.4.1`）
2. `v0.4.1` のタグを付けて GitHub のリリースを作ります（ZIP はリリースに付けても、付けなくても構いません。付けない場合は GitHub が作るソースの ZIP を使います）
3. 更新元は `av1gui/app.json` の `"update": {"github": "owner/repo"}` で決まります



`uninstall.bat`（Windows）/ `./uninstall.sh`（Linux、`--docker` で av1an のイメージも削除）。
アプリのフォルダ（Python・取得したツール・設定を含む）、ショートカット、Web 画面の自動起動とファイアウォール規則、作業フォルダを消します。エンコード済みの動画は残します。

## 構成（開発者向け）

```
shared/        共通部分（外部ツールの取得・解析・実行・設定画面・Web 画面・セットアップ）
  core/        tools（取得と検出）/ av1an / probe / runner / video / tuning / config
  gui/         テーマ・設定画面（タブ・設定項目の定義から作るページ）
  web/         Web 画面（FastAPI + 静的ページ）
  apps.ps1     インストーラー用のツール判定
av1gui/        av1an_gui 本体（app.json / env.py / webapp.py を持つ）
install*.ps1 / install.sh / uninstall* / web_firewall*   インストーラー類
```

`shared` はフォルダ直下の `app.json` を持つパッケージを「ツール」として扱います。ツールは `env.py`（使う外部ツールの一覧）と `webapp.py`（Web 画面への登録）を持てば、インストーラー・設定画面・Web 画面にそのまま載ります。設定項目は `settings_sections`（見出し付きの定義）で渡すと、デスクトップ版の設定画面と Web 画面の設定タブが同じ定義から作られます。

## 使っている外部ツール

インストール時に、それぞれの公式の配布元から取得します（このリポジトリには含みません）。

| ツール | ライセンス | 取得元 |
|---|---|---|
| [av1an](https://github.com/rust-av/Av1an) | GPL-3.0 | GitHub Releases（Windows）/ Docker イメージ（Linux） |
| [SVT-AV1](https://gitlab.com/AOMediaCodec/SVT-AV1) | BSD-3-Clause-Clear | MSYS2 |
| [libaom（aomenc）](https://aomedia.googlesource.com/aom/) | BSD-2-Clause | MSYS2 |
| [rav1e](https://github.com/xiph/rav1e) | BSD-2-Clause | MSYS2 |
| [x265](https://bitbucket.org/multicoreware/x265_git) | GPL-2.0 | MSYS2 |
| [x264](https://www.videolan.org/developers/x264.html) | GPL-2.0 | VideoLAN |
| [FFmpeg](https://ffmpeg.org/) | GPL（BtbN のビルド） | [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) |
| [MKVToolNix](https://mkvtoolnix.download/) | GPL-2.0 | 公式 / パッケージ |
| [VapourSynth](https://www.vapoursynth.com/) | LGPL-2.1 | PyPI |
| [uv](https://github.com/astral-sh/uv) | MIT / Apache-2.0 | GitHub Releases |
