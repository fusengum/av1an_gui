"""コマンドライン版

  python -m av1gui check
  python -m av1gui encode FILE_OR_DIR... [--encoder svt-av1|aom|rav1e|x265|x264] [--quality high|standard|small|N]
                                         [--speed slow|medium|fast] [--content film|anime|standard]
                                         [--res keep|1080p] [--crop auto|none] [--audio copy|opus]
                                         [--workers N] [--threads N] [--target-vmaf 95] [--extra "..."] [--out DIR]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from shared.core import av1an as A
from shared.core import tools

from .config import AvSettings
from .encode import AvJob, AvPipeline
from .encoders import ENCODERS
from .env import TOOL_LIST

VIDEO_EXTS = (".mkv", ".m2ts", ".mts", ".ts", ".mp4", ".m4v", ".mov", ".webm", ".avi", ".hevc")


def _bar(label: str, frac: float, _last=[None]):
    pct = int(frac * 100)
    if (label, pct) != _last[0]:
        _last[0] = (label, pct)
        sys.stderr.write(f"\r[{'#' * (pct // 4):<25}] {pct:3d}%  {label:<48}")
        sys.stderr.flush()
        if frac >= 1:
            sys.stderr.write("\n")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="av1gui")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    e = sub.add_parser("encode")
    e.add_argument("inputs", nargs="+")
    e.add_argument("--encoder", choices=ENCODERS)
    e.add_argument("--quality")
    e.add_argument("--speed", choices=["slow", "medium", "fast"])
    e.add_argument("--content", choices=["film", "anime", "standard"])
    e.add_argument("--res", choices=["keep", "1080p"])
    e.add_argument("--crop", choices=["auto", "none"])
    e.add_argument("--audio", choices=["copy", "opus"])
    e.add_argument("--workers", type=int, help="並列数（0 = 自動）")
    e.add_argument("--threads", type=int, help="1 本あたりのスレッド（0 = 自動）")
    e.add_argument("--target-vmaf", type=float, help="目標画質（VMAF）。指定すると画質の値は自動で探します")
    e.add_argument("--extra", help="エンコーダの追加パラメータ")
    e.add_argument("--out")
    e.add_argument("--temp")
    e.add_argument("--keep-temp", action="store_true")
    a = ap.parse_args(argv)
    s = AvSettings.load()

    if a.cmd == "check":
        ok = True
        for t in tools.check_all(s, TOOL_LIST, include_extra=False):
            ok &= t.ok or not t.required
            print(f"[{'OK ' if t.ok else ('NG ' if t.required else '-- ')}] {t.label:<20} {t.path or t.note}")
        for enc in ENCODERS:
            st, msg = A.encoder_status(enc, (s.encoder_paths or {}).get(enc, ""))
            print(f"    {enc:<8} {'使用可' if st else '使用不可'}  {msg}")
        print(f"CPU {A.cpu_count()} / 空きメモリ {A.available_memory_gb():.0f} GB")
        return 0 if ok else 1

    if a.encoder:
        s.encoder = a.encoder
    if a.quality:
        if a.quality.isdigit():
            s.quality, s.custom_q = "custom", int(a.quality)
        else:
            s.quality = a.quality
    if a.speed:
        s.speed = a.speed
    if a.workers is not None:
        s.workers = a.workers
    if a.threads is not None:
        s.threads = a.threads
    if a.target_vmaf:
        s.target_quality, s.target_vmaf = True, a.target_vmaf
    if a.extra is not None:
        s.extra = dict(s.extra or {}, **{s.encoder: a.extra})
    if a.out:
        s.output_dir = a.out
    if a.temp:
        s.temp_dir = a.temp
    if a.keep_temp:
        s.keep_temp = True
    files: list[Path] = []
    for i in a.inputs:
        p = Path(i)
        files += sorted(f for f in p.rglob("*") if f.suffix.lower() in VIDEO_EXTS and "av1gui_" not in str(f.parent)) \
            if p.is_dir() else [p]
    fails = 0
    for f in files:
        job = AvJob(f, content=a.content or s.default_content, res=a.res or s.default_res,
                    crop=a.crop or s.default_crop, audio=a.audio or s.default_audio)
        try:
            for w in AvPipeline(s, print, _bar).run(job).warnings:
                print(f"注意: {w}")
        except Exception as ex:  # noqa: BLE001
            fails += 1
            print(f"\n失敗: {f.name}: {ex}", file=sys.stderr)
    return 1 if fails else 0
