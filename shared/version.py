"""このフォルダのコードの指紋（動いているサーバーが更新前の古いコードのままか見分ける）

エンコードは Web サーバーのプロセスが行うため、ファイルを更新してもサーバーを起動し直すまで古いコードで動く。
サーバーは起動時の指紋を持ち、デスクトップ版はいまのファイルの指紋と比べる。"""
from __future__ import annotations

import hashlib
import time

from . import ROOT, apps

_EXTS = {".py", ".html", ".js", ".css", ".json"}
_cache: tuple[float, str] = (0.0, "")


def app_version() -> str:
    """このフォルダの版（直下の VERSION。リリースのタグ vX.Y.Z と同じ番号）"""
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def parse_version(v: str) -> tuple:
    """"v0.4.0" / "0.4.0" → (0, 4, 0)。数字でない部分は 0"""
    out = []
    for x in v.strip().lstrip("vV").split("."):
        digits = "".join(c for c in x if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out + [0] * (3 - len(out)))


def code_fingerprint(max_age: float = 0) -> str:
    """shared と各ツールのパッケージの中身から作る（日時ではなく中身なので、同じ内容なら同じ値）"""
    global _cache
    if max_age and time.time() - _cache[0] < max_age:
        return _cache[1]
    h = hashlib.sha1()
    for top in [ROOT / "shared", *(ROOT / p for p in apps())]:
        for f in sorted(top.rglob("*")):
            if f.suffix in _EXTS and "__pycache__" not in f.parts and f.is_file():
                try:
                    data = f.read_bytes()
                except OSError:
                    continue
                h.update(str(f.relative_to(ROOT)).replace("\\", "/").encode())
                h.update(hashlib.sha1(data).digest())
    _cache = (time.time(), h.hexdigest()[:16])
    return _cache[1]
