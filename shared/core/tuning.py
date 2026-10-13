"""映像の種類（実写 / アニメ）ごとのエンコード設定（x265 / NVENC）

x265 は --preset（速度）の上に、種類ごとの調整を重ねる。最後に利用者の追加パラメータで上書きできる。

実写（film）
  --aq-mode 3          暗部を優先して符号量を配る AQ。HDR 映画の暗いシーンのブロック・バンディング対策
  --psy-rd 2 / --psy-rdoq 1.5
                       質感（細部・フィルム粒子）を残す方向。平坦化して「のっぺり」するのを防ぐ
  --no-sao             SAO は細部をぼかすため切る（高ビットレート寄りの実写で一般的）
  --deblock -1:-1      デブロックを弱めにして細部を残す
  --no-strong-intra-smoothing  4K の細部を平滑化しない
  --bframes 6 / --rc-lookahead 40  動きの多い場面での配分を良くする
  HDR のときだけ --cbqpoffs -2 --crqpoffs -2（BT.2020 の広い色域で色差を少し手厚く）

アニメ（anime）  … x265 の --tune animation を基に、近年のデジタル作画（グラデーション・撮影処理）向けに調整
  --bframes 8          静止・平坦な絵が多く、前後のフレームがよく似ているため B フレームを増やす
  --psy-rd 1 / --psy-rdoq 1   線や平坦部の周りにノイズを足さない程度に弱める（tune animation は 0.4 で弱すぎて質感が消えやすい）
  --aq-mode 3 --aq-strength 0.8  グラデーションのバンディングを抑える
  --deblock 1:1        線の周りのリンギングを抑える（tune animation と同じ）
  --rc-lookahead 60    カット内の静止部分に符号量を寄せる
  SAO は有効のまま（平坦部のリンギング除去に効く）

標準（standard）
  x265 の preset のまま（種類ごとの調整なし）
"""
from __future__ import annotations

import shlex

CONTENT_FILM, CONTENT_ANIME, CONTENT_STANDARD = "film", "anime", "standard"
CONTENT_LABELS = {CONTENT_FILM: "実写", CONTENT_ANIME: "アニメ", CONTENT_STANDARD: "標準（調整なし）"}


def x265_tuning(content: str, hdr: bool) -> list[str]:
    """x265 CLI 形式（--name value）の調整パラメータ"""
    if content == CONTENT_FILM:
        a = ["--aq-mode", "3", "--psy-rd", "2.0", "--psy-rdoq", "1.5", "--no-sao", "--deblock", "-1:-1",
             "--no-strong-intra-smoothing", "--bframes", "6", "--rc-lookahead", "40"]
        if hdr:
            a += ["--cbqpoffs", "-2", "--crqpoffs", "-2"]
        return a
    if content == CONTENT_ANIME:
        return ["--bframes", "8", "--psy-rd", "1.0", "--psy-rdoq", "1.0", "--aq-mode", "3", "--aq-strength", "0.8",
                "--deblock", "1:1", "--rc-lookahead", "60"]
    return []


def nvenc_tuning(content: str) -> list[str]:
    """FFmpeg hevc_nvenc のオプション"""
    base = ["-spatial-aq", "1", "-temporal-aq", "1", "-rc-lookahead", "32", "-bf", "4", "-b_ref_mode", "middle"]
    if content == CONTENT_FILM:
        return base + ["-aq-strength", "8"]
    if content == CONTENT_ANIME:
        return base + ["-aq-strength", "5"]
    return base


def split_extra(text: str) -> list[str]:
    try:
        return shlex.split(text or "")
    except ValueError:
        return (text or "").split()


def cli_to_x265_params(args: list[str]) -> list[str]:
    """x265 CLI 形式を FFmpeg の -x265-params 形式（name=value）に変換する

    --no-sao → sao=0、--hdr10 → hdr10=1、--deblock -1:-1 → deblock=-1,-1（x265-params では ':' が区切りのため）
    """
    out: list[str] = []
    i = 0
    while i < len(args):
        t = args[i]
        if not t.startswith("--"):
            i += 1
            continue
        name = t[2:]
        if "=" in name:                      # --name=value
            k, v = name.split("=", 1)
            out.append(f"{k}={v.replace(':', ',')}")
            i += 1
            continue
        nxt = args[i + 1] if i + 1 < len(args) else None
        if nxt is None or nxt.startswith("--"):
            if name.startswith("no-"):
                out.append(f"{name[3:]}=0")
            else:
                out.append(f"{name}=1")
            i += 1
        else:
            out.append(f"{name}={nxt.replace(':', ',')}")
            i += 2
    return out


def merge_cli(*groups: list[str]) -> list[str]:
    """後のグループが同じ名前のパラメータを上書きする（--no-X と --X も同じ扱い）"""
    order: list[str] = []
    vals: dict[str, list[str]] = {}

    def key(n: str) -> str:
        n = n.split("=", 1)[0]
        return n[5:] if n.startswith("--no-") else n[2:]

    for g in groups:
        i = 0
        while i < len(g):
            t = g[i]
            if not t.startswith("--"):
                i += 1
                continue
            nxt = g[i + 1] if i + 1 < len(g) else None
            item = [t] if (nxt is None or nxt.startswith("--") or "=" in t) else [t, nxt]
            k = key(t)
            if k not in vals:
                order.append(k)
            vals[k] = item
            i += len(item)
    return [x for k in order for x in vals[k]]
