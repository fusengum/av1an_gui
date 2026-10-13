"""共通のツールチップ（外部ツールの説明など）。各ツールは自分の説明でこれを上書きする"""


def _h(title: str, body: str) -> str:
    return f"<b>{title}</b><br>{body}"


TIPS = {
    "ffmpeg": _h("FFmpeg / FFprobe", "動画・音声の読み込みと解析に使います。自動取得できます。"),
    "mkvmerge": _h("mkvmerge（MKVToolNix）", "MKV を作るツールです。Windows では自動取得できます（Linux はパッケージで導入します）。"),
    "av1an": _h("av1an", "映像を場面ごとに分けて、エンコーダを複数同時に動かすツールです。自動取得できます"
                "（Windows は実行ファイル一式、Linux は Docker イメージ）。"),
    "av1an_encoders": _h("AV1 エンコーダ", "SVT-AV1・aomenc・rav1e・x264 です。x265 は av1an と一緒に入ります。"
                         "Windows では自動取得、Linux では Docker イメージに含まれます。"),
}
