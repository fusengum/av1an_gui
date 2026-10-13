"""映像の共通処理（黒帯の検出・HDR マスタリング情報の書式）"""
from __future__ import annotations

import re
import subprocess
from collections import Counter

from . import probe as P
from .runner import Cancelled, Runner
from .tools import popen_kwargs

RES_KEEP, RES_1080 = "keep", "1080p"
CROP_AUTO, CROP_NONE = "auto", "none"
RES_LABELS = {RES_KEEP: "元のまま", RES_1080: "1080p に縮小"}
CROP_LABELS = {CROP_AUTO: "黒帯を自動で切る", CROP_NONE: "切らない"}


def detect_crop(ffmpeg: str, info: P.MediaInfo, runner: Runner, samples: int = 8) -> str | None:
    """動画の数か所で cropdetect を走らせ、最も多い結果を返す（w:h:x:y）。黒帯が無ければ None"""
    v = info.video
    dur = info.duration or 0
    if dur < 60:
        points = [max(0.0, dur * 0.3)]
    else:
        points = [dur * (i + 1) / (samples + 1) for i in range(samples)]
    votes: Counter = Counter()
    pat = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")
    for t in points:
        if runner.cancel.is_set():
            raise Cancelled()
        out = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-ss", f"{t:.2f}", "-i", str(info.path),
                              "-map", f"0:{v.index}", "-frames:v", "48", "-vf", "cropdetect=limit=0.09:round=2:reset=0",
                              "-f", "null", "-"], capture_output=True, text=True, errors="replace", **popen_kwargs())
        found = pat.findall(out.stderr)
        if found:
            votes[found[-1]] += 1
    if not votes:
        return None
    w, h, x, y = map(int, votes.most_common(1)[0][0])
    # 横の小さな誤検出は無視し、上下の黒帯だけを対象にする（左右も 2% 超なら採用）
    if w >= v.width * 0.98:
        w, x = v.width, 0
    if h >= v.height * 0.98:
        h, y = v.height, 0
    if (w, h) == (v.width, v.height) or w < v.width * 0.5 or h < v.height * 0.5:
        return None
    return f"{w}:{h}:{x}:{y}"


def master_display(v: P.VideoTrack) -> str | None:
    m = v.mastering
    keys = ("green_x", "green_y", "blue_x", "blue_y", "red_x", "red_y", "white_point_x", "white_point_y")
    if not m or not all(k in m for k in keys) or not m.get("max_lum"):
        return None
    c = lambda k: int(round(m[k] * 50000))  # noqa: E731
    return (f"G({c('green_x')},{c('green_y')})B({c('blue_x')},{c('blue_y')})R({c('red_x')},{c('red_y')})"
            f"WP({c('white_point_x')},{c('white_point_y')})"
            f"L({int(round(m['max_lum'] * 10000))},{int(round(m.get('min_lum', 0.005) * 10000))})")


