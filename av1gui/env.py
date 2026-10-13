"""av1an_gui が使う外部ツール"""
TOOL_LIST = [
    ("ffmpeg", "FFmpeg", True, ["-version"], ""),
    ("ffprobe", "FFprobe", True, ["-version"], ""),
    ("mkvmerge", "mkvmerge", True, ["--version"], ""),
    ("av1an", "av1an", True, ["--version"], "分割並列エンコードの本体"),
    ("av1an_encoders", "AV1 エンコーダ（SVT-AV1 / aomenc / rav1e / x264）", False, [],
     "x265 以外のエンコーダを使うときに必要です"),
]
