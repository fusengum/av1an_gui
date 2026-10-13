"""ツールの設定ファイルを横断して読む・書く（環境構築やフォルダ指定で使う）"""
from __future__ import annotations

import json
import os
import string
from pathlib import Path
from types import SimpleNamespace

from . import ROOT, app_infos, folders
from .core.config import IS_WIN


def _path(info: dict) -> Path:
    return ROOT / info.get("settings_file", f"settings_{info['pkg']}.json")


def _read(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def tool_settings(pkgs: list[str] | None = None) -> SimpleNamespace:
    """各ツールの設定を合わせたもの（外部ツールの場所の指定などを find_tool に渡す）。保存はしない"""
    merged: dict = {}
    for info in app_infos():
        if pkgs and info["pkg"] not in pkgs:
            continue
        for k, v in _read(_path(info)).items():
            if v not in ("", None) and k not in merged:
                merged[k] = v
    for fd in folders(pkgs):
        merged.setdefault(fd["key"], "")
    ns = SimpleNamespace(**merged)
    ns.save = lambda: None
    return ns


def _info(pkg: str) -> dict:
    return next(d for d in app_infos() if d["pkg"] == pkg)


def get_folder(fd: dict) -> str:
    return _read(_path(_info(fd["pkg"]))).get(fd["key"], "") or ""


def set_folder(fd: dict, value: str) -> None:
    p = _path(_info(fd["pkg"]))
    d = _read(p)
    d[fd["key"]] = value
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def folder_file(fd: dict) -> str:
    return fd.get("file", "") if IS_WIN else fd.get("file_linux", fd.get("file", ""))


def folder_ok(fd: dict, path: str) -> bool:
    f = folder_file(fd)
    return bool(path) and (Path(path) / f).is_file() if f else Path(path).is_dir()


def detect_folder(fd: dict) -> str:
    """設定済みの値 → このアプリの親フォルダ → よくある場所 の順に探す"""
    cands = [get_folder(fd)]
    if fd.get("search_parents"):
        cands += [str(p) for p in ROOT.parents[:2]]
    if IS_WIN:
        drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        cands += [str(Path(d) / rel) for d in drives for rel in fd.get("search", [])]
    else:
        cands += [os.path.expanduser(x) for x in fd.get("search_linux", [])]
    return next((c for c in cands if c and folder_ok(fd, c)), "")
