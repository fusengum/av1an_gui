"""外部ツールの検出と自動ダウンロード。

共通で扱うもの: FFmpeg / FFprobe、MKVToolNix（mkvmerge）、av1an とエンコーダ一式。
ツールごとに必要なものは INSTALLERS / EXTRA_CHECKS / EXTRA_SEARCH に登録して足す。
"""
from __future__ import annotations

import io
import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import EXE, IS_WIN, TOOLS_DIR

BIN_DIR = TOOLS_DIR / "bin"
ProgressCB = Callable[[str, float], None]   # (message, 0..1 or -1)

NO_WINDOW = 0x08000000 if IS_WIN else 0     # CREATE_NO_WINDOW

# 拡張用のフック
EXTRA_SEARCH: list = []    # (name, settings) -> 追加で探すフォルダの list[Path]
EXTRA_CHECKS: list = []    # (settings) -> 環境チェックに足す list[ToolStatus]


def popen_kwargs() -> dict:
    return {"creationflags": NO_WINDOW} if IS_WIN else {}


# ---------------------------------------------------------------- 検出

def _which_any(names: list[str], extra_dirs: list[Path]) -> str | None:
    for d in [BIN_DIR, *extra_dirs]:
        for n in names:
            p = d / n
            if p.is_file():
                return str(p)
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def find_tool(name: str, settings) -> str | None:
    explicit = getattr(settings, name, "") or ""
    if explicit and Path(explicit).is_file():
        return explicit
    extra: list[Path] = []
    if name == "mkvmerge" and IS_WIN:
        extra += [Path(r"C:\Program Files\MKVToolNix"), Path(r"C:\Program Files (x86)\MKVToolNix")]
    if name == "mkvmerge":
        extra.append(TOOLS_DIR / "mkvtoolnix")
    for h in EXTRA_SEARCH:
        extra += h(name, settings)
    return _which_any([name + EXE], extra)


@dataclass
class ToolStatus:
    name: str
    label: str
    required: bool
    path: str | None
    version: str = ""
    note: str = ""
    downloadable: bool = False

    @property
    def ok(self) -> bool:
        return self.path is not None


def _version(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                             encoding="utf-8", errors="replace", **popen_kwargs())
        line = (out.stdout or out.stderr).strip().splitlines()
        return line[0][:80] if line else ""
    except Exception:
        return ""


def check_all(settings, tool_list=None, include_extra: bool = True) -> list[ToolStatus]:
    """tool_list: (名前, 表示名, 必須か, バージョン表示の引数, 無いときの説明) の一覧。
    include_extra: 拡張が登録した追加のチェック（EXTRA_CHECKS）も行う"""
    res: list[ToolStatus] = []
    if include_extra:
        for h in EXTRA_CHECKS:
            res += h(settings)

    for name, label, req, ver_args, need in (tool_list or []):
        if name in ("av1an", "av1an_encoders", "vsplugins"):
            from . import av1an as A
            ok, msg = {"av1an": A.status, "av1an_encoders": A.encoders_status, "vsplugins": A.vsplugins_status}[name]()
            res.append(ToolStatus(name, label, req, msg if ok else None, note="" if ok else f"{msg}（{need}）",
                                  downloadable=True))
            continue
        p = find_tool(name, settings)
        st = ToolStatus(name, label, req, p, downloadable=name in INSTALLERS)
        if p:
            st.version = _version([p, *ver_args])
        else:
            st.note = need
        res.append(st)
    return res


# ---------------------------------------------------------------- ダウンロード

def _arch() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "x64"


def _open(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "av1an_gui", "Accept": "*/*"})
    ctx = ssl.create_default_context()
    return urllib.request.urlopen(req, timeout=60, context=ctx)


def _download(url: str, dst: Path, cb: ProgressCB, label: str) -> Path:
    with _open(url) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(dst, "wb") as f:
            while True:
                buf = r.read(1 << 20)
                if not buf:
                    break
                f.write(buf)
                done += len(buf)
                cb(f"{label} をダウンロード中… {done/1e6:.0f} MB", done / total if total else -1)
    return dst


def _extract_bins(archive: Path, wanted: list[str], cb: ProgressCB) -> list[Path]:
    """アーカイブ内から wanted のファイル名（拡張子込み）を探して BIN_DIR に置く"""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    got = []
    name = archive.name.lower()
    cb(f"{archive.name} を展開中…", -1)
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                base = Path(info.filename).name
                if base in wanted and not info.is_dir():
                    out = BIN_DIR / base
                    with z.open(info) as src, open(out, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    got.append(out)
    else:
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                base = Path(m.name).name
                if base in wanted and m.isfile():
                    out = BIN_DIR / base
                    with t.extractfile(m) as src, open(out, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    got.append(out)
    for p in got:
        if not IS_WIN:
            p.chmod(0o755)
    return got


def install_ffmpeg(cb: ProgressCB) -> list[Path]:
    """BtbN の FFmpeg ビルド（GPL, 最新 master）を取得"""
    arch = _arch()
    if IS_WIN:
        fn = "ffmpeg-master-latest-winarm64-gpl.zip" if arch == "arm64" else "ffmpeg-master-latest-win64-gpl.zip"
    else:
        fn = "ffmpeg-master-latest-linuxarm64-gpl.tar.xz" if arch == "arm64" else "ffmpeg-master-latest-linux64-gpl.tar.xz"
    url = f"https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/{fn}"
    with tempfile.TemporaryDirectory() as td:
        arc = _download(url, Path(td) / fn, cb, "FFmpeg")
        got = _extract_bins(arc, [f"ffmpeg{EXE}", f"ffprobe{EXE}"], cb)
    if len(got) < 2:
        raise RuntimeError("FFmpeg の展開に失敗しました")
    return got


def _install_github(repo: str, exe: str, cb: ProgressCB) -> list[Path]:
    """GitHub Releases の最新版から、この OS/CPU 向けのアーカイブを取得して exe を取り出す"""
    cb(f"{exe} の最新リリースを確認中…", -1)
    with _open(f"https://api.github.com/repos/{repo}/releases/latest") as r:
        rel = json.load(r)
    arch = _arch()
    arch_keys = ("aarch64", "arm64") if arch == "arm64" else ("x86_64", "amd64")
    os_keys = ("windows",) if IS_WIN else ("linux",)
    asset = None
    for a in rel.get("assets", []):
        n = a["name"].lower()
        if any(k in n for k in os_keys) and any(k in n for k in arch_keys) and \
                n.endswith((".zip", ".tar.gz", ".tar.xz", ".tgz")):
            if asset is None or ("musl" in n and "musl" not in asset["name"].lower()):
                asset = a          # Linux は静的リンクの musl 版を優先
    if asset is None:
        raise RuntimeError(f"この環境向けの {exe} が見つかりません（{rel.get('tag_name')}）")
    with tempfile.TemporaryDirectory() as td:
        arc = _download(asset["browser_download_url"], Path(td) / asset["name"], cb, f"{exe} {rel.get('tag_name')}")
        got = _extract_bins(arc, [f"{exe}{EXE}"], cb)
    if not got:
        raise RuntimeError(f"{exe} の展開に失敗しました")
    return got


def install_mkvtoolnix(cb: ProgressCB) -> list[Path]:
    """Windows: MKVToolNix のポータブル版 zip を tools/mkvtoolnix に展開。Linux はパッケージで導入"""
    if not IS_WIN:
        raise RuntimeError("Linux では apt install mkvtoolnix などで導入してください")
    cb("MKVToolNix の最新バージョンを確認中…", -1)
    with _open("https://mkvtoolnix.download/latest-release.xml") as r:
        xml = r.read().decode("utf-8", "replace")
    m = re.search(r"<version>\s*(\d+(?:\.\d+)+)\s*</version>", xml)
    if not m:
        raise RuntimeError("MKVToolNix の最新バージョンを特定できません")
    ver = m.group(1)
    bits = "32" if platform.machine().endswith("86") else "64"
    fn = f"mkvtoolnix-{bits}-bit-{ver}.zip"
    dst_dir = TOOLS_DIR / "mkvtoolnix"
    with tempfile.TemporaryDirectory() as td:
        arc = _download(f"https://mkvtoolnix.download/windows/releases/{ver}/{fn}", Path(td) / fn, cb,
                        f"MKVToolNix {ver}")
        cb("MKVToolNix を展開中…", -1)
        shutil.rmtree(dst_dir, ignore_errors=True)
        with zipfile.ZipFile(arc) as z:
            for info in z.infolist():
                parts = Path(info.filename).parts
                if info.is_dir() or len(parts) < 2:
                    continue
                out = dst_dir.joinpath(*parts[1:])      # 先頭の mkvtoolnix/ を外す
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(out, "wb") as f:
                    shutil.copyfileobj(src, f)
    exe = dst_dir / "mkvmerge.exe"
    if not exe.exists():
        raise RuntimeError("mkvmerge.exe が見つかりません")
    return [exe]


INSTALLERS = {
    "ffmpeg": install_ffmpeg,
    "ffprobe": install_ffmpeg,
}
if IS_WIN:
    INSTALLERS["mkvmerge"] = install_mkvtoolnix


def install_av1an(cb: ProgressCB) -> list[Path]:
    from . import av1an as A
    return A.install(cb)


INSTALLERS["av1an"] = install_av1an


def install_av1an_encoders(cb: ProgressCB) -> list[Path]:
    from . import av1an as A
    return A.install_encoders(cb)


INSTALLERS["av1an_encoders"] = install_av1an_encoders


def install_vsplugins(cb: ProgressCB) -> list[Path]:
    from . import av1an as A
    return A.install_vsplugins(cb)


if IS_WIN:
    INSTALLERS["vsplugins"] = install_vsplugins


def install_missing(settings, cb: ProgressCB, force: bool = False, tool_list=None) -> list[str]:
    """不足しているダウンロード可能ツールを取得。戻り値は導入したツール名"""
    done: list[str] = []
    seen = set()
    for st in check_all(settings, tool_list, include_extra=False):
        if st.downloadable and (force or not st.ok):
            fn = INSTALLERS[st.name]
            if fn in seen:
                continue
            seen.add(fn)
            fn(cb)
            done.append(st.name)
    cb("完了", 1.0)
    return done
