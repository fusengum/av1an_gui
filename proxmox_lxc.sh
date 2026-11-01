#!/usr/bin/env bash
# Proxmox VE のホストで root で実行します。av1an_gui 用の LXC を 1 つ作り、その中に Web 画面だけの構成で入れます
# LXC はホストのカーネルをそのまま使うので、VM と違って仮想化による損失がありません。コア数は制限せず、全スレッドを見せます
# ホストに足すのは、新しい LXC と（無ければ）Ubuntu 24.04 のテンプレートだけです。既にある VM・LXC・設定には触れません
#   ./proxmox_lxc.sh [--id N] [--name av1an-gui] [--memory GB] [--disk GB] [--storage NAME] [--cores N]
#                    [--bridge vmbr0] [--ip dhcp|CIDR --gw IP] [--mount HOST_DIR[:CT_DIR][:ro]]...
#                    [--port 8765] [--version TAG] [--src DIR]
set -euo pipefail
REPO="fusengum/av1an_gui"
CTID=""; NAME="av1an-gui"; MEM=""; DISK=64; STORAGE=""; CORES=""; BRIDGE="vmbr0"; IP="dhcp"; GW=""
PORT=8765; VERSION=""; SRC=""; MOUNTS=()
usage() { sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
while [ $# -gt 0 ]; do case "$1" in
  --id) CTID="$2"; shift;; --name) NAME="$2"; shift;; --memory) MEM="$2"; shift;; --disk) DISK="$2"; shift;;
  --storage) STORAGE="$2"; shift;; --cores) CORES="$2"; shift;; --bridge) BRIDGE="$2"; shift;;
  --ip) IP="$2"; shift;; --gw) GW="$2"; shift;; --mount) MOUNTS+=("$2"); shift;; --port) PORT="$2"; shift;;
  --version) VERSION="$2"; shift;; --src) SRC="$2"; shift;; -h|--help) usage;;
  *) echo "不明なオプション: $1"; usage 1;; esac; shift; done
step() { printf '\n\033[36m=== %s\033[0m\n' "$1"; }
die() { echo "エラー: $*" >&2; exit 1; }

command -v pct >/dev/null && command -v pveam >/dev/null || die "Proxmox VE のホストで実行してください（pct がありません）"
[ "$(id -u)" -eq 0 ] || die "root で実行してください"
[ -z "$SRC" ] || [ -f "$SRC/install.sh" ] || die "--src に av1an_gui のフォルダを指定してください（install.sh がありません）"

# 1. 決めておくもの（指定が無ければホストに合わせる）
[ -n "$CTID" ] || CTID=$(pvesh get /cluster/nextid)
pct status "$CTID" >/dev/null 2>&1 && die "ID $CTID はもう使われています（--id で別の番号を指定してください）"
if [ -z "$MEM" ]; then
  # 並列数は LXC のメモリで決まるので、いま空いているメモリの 8 割を渡す（少なくとも 8 GB）
  avail=$(awk '/MemAvailable/ {print int($2 / 1048576)}' /proc/meminfo)
  MEM=$(( avail * 8 / 10 )); [ "$MEM" -ge 8 ] || MEM=8
fi
[ -n "$STORAGE" ] || STORAGE=$(pvesm status --content rootdir 2>/dev/null | awk 'NR > 1 && $3 == "active" {print $1; exit}')
[ -n "$STORAGE" ] || die "LXC を置けるストレージが見つかりません（--storage で指定してください）"
TSTORE=$(pvesm status --content vztmpl 2>/dev/null | awk 'NR > 1 && $3 == "active" {print $1; exit}')
[ -n "$TSTORE" ] || die "テンプレートを置けるストレージが見つかりません"

# 2. テンプレート（Ubuntu 24.04。入っていれば取り直さない）
step "テンプレートを確認"
TPL=$(pveam list "$TSTORE" 2>/dev/null | awk '{print $1}' | grep -E 'ubuntu-24\.04-standard_.*_amd64' | sort -V | tail -1 || true)
if [ -z "$TPL" ]; then
  pveam update >/dev/null
  NAME_T=$(pveam available --section system | awk '{print $2}' | grep -E '^ubuntu-24\.04-standard_.*_amd64' | sort -V | tail -1)
  [ -n "$NAME_T" ] || die "Ubuntu 24.04 のテンプレートが見つかりません"
  echo "取得します: $NAME_T"
  pveam download "$TSTORE" "$NAME_T"
  TPL="$TSTORE:vztmpl/$NAME_T"
fi
echo "$TPL"

# 3. LXC を作る。Docker（av1an の実行に使う）を中で動かすため nesting と keyctl を有効にする
step "LXC $CTID（$NAME）を作成"
NET="name=eth0,bridge=$BRIDGE,type=veth,ip=$IP"
[ -n "$GW" ] && NET="$NET,gw=$GW"
ARGS=(--hostname "$NAME" --unprivileged 1 --features nesting=1,keyctl=1 --memory $(( MEM * 1024 )) --swap 4096
      --rootfs "$STORAGE:$DISK" --net0 "$NET" --onboot 1 --description "av1an_gui（proxmox_lxc.sh で作成）")
[ -n "$CORES" ] && ARGS+=(--cores "$CORES")
i=0; SHOWN=()
for m in "${MOUNTS[@]}"; do
  IFS=: read -r hdir cdir ro <<<"$m"
  [ "$cdir" = ro ] && { cdir=""; ro=ro; }
  [ -d "$hdir" ] || die "フォルダがありません: $hdir"
  [ -n "$cdir" ] || cdir="/mnt/$(basename "$hdir")"
  opt="$hdir,mp=$cdir"; [ "$ro" = ro ] && opt="$opt,ro=1"
  ARGS+=("--mp$i" "$opt"); i=$((i + 1)); SHOWN+=("$hdir → $cdir${ro:+（読み取り専用）}")
done
pct create "$CTID" "$TPL" "${ARGS[@]}"
pct start "$CTID"
ct() { pct exec "$CTID" -- env LANG=C.UTF-8 "$@"; }
printf 'ネットワークを待っています'
for _ in $(seq 1 60); do ct getent hosts github.com >/dev/null 2>&1 && break; printf '.'; sleep 2; done; echo
ct getent hosts github.com >/dev/null 2>&1 || die "LXC からインターネットに繋がりません（--ip / --gw / --bridge を確かめてください）"

# 4. av1an_gui を入れる（既定は GitHub のリリース。--src なら手元のフォルダ）
APP=/opt/av1an_gui
step "av1an_gui を配置（$APP）"
ct mkdir -p "$APP"
if [ -n "$SRC" ]; then
  tar -C "$SRC" --exclude=./.git --exclude=./.venv --exclude=./tools --exclude=./logs --exclude='./settings*.json' \
      --exclude='__pycache__' -cf - . | ct tar -xf - -C "$APP"
else
  [ -n "$VERSION" ] || VERSION=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
                                 | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)
  [ -n "$VERSION" ] || die "最新のリリースが分かりません（--version で指定してください）"
  echo "$REPO $VERSION"
  curl -fsSL "https://github.com/$REPO/archive/refs/tags/$VERSION.tar.gz" | ct tar -xzf - -C "$APP" --strip-components=1
fi

step "LXC の中でセットアップ（install.sh --no-gui）"
ct bash "$APP/install.sh" --no-gui --port "$PORT"

ADDR=$(ct hostname -I | awk '{print $1}')
printf '\n\033[32m完了しました。Web 画面: http://%s:%s\033[0m\n' "$ADDR" "$PORT"
echo "LXC $CTID（$NAME）: メモリ ${MEM} GB・コア ${CORES:-制限なし}・ディスク ${DISK} GB（$STORAGE）"
for s in "${SHOWN[@]}"; do echo "フォルダ: $s"; done
[ ${#SHOWN[@]} -eq 0 ] || echo "（Web 画面では → の右側の、LXC の中のパスで指定します）"
echo "消すとき: pct stop $CTID && pct destroy $CTID"
