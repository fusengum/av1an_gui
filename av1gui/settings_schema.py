"""設定画面の定義（デスクトップ版の設定画面と Web 画面の設定タブで共通。形式は shared.web.apps.WebApp を参照）

main=True の項目はデスクトップ版ではメイン画面に置くので、設定画面には出さない（Web 画面には出す）。
デスクトップ版のメイン画面: エンコーダ・画質・速度・並列数・目標画質・追加パラメータ・エンコーダの場所・保存先
advanced=True の項目は「詳しい設定」の中に畳んで出す。"""
from __future__ import annotations

from shared.core.tuning import CONTENT_LABELS
from shared.core.video import CROP_LABELS, RES_LABELS

from . import encoders as E
from .encode import AUDIO_LABELS, CHUNK_LABELS, SC_LABELS, SPLIT_LABELS


def _f(key, label, type_, tip="", **kw) -> dict:
    return {"key": key, "label": label, "type": type_, "tip": tip, **kw}


SECTIONS = [
    {"id": "general", "title": "一般", "fields": [
        _f("output_dir", "保存先", "dir", "out_dir", main=True, placeholder="空欄 = 入力と同じフォルダ",
           hint="変換したファイルを書き出すフォルダです。"),
        _f("suffix", "ファイル名の末尾", "text", "suffix", placeholder="空欄 = 自動（_AV1 / _HEVC / _AVC）",
           hint="元のファイル名の後ろに付けます。"),
        _f("temp_dir", "作業フォルダ", "dir", "temp_dir", placeholder="空欄 = 保存先に作ります",
           hint="エンコード途中のファイルを置きます。空き容量が多く速いドライブがおすすめです。"),
        _f("keep_temp", "作業ファイルを残す", "bool", "keep_temp",
           hint="トラブルを調べるとき用です。通常はオフにします（完了後に自動で消します）。"),
    ]},
    {"id": "defaults", "title": "追加時の既定",
     "note": "これから追加するファイルに最初から設定する値です。追加した後も、一覧の各行で変えられます。",
     "fields": [
         _f("default_content", "種類", "select", "content", choices=CONTENT_LABELS,
            hint="映像に合わせて細かい設定を変えます。アニメは線の周りのノイズと縞を抑えます。"),
         _f("default_res", "解像度", "select", "res", choices=RES_LABELS,
            hint="「1080p に縮小」は 4K を横 1920 にします。容量が大きく減ります。"),
         _f("default_crop", "黒帯", "select", "crop", choices=CROP_LABELS,
            hint="映画の上下の黒帯を自動で見つけて切り取ります。"),
         _f("default_audio", "音声", "select", "audio", choices=AUDIO_LABELS,
            hint="「そのまま」は元の音声をすべて残します。字幕とチャプターはどれでも引き継ぎます。"),
     ]},
    {"id": "encode", "title": "エンコード", "fields": [
        _f("encoder", "エンコーダ", "select", "encoder", choices=E.ENCODER_LABELS, main=True),
        _f("quality", "画質", "select", "quality", choices=E.QUALITY_LABELS, main=True,
           show_if={"target_quality": False}),
        _f("custom_q", "画質の数値", "int", "quality", main=True, min=0, max=255,
           show_if={"quality": "custom", "target_quality": False},
           hint="小さいほど高画質です。尺度はエンコーダごとに違います（x265 / SVT-AV1 は CRF）。"),
        _f("speed", "速度", "select", "speed", choices=E.SPEED_LABELS, main=True,
           hint="遅いほど、同じ容量で画質が良くなります。"),
        _f("target_quality", "目標画質（VMAF）を使う", "bool", "target", main=True,
           hint="画質の値の代わりに、場面ごとに VMAF が目標になるよう調整します。時間は 2〜3 倍かかります。"),
        _f("target_vmaf", "目標の VMAF", "number", "target", main=True, min=70, max=99.5, step=0.5,
           show_if={"target_quality": True}, hint="95 前後で元とほぼ見分けがつきません。"),
        _f("target_probes", "試行回数", "int", "probes", main=True, min=2, max=10, show_if={"target_quality": True},
           hint="多いほど正確ですが、時間がかかります（既定 4）。"),
        _f("extra.{encoder}", "追加パラメータ", "text", "extra", main=True,
           placeholder="例: --psy-rd 1.5（いま選んでいるエンコーダの書き方）",
           hint="上級者向けです。いま選んでいるエンコーダの分だけを保存します。"),
        _f("encoder_paths.{encoder}", "エンコーダの場所", "file", "encoder_path", main=True,
           placeholder="空欄 = 自動で取得したもの",
           hint="SVT-AV1-PSY などの派生版を使うときに指定します。"),
    ]},
    {"id": "parallel", "title": "並列数",
     "note": "通常は 0（自動）のままで大丈夫です。CPU の数と空きメモリから、ファイルごとに決めます。"
             "決め方を変えたいときは、下のアシストで質問に答えてください。",
     "actions": [{"id": "assist", "label": "質問に答えて並列数を決める…"}],
     "fields": [
         _f("workers", "並列数", "int", "workers", main=True, min=0, max=4096, suffix="本", special="自動",
            hint="同時に動かすエンコーダの数です。0 = 自動。"),
         _f("threads", "1 本あたりのスレッド", "int", "threads", main=True, min=0, max=1024, special="自動",
            hint="0 = 自動（全コアを並列数で割った数）。"),
     ]},
    {"id": "split", "title": "分割",
     "note": "映像を場面の切れ目で小さく分け、複数を同時にエンコードします。通常は「自動」のままで大丈夫です。",
     "fields": [
         _f("split", "分割の長さ", "select", "split", choices=SPLIT_LABELS,
            hint="自動では、1 つ最大 10 秒を基本に、短い映像では並列数が埋まるよう短くします。"
                 "最小の長さは 1 秒分（24 fps なら 24 フレーム）にします。"),
         _f("max_scene_sec", "最大の長さ", "number", "max_scene", min=1, max=600, step=0.5, suffix="秒",
            show_if={"split": "manual"}, hint="長い場面はこの長さで切ります。短いほど並列が効きますが、容量が少し増えます。"),
         _f("min_scene_len", "最小の長さ", "int", "min_scene", min=1, max=1000, suffix="フレーム",
            show_if={"split": "manual"}, hint="これより短い場面では切りません。目安は 1 秒分のフレーム数です。"),
         _f("chunk_method", "分割方法", "select", "chunk", choices=CHUNK_LABELS, advanced=True,
            hint="「自動」で問題ありません（Windows は hybrid、Linux は lsmash）。"),
         _f("sc_method", "シーン検出", "select", "sc", choices=SC_LABELS, advanced=True,
            hint="「速い」は検出が速くなりますが、切れ目の精度が下がります。"),
     ]},
]
