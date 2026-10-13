# av1an_gui

[av1an](https://github.com/rust-av/Av1an) を画面で操作する、分割並列エンコードのツールです。
映像を場面ごとに細かく分け、エンコーダを CPU のコア数に合わせて何本も同時に動かします。8 コアの PC でも、128 コア以上のサーバーでも、並列数は自動で決まります。

- エンコーダ: **SVT-AV1** / aomenc / rav1e（AV1）、x265（HEVC）、x264（H.264）
- 実写・アニメ向けの設定を同梱（ファイルごとに切り替え。追加パラメータで上書きも可能）
- 並列数とスレッド数を CPU 数・空きメモリから自動決定（上限なし）
- 目標画質（VMAF）モード、途中で止めても続きから再開
- 黒帯の自動切り取り、1080p への縮小
- 音声はそのまま / Opus に変換 / 外す。字幕とチャプターは引き継ぎます
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
  - 手動で起動: `web.bat`（Windows）/ `.venv/bin/python -m shared.web`（Linux）。自動起動時のログは `logs/web.log` です
- **コマンドライン**:

```
python -m av1gui check
python -m av1gui encode FILE_OR_DIR... [--encoder svt-av1|aom|rav1e|x265|x264] [--quality high|standard|small|N]
                                       [--speed slow|medium|fast] [--content film|anime|standard]
                                       [--res keep|1080p] [--crop auto|none] [--audio copy|opus|none]
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

## アンインストール

`uninstall.bat`（Windows）/ `./uninstall.sh`（Linux、`--docker` で av1an のイメージも削除）。
アプリのフォルダ（Python・取得したツール・設定を含む）、ショートカット、Web 画面の自動起動とファイアウォール規則、作業フォルダを消します。エンコード済みの動画は残します。

## 構成（開発者向け）

```
shared/        共通部分（外部ツールの取得・解析・実行・設定画面・Web 画面・セットアップ）
  core/        tools（取得と検出）/ av1an / probe / runner / video / tuning / config
  gui/         テーマ・環境設定ダイアログ
  web/         Web 画面（FastAPI + 静的ページ）
  apps.ps1     インストーラー用のツール判定
av1gui/        av1an_gui 本体（app.json / env.py / webapp.py を持つ）
install*.ps1 / install.sh / uninstall* / web_firewall*   インストーラー類
```

`shared` はフォルダ直下の `app.json` を持つパッケージを「ツール」として扱います。ツールは `env.py`（使う外部ツールの一覧）と `webapp.py`（Web 画面への登録）を持てば、インストーラー・環境設定・Web 画面にそのまま載ります。

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
