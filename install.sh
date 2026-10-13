#!/usr/bin/env bash
# 全自動セットアップ（Linux: Debian / Ubuntu / Fedora 系）— このフォルダに入っているツールが対象
# Python が無い状態から、uv・Python・ライブラリ・外部ツール・Docker（av1an）・Web 画面の常駐まで構築する
# 既に入っているもの（PATH にあるコマンド・条件を満たす Python やライブラリ）は入れ直さない。--force で取り直す
#   ./install.sh [--folder KEY=DIR] [--no-web] [--port 8765] [--no-gui] [--nvidia] [--force]
set -euo pipefail
APP="$(cd "$(dirname "$0")" && pwd)"
TOOLS="$APP/tools"
PORT=8765; WEB=1; GUI=1; NVIDIA=0; FORCE=""; FOLDERS=()
while [ $# -gt 0 ]; do case "$1" in
  --no-web) WEB=0;; --no-gui) GUI=0;; --nvidia) NVIDIA=1;; --force) FORCE="--force";;
  --port) PORT="$2"; shift;; --folder) FOLDERS+=("$2"); shift;; *) echo "不明なオプション: $1"; exit 1;; esac; shift; done
step() { printf '\n\033[36m=== %s\033[0m\n' "$1"; }
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null || { echo "sudo がありません。root で実行するか sudo を導入してください（Debian: su -c 'apt install sudo'）"; exit 1; }
  SUDO="sudo"
fi
RUN_USER="${SUDO_USER:-$(id -un)}"

# 1. OS パッケージ（入っていないものだけ入れる。コマンドが PATH にあれば、そのパッケージは入れない）
step "OS パッケージを確認"
need_cmd() { command -v "$1" >/dev/null 2>&1; }
MISSING=()
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  has() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"; }
  need_cmd curl || MISSING+=(curl); has ca-certificates || MISSING+=(ca-certificates)
  need_cmd xz || MISSING+=(xz-utils); need_cmd tar || MISSING+=(tar); need_cmd lspci || MISSING+=(pciutils)
  need_cmd mkvmerge || MISSING+=(mkvtoolnix)
  need_cmd docker || MISSING+=(docker.io)   # Docker CE 導入済みなら触らない
  if [ $GUI -eq 1 ]; then
    for p in libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libfontconfig1 libdbus-1-3; do
      has "$p" || MISSING+=("$p"); done
  fi
  if [ ${#MISSING[@]} -gt 0 ]; then
    echo "導入します: ${MISSING[*]}"
    $SUDO apt-get update -q
    $SUDO apt-get install -y -q "${MISSING[@]}"
  else echo "すべて導入済みです"; fi
elif command -v dnf >/dev/null; then
  has() { rpm -q "$1" >/dev/null 2>&1; }
  need_cmd curl || MISSING+=(curl); has ca-certificates || MISSING+=(ca-certificates)
  need_cmd xz || MISSING+=(xz); need_cmd tar || MISSING+=(tar); need_cmd lspci || MISSING+=(pciutils)
  need_cmd mkvmerge || MISSING+=(mkvtoolnix)
  need_cmd docker || MISSING+=(moby-engine)
  if [ $GUI -eq 1 ]; then
    for p in mesa-libGL mesa-libEGL libxkbcommon-x11 xcb-util-cursor fontconfig dbus-libs; do has "$p" || MISSING+=("$p"); done
  fi
  if [ ${#MISSING[@]} -gt 0 ]; then echo "導入します: ${MISSING[*]}"; $SUDO dnf install -y "${MISSING[@]}"
  else echo "すべて導入済みです"; fi
else
  echo "apt / dnf がありません。curl, mkvtoolnix, docker を手動で導入してから再実行してください"; fi

# 2. Docker（av1an の実行に使う）。イメージが既にあれば取り直さない（--force で更新）
step "Docker を準備"
if ! $SUDO docker info >/dev/null 2>&1; then
  $SUDO systemctl enable --now docker >/dev/null 2>&1 || true
fi
id -nG "$RUN_USER" | grep -qw docker || $SUDO usermod -aG docker "$RUN_USER" || true
if ! $SUDO docker info >/dev/null 2>&1; then
  echo "Docker が起動していません。並列エンコードは使えず、通常の 1 本エンコードになります"
elif $SUDO docker image inspect masterofzen/av1an:master >/dev/null 2>&1 && [ -z "$FORCE" ]; then
  echo "av1an イメージ: 導入済み（更新するには --force を付けます）"
else
  $SUDO docker pull masterofzen/av1an:master || echo "av1an イメージの取得に失敗しました（後で環境設定から取得できます）"
fi

# 3. NVIDIA（任意）
if lspci 2>/dev/null | grep -qi nvidia; then
  if command -v nvidia-smi >/dev/null; then echo "NVIDIA GPU: ドライバ導入済み（NVENC が使えます）"
  elif [ $NVIDIA -eq 1 ] && command -v ubuntu-drivers >/dev/null; then
    step "NVIDIA ドライバを導入"; $SUDO ubuntu-drivers autoinstall; echo "再起動後に NVENC が使えるようになります"
  else echo "NVIDIA GPU を検出しました。NVENC を使うには --nvidia を付けて再実行するか、ドライバを導入してください"; fi
fi

# 4. uv と Python
#    uv はこのフォルダ内 → PATH の順に探し、無ければこのフォルダ内に取得する
#    Python は既存の仮想環境が使えればそのまま。無ければ PC の Python（3.12 以上 3.14 未満）を使い、それも無いときだけ取得する
export UV_PYTHON_INSTALL_DIR="$TOOLS/python" UV_CACHE_DIR="$TOOLS/uv-cache"
PY_MIN=3.12; PY_REQ=">=3.12,<3.14"
if [ -x "$TOOLS/uv/uv" ]; then UV="$TOOLS/uv/uv"
elif command -v uv >/dev/null && [ -z "$FORCE" ]; then UV="$(command -v uv)"; echo "uv: $UV（導入済み）"
else
  step "uv を取得"
  mkdir -p "$TOOLS/uv"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$TOOLS/uv" UV_NO_MODIFY_PATH=1 sh
  UV="$TOOLS/uv/uv"
fi
PY="$APP/.venv/bin/python"
py_ok() { [ -x "$1" ] && "$1" -c 'import sys; sys.exit(0 if (3, 12) <= sys.version_info[:2] < (3, 14) else 1)' 2>/dev/null; }
new_venv() { rm -rf "$APP/.venv"; "$UV" venv --python "$1" "$APP/.venv"; }
step "Python を準備"
if py_ok "$PY" && [ -z "$FORCE" ]; then
  echo "Python $("$PY" -c 'import platform; print(platform.python_version())')（導入済み）"
else
  FOUND="$("$UV" python find --system "$PY_REQ" 2>/dev/null | head -n1 || true)"
  if [ -n "$FOUND" ] && py_ok "$FOUND"; then
    echo "PC に入っている Python を使います: $FOUND"; new_venv "$FOUND"
  else
    "$UV" python install "$PY_MIN"; new_venv "$PY_MIN"
  fi
fi
step "ライブラリを確認"
REQS=(-r "$APP/requirements.txt")
for r in "$APP"/*/requirements.txt; do [ -f "$r" ] && [ -f "$(dirname "$r")/app.json" ] && REQS+=(-r "$r"); done
if "$UV" pip install --dry-run --python "$PY" "${REQS[@]}" 2>&1 | grep -q "Would make no changes"; then
  echo "すべて導入済みです"
elif ! "$UV" pip install --python "$PY" "${REQS[@]}"; then
  # PC の Python の版に合う配布物が無い場合などは、決まった版の Python で作り直してもう一度試す
  CUR="$("$PY" -c 'import platform; print(platform.python_version())')"
  case "$CUR" in "$PY_MIN".*) exit 1;; esac
  echo "Python $CUR ではライブラリを入れられませんでした。Python $PY_MIN で作り直します"
  "$UV" python install "$PY_MIN"; new_venv "$PY_MIN"
  "$UV" pip install --python "$PY" "${REQS[@]}"
fi

# 5. 外部ツール
step "外部ツールを導入"
# 手動で指定するフォルダ（自動取得できない外部ツールの場所など。必要なツールが入っている場合だけ）
ARGS=()
for f in ${FOLDERS[@]+"${FOLDERS[@]}"}; do ARGS+=(--folder "$f"); done
if [ -t 0 ]; then
  while IFS=$'\t' read -r KEY FOUND TITLE; do
    [ -n "$KEY" ] || continue
    printf '%s\n' "${FOLDERS[@]+"${FOLDERS[@]}"}" | grep -q "^$KEY=" && continue
    [ -n "$FOUND" ] && continue
    read -r -p "$TITLE のパス（後で設定するなら空のまま Enter）: " V </dev/tty
    [ -n "$V" ] && ARGS+=(--folder "$KEY=$V")
  done < <(cd "$APP" && "$PY" -m shared folders)
fi
( cd "$APP" && "$PY" -m shared setup ${ARGS[@]+"${ARGS[@]}"} $FORCE ) || true

# 6. 所有者をそろえる（sudo で実行した場合）
[ -n "${SUDO_USER:-}" ] && chown -R "$SUDO_USER": "$APP"

# 入っているツール（各ツールの app.json）
APPS=(); NAMES=()
while IFS=$'\t' read -r P N; do APPS+=("$P"); NAMES+=("$N"); done < <(cd "$APP" && "$PY" -m shared apps)
SUITE=$(IFS=/; echo "${NAMES[*]}")
WEBSVC=$(cd "$APP" && "$PY" -c "from shared import app_infos as a; i=a(); w=[x.get('web_name') for x in i if x.get('web_name')]; print((w[0] if w else (i[0]['name']+'_web' if i else 'tools_web')).replace('_', '-'))")

# 7. Web 画面（systemd で常駐）
if [ $WEB -eq 1 ] && command -v systemctl >/dev/null; then
  step "Web 画面を登録（ポート $PORT）"
  # 待ち受けは settings_web.json に書き、サーバーは引数なしで起動する（デスクトップ版の設定から変えられるように）
  printf '{"lan": true, "port": %s, "autostart": true}\n' "$PORT" > "$APP/settings_web.json"
  [ -n "${SUDO_USER:-}" ] && chown "$SUDO_USER": "$APP/settings_web.json"
  $SUDO tee /etc/systemd/system/$WEBSVC.service >/dev/null <<UNIT
[Unit]
Description=$SUITE Web UI
After=network-online.target docker.service

[Service]
User=$RUN_USER
SupplementaryGroups=docker
WorkingDirectory=$APP
ExecStart=$PY -m shared.web
# 設定を変えたときはサーバーが異常終了扱いで終わり、ここで起動し直される
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now "$WEBSVC"
  command -v ufw >/dev/null && $SUDO ufw allow "$PORT/tcp" >/dev/null 2>&1 || true
  command -v firewall-cmd >/dev/null && { $SUDO firewall-cmd --permanent --add-port="$PORT/tcp" && $SUDO firewall-cmd --reload; } >/dev/null 2>&1 || true
  IP=$(hostname -I 2>/dev/null | awk '{print $1}')
  printf '\033[32mWeb 画面: http://%s:%s\033[0m\n' "$IP" "$PORT"
fi

# 8. デスクトップ版のランチャー
if [ $GUI -eq 1 ]; then
  HOME_DIR=$(getent passwd "$RUN_USER" | cut -d: -f6)
  D="$HOME_DIR/.local/share/applications"; mkdir -p "$D"
  for i in "${!APPS[@]}"; do
    a=${APPS[$i]}; n=${NAMES[$i]}
    cat > "$D/$n.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=$n
Exec=$PY -m $a
Path=$APP
Icon=$APP/$a/assets/icon.png
Terminal=false
Categories=AudioVideo;
DESK
  done
  [ -n "${SUDO_USER:-}" ] && chown -R "$SUDO_USER": "$D"
fi

step "環境チェック"
( cd "$APP" && "$PY" -m shared check ) || true
echo
echo "セットアップ完了（$SUITE）。Docker を使うため、一度ログインし直すと av1an がユーザー権限で使えるようになります。"
( cd "$APP" && "$PY" -m shared folders ) | while IFS=$'\t' read -r KEY FOUND TITLE; do
  if [ -n "$FOUND" ]; then echo "$TITLE: $FOUND"; else echo "$TITLE は未設定です。./install.sh --folder $KEY=<フォルダ> で再実行するか、アプリの環境設定で指定してください。"; fi
done
