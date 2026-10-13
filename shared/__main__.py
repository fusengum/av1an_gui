"""共通のコマンド

  python -m shared setup [--folder KEY=DIR ...] [--force] [--progress] [--apps PKG,PKG]
      入っているツールが使う外部ツールを自動取得する（インストーラーから呼ばれる）
  python -m shared check       環境チェック
  python -m shared apps        入っているツールの一覧
  python -m shared folders     手動で指定するフォルダの一覧と、検出した場所（key<TAB>場所<TAB>見出し）
"""
from __future__ import annotations

import argparse
import sys
import time

from . import app_name, apps, folders, tool_list
from .core import tools
from .settings import detect_folder, folder_ok, set_folder, tool_settings


def _env_hooks(pkgs):
    """各ツールの env.py を読み込む（ツール固有の自動取得や環境チェックがここで登録される）"""
    from . import env_module
    for p in pkgs:
        env_module(p)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="shared")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("setup")
    sp.add_argument("--folder", action="append", default=[], help="手動で指定するフォルダ（KEY=DIR）")
    sp.add_argument("--force", action="store_true", help="既にあっても取り直します")
    sp.add_argument("--progress", action="store_true", help="インストーラー画面向けの進捗行（@@STEP / @@PROG）を出します")
    sp.add_argument("--apps", help="対象のツール（カンマ区切り。省略時は入っているものすべて）")
    sub.add_parser("check")
    sub.add_parser("apps")
    sub.add_parser("folders")
    a = ap.parse_args(argv)
    pkgs = a.apps.split(",") if getattr(a, "apps", None) else apps()
    _env_hooks(pkgs)

    if a.cmd == "apps":
        for p in apps():
            print(f"{p}\t{app_name(p)}")
        return 0
    if a.cmd == "folders":
        for fd in folders(pkgs):
            print(f"{fd['key']}\t{detect_folder(fd)}\t{fd.get('title', fd['key'])}")
        return 0

    if a.cmd == "setup":
        given = dict(x.split("=", 1) for x in a.folder if "=" in x)
        for fd in folders(pkgs):
            v = given.get(fd["key"], "").strip().strip('"')
            if v and not folder_ok(fd, v):
                print(f"{fd.get('title', fd['key'])}: 指定のフォルダが正しくありません: {v}", file=sys.stderr)
                v = ""
            v = v or detect_folder(fd)
            if v:
                set_folder(fd, v)      # 指定・自動検出した場所を保存する
        s = tool_settings(pkgs)
        machine = a.progress
        last = [0.0]

        def emit(line: str) -> None:
            print(line, flush=True)

        def cb(msg: str, frac: float) -> None:
            if machine:
                now = time.monotonic()
                if frac < 0 or frac >= 1 or now - last[0] > 0.2:   # 進捗行は間引く
                    last[0] = now
                    emit(f"@@PROG {frac:.3f} {msg}")
            elif frac < 0 or frac >= 1:
                print(msg, flush=True)

        # 1 つ失敗しても残りは続ける。同じ導入処理（ffmpeg / ffprobe）は 1 回にまとめる
        seen = set()
        for item in tool_list(pkgs):
            name, label = item[0], item[1]
            fn = tools.INSTALLERS.get(name)
            if fn is None or fn in seen:
                continue
            seen.add(fn)
            if name == "ffmpeg":
                label = "FFmpeg / FFprobe"
            if machine:
                emit(f"@@STEP {name} run {label}")
            try:
                st = tools.check_all(s, [item], include_extra=False)[0]
                if st.ok and not a.force:
                    if machine:
                        emit(f"@@STEP {name} skip {label}（導入済み）")
                    continue
                tools.install_missing(s, cb, force=a.force, tool_list=[item])
                st = tools.check_all(s, [item], include_extra=False)[0]
                if machine:
                    emit(f"@@STEP {name} {'ok' if st.ok else 'ng'} {label}")
            except Exception as e:  # noqa: BLE001
                print(f"{label} の導入に失敗しました: {e}", file=sys.stderr, flush=True)
                if machine:
                    emit(f"@@STEP {name} ng {label}: {e}")
        a.cmd = "check"

    if a.cmd == "check":
        ok = True
        s = tool_settings(pkgs)
        print("ツール: " + ", ".join(app_name(p) for p in pkgs))
        for t in tools.check_all(s, tool_list(pkgs)):
            mark = "OK " if t.ok else ("NG " if t.required else "-- ")
            ok &= t.ok or not t.required
            print(f"[{mark}] {t.label:<34} {t.path or t.note}")
        for fd in folders(pkgs):
            if found := detect_folder(fd):
                print(f"[OK ] {fd.get('title', fd['key']):<34} {found}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
