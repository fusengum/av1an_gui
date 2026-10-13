"""エンコーダごとの定義（画質・速度・スレッド・HDR 情報・種類別の調整）

すべて「エンコーダ本体の CLI 形式」の引数を返す（av1an の --video-params にそのまま渡す）。
種類別の調整（実写 / アニメ）の考え方:

SVT-AV1（3.x / 4.x 本家のオプションのみ使用。PSY 系の派生版でも通る）
  共通: --tune 0（見た目重視） --enable-qm 1 --qm-min 0（量子化行列で細部とグラデーションを残す）
        --enable-variance-boost 1（平坦部・暗部に符号量を回してバンディングを抑える）
  実写: --film-grain 8（粒子を合成で再現し、粒子そのものに容量を使わない）、--ac-bias 1.0（細かい質感を残す）
  アニメ: --film-grain 0、--variance-boost-strength 3（グラデーションの縞を強めに抑える）
aomenc
  実写: --tune-content=film --enable-qm=1 + av1an の --photon-noise 8（粒子の合成）
  アニメ: --enable-qm=1
rav1e
  実写: av1an の --photon-noise 8
x265
  shared/core/tuning.py と同じ
x264
  実写: --tune film --aq-mode 3、アニメ: --tune animation --aq-mode 3
"""
from __future__ import annotations

from shared.core.tuning import CONTENT_ANIME, CONTENT_FILM, CONTENT_LABELS, x265_tuning  # noqa: F401
from shared.core import probe as P

ENCODERS = ["svt-av1", "aom", "rav1e", "x265", "x264"]
ENCODER_LABELS = {
    "svt-av1": "SVT-AV1（AV1・おすすめ）",
    "aom": "aomenc（AV1・高画質だが遅い）",
    "rav1e": "rav1e（AV1）",
    "x265": "x265（HEVC）",
    "x264": "x264（H.264）",
}
CODEC_OF = {"svt-av1": "AV1", "aom": "AV1", "rav1e": "AV1", "x265": "HEVC", "x264": "AVC"}

QUALITY_LABELS = {"high": "高画質", "standard": "標準", "small": "小さめ", "custom": "数値で指定"}
SPEED_LABELS = {"slow": "遅い・高画質", "medium": "標準", "fast": "速い"}

# 画質の値（数値が小さいほど高画質）: (高画質, 標準, 小さめ, 範囲, 名前)
QUALITY = {
    "svt-av1": (21, 25, 30, (1, 63), "CRF"),
    "aom": (22, 27, 32, (0, 63), "cq-level"),
    "rav1e": (60, 80, 100, (0, 255), "quantizer"),
    "x265": (16, 18, 21, (0, 51), "CRF"),
    "x264": (16, 18, 21, (0, 51), "CRF"),
}
# 速度: (遅い, 標準, 速い)
SPEED = {
    "svt-av1": ("4", "6", "8"),
    "aom": ("3", "4", "6"),
    "rav1e": ("4", "6", "8"),
    "x265": ("slow", "medium", "fast"),
    "x264": ("slow", "medium", "fast"),
}
SPEED_INDEX = {"slow": 0, "medium": 1, "fast": 2}


def quality_value(enc: str, quality: str, custom: int) -> int:
    if quality == "custom":
        lo, hi = QUALITY[enc][3]
        return max(lo, min(hi, int(custom)))
    i = {"high": 0, "standard": 1, "small": 2}.get(quality, 1)
    return QUALITY[enc][i]


def base_args(enc: str, q: int, speed: str) -> list[str]:
    sp = SPEED[enc][SPEED_INDEX.get(speed, 0)]
    if enc == "svt-av1":
        return ["--preset", sp, "--crf", str(q)]
    if enc == "aom":
        return [f"--cpu-used={sp}", "--end-usage=q", f"--cq-level={q}"]
    if enc == "rav1e":
        return ["--speed", sp, "--quantizer", str(q)]
    if enc == "x265":
        return ["--preset", sp, "--crf", str(q), "--output-depth", "10", "--profile", "main10", "--no-open-gop"]
    if enc == "x264":
        return ["--preset", sp, "--crf", str(q), "--output-depth", "10", "--profile", "high10"]
    return []


def thread_args(enc: str, threads: int) -> list[str]:
    from shared.core.av1an import svt_lp
    if enc == "svt-av1":
        return ["--lp", str(svt_lp(threads))]
    if enc == "aom":
        return [f"--threads={threads}", "--row-mt=1"]
    if enc == "rav1e":
        return ["--threads", str(threads)]
    if enc == "x265":
        return ["--pools", str(threads)]
    if enc == "x264":
        return ["--threads", str(threads)]
    return []


def tuning_args(enc: str, content: str, hdr: bool) -> list[str]:
    if enc == "svt-av1":
        if content == CONTENT_FILM:
            return ["--tune", "0", "--enable-qm", "1", "--qm-min", "0", "--enable-variance-boost", "1",
                    "--film-grain", "8", "--ac-bias", "1.0"]
        if content == CONTENT_ANIME:
            return ["--tune", "0", "--enable-qm", "1", "--qm-min", "0", "--enable-variance-boost", "1",
                    "--variance-boost-strength", "3", "--film-grain", "0"]
        return []
    if enc == "aom":
        if content == CONTENT_FILM:
            return ["--tune-content=film", "--enable-qm=1"]
        if content == CONTENT_ANIME:
            return ["--enable-qm=1"]
        return []
    if enc == "x265":
        return x265_tuning(content, hdr)
    if enc == "x264":
        if content == CONTENT_FILM:
            return ["--tune", "film", "--aq-mode", "3"]
        if content == CONTENT_ANIME:
            return ["--tune", "animation", "--aq-mode", "3"]
        return []
    return []


def photon_noise(enc: str, content: str) -> int | None:
    """aomenc / rav1e は av1an の photon noise で粒子を合成する（実写のみ）"""
    if enc in ("aom", "rav1e") and content == CONTENT_FILM:
        return 8
    return None


def _md_float(v: P.VideoTrack) -> str | None:
    m = v.mastering
    keys = ("green_x", "green_y", "blue_x", "blue_y", "red_x", "red_y", "white_point_x", "white_point_y")
    if not m or not all(k in m for k in keys) or not m.get("max_lum"):
        return None
    f = lambda k: f"{m[k]:.4f}"  # noqa: E731
    return (f"G({f('green_x')},{f('green_y')})B({f('blue_x')},{f('blue_y')})R({f('red_x')},{f('red_y')})"
            f"WP({f('white_point_x')},{f('white_point_y')})L({m['max_lum']:.1f},{m.get('min_lum', 0.005):.4f})")


def color_args(enc: str, v: P.VideoTrack, uhd: bool) -> tuple[list[str], str]:
    """(色・HDR 情報の引数, 注意書き)"""
    from shared.core.video import master_display
    pq = v.is_pq
    hlg = v.transfer == "arib-std-b67"
    hdr = pq or hlg
    note = ""
    if enc == "svt-av1":
        a = ["--color-primaries", "9" if hdr else "1", "--transfer-characteristics",
             "16" if pq else ("18" if hlg else "1"), "--matrix-coefficients", "9" if hdr else "1", "--color-range", "0"]
        if uhd:
            a += ["--chroma-sample-position", "topleft"]
        if pq:
            md = _md_float(v)
            if md:
                a += ["--mastering-display", md]
            if v.max_cll:
                a += ["--content-light", f"{v.max_cll},{v.max_fall}"]
        return a, note
    if enc == "aom":
        a = [f"--color-primaries={'bt2020' if hdr else 'bt709'}",
             f"--transfer-characteristics={'smpte2084' if pq else ('hlg' if hlg else 'bt709')}",
             f"--matrix-coefficients={'bt2020ncl' if hdr else 'bt709'}"]
        if pq:
            note = "aomenc はマスタリング情報（MaxCLL など）を書けないため、出力時に MKV 側へ書き込みます"
        return a, note
    if enc == "rav1e":
        a = ["--primaries", "BT2020" if hdr else "BT709", "--transfer",
             "SMPTE2084" if pq else ("HLG" if hlg else "BT709"), "--matrix", "BT2020NCL" if hdr else "BT709",
             "--range", "Limited"]
        if pq:
            md = _md_float(v)
            if md:
                a += ["--mastering-display", md]
            if v.max_cll:
                a += ["--content-light", f"{v.max_cll},{v.max_fall}"]
        return a, note
    if enc in ("x265", "x264"):
        prim = v.primaries or ("bt2020" if hdr else "bt709")
        trc = v.transfer or ("smpte2084" if pq else "bt709")
        mat = v.matrix or ("bt2020nc" if hdr else "bt709")
        a = ["--colorprim", prim, "--transfer", trc, "--colormatrix", mat,
             "--range", "limited" if enc == "x265" else "tv"]
        if enc == "x265":
            a.append("--repeat-headers")
            if pq:
                a += ["--hdr10", "--hdr10-opt"]
        if pq:
            md = master_display(v)
            if md:
                a += ["--master-display" if enc == "x265" else "--mastering-display", md]
            if v.max_cll:
                a += ["--max-cll" if enc == "x265" else "--cll", f"{v.max_cll},{v.max_fall}"]
        if uhd:
            a += ["--chromaloc", "2"]
        return a, note
    return [], note


def merge_args(*groups: list[str]) -> list[str]:
    """後のグループが同じ名前の引数を上書きする（--name value / --name=value / --no-name の両形式）"""
    order: list[str] = []
    vals: dict[str, list[str]] = {}

    def key(t: str) -> str:
        n = t.split("=", 1)[0].lstrip("-")
        return n[3:] if n.startswith("no-") else n

    for g in groups:
        i = 0
        while i < len(g):
            t = g[i]
            if not t.startswith("-") or _is_number(t):
                i += 1
                continue
            nxt = g[i + 1] if i + 1 < len(g) else None
            takes = nxt is not None and "=" not in t and (not nxt.startswith("-") or _is_number(nxt))
            item = [t, nxt] if takes else [t]
            k = key(t)
            if k not in vals:
                order.append(k)
            vals[k] = item
            i += len(item)
    return [x for k in order for x in vals[k]]


def _is_number(t: str) -> bool:
    try:
        float(t.split(":")[0].split(",")[0])
        return True
    except ValueError:
        return False


def strip_for_probe(args: list[str]) -> list[str]:
    """目標画質の試し打ち用: 粒子の合成は計測を狂わせるため外す"""
    out, i = [], 0
    while i < len(args):
        t = args[i]
        if t in ("--film-grain", "--film-grain-denoise"):
            i += 2
            continue
        out.append(t)
        i += 1
    return out


_HELP_CACHE: dict[str, str] = {}


def drop_unsupported(enc: str, exe: str | None, args: list[str], log=None) -> list[str]:
    """SVT-AV1 は版によって使えるオプションが違う（古い版や派生版）。--help に無いオプションは外す"""
    if enc != "svt-av1" or not exe:
        return args
    import subprocess

    from shared.core.tools import popen_kwargs
    if exe not in _HELP_CACHE:
        try:
            r = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=30, errors="replace",
                               **popen_kwargs())
            _HELP_CACHE[exe] = r.stdout + r.stderr
        except Exception:  # noqa: BLE001
            _HELP_CACHE[exe] = ""
    text = _HELP_CACHE[exe]
    if "--crf" not in text:
        return args          # ヘルプが読めなければそのまま
    out, i, dropped = [], 0, []
    while i < len(args):
        t = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None
        takes = nxt is not None and (not nxt.startswith("-") or _is_number(nxt))
        n = 2 if takes else 1
        if t.startswith("--") and f"{t.split('=')[0]}" not in text:
            dropped.append(t)
        else:
            out += args[i:i + n]
        i += n
    if dropped and log:
        log("注意: このエンコーダのバージョンでは使えない設定を外しました: " + " ".join(dropped))
    return out
