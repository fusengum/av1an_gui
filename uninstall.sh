#!/usr/bin/env bash
# アンインストーラー（このフォルダに入っているツール一式。ツールが使う外部のフォルダには触れない）
#   ./uninstall.sh            確認してから削除
#   ./uninstall.sh -y         確認なし
#   ./uninstall.sh -y --docker  av1an の Docker イメージも削除
set -e
APP="$(cd "$(dirname "$0")" && pwd)"
[ -f "$APP/shared/__init__.py" ] || { echo "このツールのフォルダではありません: $APP"; exit 1; }
YES=0; DOCKER=0
for a in "$@"; do case "$a" in -y) YES=1;; --docker) DOCKER=1;; esac; done
SUDO=""; [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null && SUDO="sudo"
RUN_USER="${SUDO_USER:-$(id -un)}"
HOME_DIR=$(getent passwd "$RUN_USER" | cut -d: -f6)
PY="$APP/.venv/bin/python"; [ -x "$PY" ] || PY=python3

# 入っているツールの情報（app.json）
INFO=$(cd "$APP" && "$PY" - <<'PYCODE' 2>/dev/null || true
import json, os
from pathlib import Path
from shared import app_infos
i = app_infos()
web = next((x["web_name"] for x in i if x.get("web_name")), (i[0]["name"] + "_web") if i else "tools_web")
print("WEB\t" + web.replace("_", "-"))
for x in i:
    print("APP\t" + x["pkg"] + "\t" + x["name"])
    for t in x.get("temp_dirs", []):
        print("TMP\t" + t)
    for fd in x.get("folders", []):
        f = fd.get("file_linux", fd.get("file", ""))
        if f and (Path(".") / f).exists():
            print("DANGER\t" + fd.get("title", fd["key"]))
PYCODE
)
WEBSVC=$(printf '%s\n' "$INFO" | awk -F'\t' '$1=="WEB"{print $2}')
[ -n "$WEBSVC" ] || WEBSVC=tools-web
if printf '%s\n' "$INFO" | grep -q '^DANGER'; then
  echo "このフォルダは外部ツールのフォルダでもあります。消すと困るため中止します。"; exit 1
fi

# 作業フォルダ: EncodeTemp などの中の、このツールが作ったもの（<パッケージ>_<番号>）だけを消す。
# EncodeTemp など自体は、空になったときだけ消す（利用者が同じ名前のフォルダを使っていても中身には触れない）
PKGS=$(printf '%s\n' "$INFO" | awk -F'\t' '$1=="APP"{print $2}' | paste -sd'|' -)
TMPP=(); TMPS=()
for n in EncodeTemp encode_tools $(printf '%s\n' "$INFO" | awk -F'\t' '$1=="TMP"{print $2}'); do
  for d in "/$n" "${TMPDIR:-/tmp}/$n"; do [ -d "$d" ] && TMPP+=("$d"); done
done
for d in ${TMPP[@]+"${TMPP[@]}"}; do
  [ -n "$PKGS" ] || break
  while IFS= read -r x; do TMPS+=("$x"); done < <(find "$d" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended \
    -regex ".*/\.?($PKGS)(_av1an)?_[0-9a-f]{8,12}" 2>/dev/null)
done
DESKS=()
while IFS=$'\t' read -r K P N; do
  [ "$K" = APP ] || continue
  # このフォルダを起動するランチャーだけ（同じ名前の別のものは消さない）
  f="$HOME_DIR/.local/share/applications/$N.desktop"; [ -f "$f" ] && grep -qF "$APP/" "$f" && DESKS+=("$f")
done <<< "$INFO"
SVC=0; [ -f "/etc/systemd/system/$WEBSVC.service" ] && SVC=1

echo "削除するもの:"
echo "  - アプリ本体・Python・自動取得ツール・設定: $APP"
[ $SVC -eq 1 ] && echo "  - Web 画面のサービス: $WEBSVC.service"
for f in ${DESKS[@]+"${DESKS[@]}"}; do echo "  - ランチャー: $f"; done
for f in ${TMPS[@]+"${TMPS[@]}"}; do echo "  - 作業フォルダ: $f"; done
[ $DOCKER -eq 1 ] && echo "  - Docker イメージ: masterofzen/av1an:master"
echo "残すもの: 変換済みの動画、apt/dnf で入れたパッケージ、手動で指定した外部ツール"
if [ $YES -ne 1 ]; then read -r -p "アンインストールしますか？ (y/N) " a; [[ "$a" =~ ^[yY] ]] || exit 0; fi

if [ $SVC -eq 1 ]; then
  $SUDO systemctl disable --now "$WEBSVC" 2>/dev/null || true
  $SUDO rm -f "/etc/systemd/system/$WEBSVC.service"
  $SUDO systemctl daemon-reload || true
fi
# ファイアウォール: インストーラーが足したと記録したものだけを消す
if [ -f "$APP/tools/firewall_added" ]; then
  while read -r KIND P; do
    case "$KIND" in
      ufw) command -v ufw >/dev/null && $SUDO ufw delete allow "$P/tcp" >/dev/null 2>&1 || true;;
      firewalld) command -v firewall-cmd >/dev/null && { $SUDO firewall-cmd --permanent --remove-port="$P/tcp" && $SUDO firewall-cmd --reload; } >/dev/null 2>&1 || true;;
    esac
  done < "$APP/tools/firewall_added"
fi

pkill -f "$APP/.venv/bin/python" 2>/dev/null || true
# av1an のコンテナ（エンコード途中のもの）
if command -v docker >/dev/null; then
  # このフォルダが起動したコンテナだけ（目印のラベルで見分ける。利用者自身の av1an のコンテナは止めない）
  ids=$($SUDO docker ps -q --filter "label=av1an_gui.root=$APP" 2>/dev/null || true)
  [ -n "$ids" ] && $SUDO docker rm -f $ids >/dev/null 2>&1 || true
  [ $DOCKER -eq 1 ] && $SUDO docker rmi masterofzen/av1an:master >/dev/null 2>&1 || true
fi
sleep 1
rm -f ${DESKS[@]+"${DESKS[@]}"}
[ ${#TMPS[@]} -gt 0 ] && { rm -rf "${TMPS[@]}" 2>/dev/null || $SUDO rm -rf "${TMPS[@]}"; }
for d in ${TMPP[@]+"${TMPP[@]}"}; do rmdir "$d" 2>/dev/null || true; done     # 空になったときだけ消える
# 以前の版のインストーラーで uv が ~/.local/bin に置いた python のリンク（このフォルダの Python を指すものだけ）
for f in "$HOME_DIR"/.local/bin/python*; do
  [ -L "$f" ] || continue
  case "$(readlink -f "$f" 2>/dev/null)" in "$APP"/*) rm -f "$f";; esac
done
# Docker が root で作ったファイルが残る場合がある
rm -rf "$APP" 2>/dev/null || $SUDO rm -rf "$APP"
echo "アンインストール完了"
