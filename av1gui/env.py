"""av1an_gui が使う外部ツール"""
import sys

TOOL_LIST = [
    ("ffmpeg", "FFmpeg", True, ["-version"], ""),
    ("ffprobe", "FFprobe", True, ["-version"], ""),
    ("mkvmerge", "mkvmerge", True, ["--version"], ""),
    ("av1an", "av1an", True, ["--version"], "分割並列エンコードの本体"),
    ("av1an_encoders", "AV1 エンコーダ（SVT-AV1 / aomenc / rav1e / x264）", False, [],
     "x265 以外のエンコーダを使うときに必要です"),
]
if sys.platform.startswith("win"):
    # 分割を速くするための読み込みプラグイン（無ければ hybrid で分割する）。Linux は Docker イメージに同梱
    TOOL_LIST.append(("vsplugins", "VapourSynth のプラグイン（L-SMASH-Works / FFMS2）", False, [],
                      "無くても動きますが、分割の準備に時間がかかります"))
