"""av1an_gui の処理本体

  1. 黒帯の検出（任意）
  2. av1an で映像だけを分割並列エンコード（SVT-AV1 / aomenc / rav1e / x265 / x264）
  3. 音声を Opus に変換（任意）
  4. mkvmerge で 映像 + 元の音声（または Opus）+ 字幕 + チャプター を 1 つの MKV にまとめる
     （HDR の色情報・マスタリング情報も MKV に書く。エンコーダが書けない場合の補い）
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from shared.core.video import CROP_AUTO, CROP_LABELS, RES_1080, RES_LABELS, detect_crop  # noqa: F401
from shared.core.tuning import CONTENT_FILM, CONTENT_LABELS, split_extra
from shared.core import av1an as A
from shared.core import probe as P
from shared.core.config import APP_ROOT, IS_WIN
from shared.core.runner import Cancelled, JobError, Runner, ascii_work_root, unique_path  # noqa: F401
from shared.core.tools import find_tool

from . import encoders as E
from .config import AvSettings

AUDIO_COPY, AUDIO_OPUS = "copy", "opus"
AUDIO_LABELS = {AUDIO_COPY: "そのまま", AUDIO_OPUS: "Opus に変換"}   # 元の音声は必ず残す（外す選択肢は置かない）
CHUNK_LABELS = {"auto": "自動", "hybrid": "hybrid（追加の準備不要）", "segment": "segment（分割ファイルを作る）",
                "lsmash": "lsmash（要 VapourSynth プラグイン）", "ffms2": "ffms2（要 VapourSynth プラグイン）",
                "bestsource": "bestsource（要 VapourSynth プラグイン）"}
SC_LABELS = {"standard": "標準（正確）", "fast": "速い"}
SPLIT_LABELS = {"auto": "自動（おすすめ）", "short": "短め（最大 5 秒。並列が効きやすい）", "standard": "標準（最大 10 秒）",
                "long": "長め（最大 20 秒。容量が少し減る）", "manual": "手動で指定"}
SPLIT_MAX_SEC = {"short": 5.0, "standard": 10.0, "long": 20.0}
OPUS_KBPS = {1: 96, 2: 160, 6: 320, 8: 448}


@dataclass
class AvJob:
    src: Path
    content: str = CONTENT_FILM
    res: str = "keep"
    crop: str = CROP_AUTO
    audio: str = AUDIO_COPY


@dataclass
class AvResult:
    output: Path | None = None
    log: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)   # 完了したが注意が要ること（入らなかったトラックなど）


def _missing(kind: str, src: list, got: list, key) -> str:
    """元にあったトラックが新しいファイルに足りなければ、その説明。key: 同じトラックとみなす目印"""
    if len(got) >= len(src):
        return ""
    left = [key(t) for t in got]
    lost = []
    for t in src:
        k = key(t)
        if k in left:
            left.remove(k)
        else:
            lost.append(t)
    lost = (lost or src)[:len(src) - len(got)]
    return (f"{kind} {len(src)} 本のうち {len(src) - len(got)} 本が新しいファイルに入りませんでした: "
            + "、".join(t.describe() for t in lost))


def check_tracks(info: P.MediaInfo, out: Path, audio: str, ffprobe: str) -> list[str]:
    """元の音声・字幕が欠けていないか、出来上がったファイルを調べる（mkvmerge は読めないトラックを
    警告だけで飛ばすため）。変換した音声は、本数と言語・タイトルで比べる"""
    got = P.probe(out, ffprobe)
    warns = []
    key = (lambda a: (a.language, a.title)) if audio == AUDIO_OPUS else (lambda a: (a.codec, a.language, a.title))
    warns.append(_missing("音声", info.audio, got.audio, key))
    warns.append(_missing("字幕", info.subtitles, got.subtitles, lambda t: (t.language, t.title)))
    return [w for w in warns if w]


def video_notes(info: P.MediaInfo) -> list[str]:
    v = info.video
    notes = []
    if v and v.hdr10plus:
        notes.append("HDR10+ の動的な明るさ情報は引き継ぎません（HDR10 として出力します）")
    return notes


def describe_plan(info: P.MediaInfo, job: AvJob, s: AvSettings) -> tuple[str, str]:
    v = info.video
    parts = [E.CODEC_OF.get(s.encoder, "")]
    if v and (v.is_pq or v.transfer == "arib-std-b67"):
        parts.append("HDR10" if v.is_pq else "HLG")
    if job.res == RES_1080 and v and v.width > 1920:
        parts.append("1080p")
    parts.append(CONTENT_LABELS.get(job.content, "").split("（")[0])
    if job.audio == AUDIO_OPUS:
        parts.append("Opus")
    detail = [f"エンコーダ: {E.ENCODER_LABELS.get(s.encoder, s.encoder)}",
              f"画質: {E.QUALITY[s.encoder][4]} {s.quality_value()}" if not s.target_quality
              else f"画質: 目標 VMAF {s.target_vmaf:g}",
              f"速度: {E.SPEED_LABELS.get(s.speed, s.speed)}",
              f"種類: {CONTENT_LABELS.get(job.content, job.content)}",
              f"音声: {AUDIO_LABELS.get(job.audio, job.audio)}"]
    detail += [f"注意: {n}" for n in video_notes(info)]
    return " ".join(p for p in parts if p), "\n".join(detail)


def split_params(s: AvSettings, info: P.MediaInfo, workers: int) -> tuple[float, int, str]:
    """分割の (最大秒数, 最小フレーム数, 説明)。
    自動: 1 つ最大 10 秒を基本に、短い映像では分割数が並列数の 4 倍以上になるよう短くする（下限 2 秒）。
    最小の長さは 1 秒分のフレーム数（短すぎる分割は切れ目の分だけ無駄が増えるため）"""
    fps = P._frac(info.video.fps if info.video else "", 24.0) or 24.0
    one_sec = max(12, min(120, round(fps)))
    if s.split == "manual":
        return float(s.max_scene_sec or 10), int(s.min_scene_len or one_sec), "手動"
    if s.split in SPLIT_MAX_SEC:
        mx = SPLIT_MAX_SEC[s.split]
        how = SPLIT_LABELS[s.split].split("（")[0]
    else:
        mx = 10.0
        if info.duration > 0 and workers > 0:
            mx = max(2.0, min(10.0, round(info.duration / (workers * 4) * 2) / 2))
        length = f"{info.duration / 60:.0f} 分" if info.duration >= 90 else f"{info.duration:.0f} 秒"
        how = f"自動: 長さ {length}・並列 {workers} 本"
    return mx, min(one_sec, max(1, int(mx * fps / 2))), how


def build_video_params(s: AvSettings, v: P.VideoTrack, content: str, out_width: int, threads: int,
                       scaled: bool) -> tuple[list[str], str]:
    enc = s.encoder
    uhd = out_width >= 3200 and not scaled
    color, note = E.color_args(enc, v, uhd)
    args = E.merge_args(E.base_args(enc, s.quality_value(), s.speed), E.thread_args(enc, threads), color,
                        E.tuning_args(enc, content, v.is_pq), split_extra((s.extra or {}).get(enc, "")))
    return args, note


class AvPipeline:
    def __init__(self, settings: AvSettings, log, progress, cancel: threading.Event | None = None):
        self.s = settings
        self.log = log
        self.progress = progress
        self.cancel = cancel or threading.Event()

    def _tool(self, name):
        p = find_tool(name, self.s)
        if not p:
            raise JobError(f"{name} が見つかりません（設定の「外部ツール」でセットアップしてください。Web 画面では「環境」タブです）")
        return p

    def _check_encoder(self, log):
        ok, msg = A.status()
        if not ok:
            raise JobError(f"av1an が使えません: {msg}（設定の「外部ツール」でセットアップしてください。Web 画面では「環境」タブです）")
        enc = self.s.encoder
        override = (self.s.encoder_paths or {}).get(enc, "")
        ok, msg = A.encoder_status(enc, override)
        if not ok and IS_WIN and not override and enc in A.EXTRA_ENCODERS:
            log(f"{enc} が使えないため取得します（{msg}）")
            try:
                A.install_encoders(lambda m, f: None, (enc,))
            except Exception as e:  # noqa: BLE001
                log(f"取得に失敗: {e}")
            ok, msg = A.encoder_status(enc)
        if not ok:
            raise JobError(f"{enc} が使えません: {msg}")
        return override

    def run(self, job: AvJob, info: P.MediaInfo | None = None) -> AvResult:
        res = AvResult()
        if job.audio not in AUDIO_LABELS:
            job.audio = AUDIO_COPY          # 以前の版の「外す」など: 元の音声は必ず残す

        def log(m):
            res.log.append(m)
            self.log(m)

        s = self.s
        ffmpeg, ffprobe, mkvmerge = self._tool("ffmpeg"), self._tool("ffprobe"), self._tool("mkvmerge")
        info = info or P.probe(job.src, ffprobe)
        v = info.video
        if v is None:
            raise JobError("映像トラックがありません")
        override = self._check_encoder(log)
        runner = Runner(log, self.cancel)

        log(f"=== {job.src.name}")
        log(f"映像: {v.describe()} → {E.CODEC_OF[s.encoder]}（{s.encoder}）")
        for n in video_notes(info):
            log(f"注意: {n}")

        out_dir = Path(s.output_dir) if s.output_dir else job.src.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        stages = [("prep", 0.03), ("scene", 0.07), ("encode", 0.83), ("audio", 0.03 if job.audio == AUDIO_OPUS else 0.0),
                  ("mux", 0.04)]
        tot = sum(w for _, w in stages)
        base, acc = {}, 0.0
        for k, w in stages:
            base[k] = (acc / tot, w / tot)
            acc += w

        def prog(stage, label, f):
            b, w = base[stage]
            self.progress(label, b + w * max(0.0, min(f, 1.0)))

        # 1. 黒帯・縮小
        crop = None
        if job.crop == CROP_AUTO:
            prog("prep", "黒帯を検出中", 0.2)
            crop = detect_crop(ffmpeg, info, runner)
            log(f"黒帯: {'検出 crop=' + crop if crop else 'なし'}")
        width = int(crop.split(":")[0]) if crop else v.width
        height = int(crop.split(":")[1]) if crop else v.height
        scale = job.res == RES_1080 and width > 1920
        out_w = 1920 if scale else width
        vf = []
        if crop:
            vf.append(f"crop={crop}")
        if scale:
            vf.append("scale=1920:-2:flags=lanczos")

        # 2. 並列数・パラメータ
        workers, threads, how = A.resolve_workers(s.encoder, out_w, s.workers, s.threads,
                                                  (s.worker_profiles or {}).get(s.encoder))
        per_mem = A.worker_cost(s.encoder, out_w)[1]
        mem = A.available_memory_gb()
        log(f"CPU {A.cpu_count()} / 空きメモリ {mem:.0f} GB → 並列 {workers} 本 × {threads} スレッド"
            f"（{A.RES_CLASS_LABELS[A.res_class(out_w)]}・{how}）")
        if mem and workers * per_mem > mem:
            log(f"警告: 並列数に対してメモリが足りない可能性があります（1 本あたり約 {per_mem} GB）")
        params, note = build_video_params(s, v, job.content, out_w, threads, scale)
        if IS_WIN:
            params = E.drop_unsupported(s.encoder, str(A.encoder_exe(s.encoder, override)), params, log)
        if note:
            log(f"注意: {note}")
        log("エンコーダの設定: " + " ".join(params))

        # 同じ入力・設定なら同じ作業フォルダ → 中断しても続きから再開できる
        key = hashlib.sha1("|".join([str(job.src.resolve()), s.encoder, " ".join(params), str(crop), str(scale),
                                     str(s.target_quality), str(s.target_vmaf)]).encode()).hexdigest()[:12]
        root = ascii_work_root(Path(s.temp_dir) if s.temp_dir else out_dir, log)
        tmp = root / f"av1gui_{key}"
        state_dir = tmp / "tmp"
        resume = (state_dir / "chunks.json").exists()
        tmp.mkdir(parents=True, exist_ok=True)
        vid = tmp / "video.mkv"
        vid.unlink(missing_ok=True)

        chunk = s.chunk_method if s.chunk_method != "auto" else ("hybrid" if IS_WIN else "lsmash")
        max_sec, min_len, split_how = split_params(s, info, workers)
        log(f"分割: 1 つ最大 {max_sec:g} 秒・最小 {min_len} フレーム（{split_how}）")
        common = ["-e", s.encoder, "-c", "mkvmerge", "-w", str(workers), "--pix-format", "yuv420p10le",
                  "--audio-params=-an", "-y", "--keep", "--sc-method", s.sc_method,
                  "--extra-split-sec", f"{max_sec:g}", "--min-scene-len", str(min_len),
                  "--video-params=" + " ".join(params)]
        if height > 1080 and not scale:
            common += ["--sc-downscale-height", "540"]
        pn = E.photon_noise(s.encoder, job.content)
        if pn:
            common += ["--photon-noise", str(pn)]
        if vf:
            common.append("--ffmpeg=-vf " + ",".join(vf))
        if s.target_quality:
            t = float(s.target_vmaf)
            common += [f"--target-quality={t - 0.5:g}-{t + 0.5:g}", "--probes", str(s.target_probes),
                       "--probe-video-params=" + " ".join(E.strip_for_probe(
                           E.merge_args(params, E.base_args(s.encoder, s.quality_value(), "fast"))))]
            if vf:
                common.append("--vmaf-filter=" + ",".join(vf))
            log(f"目標画質: VMAF {t:g}（チャンクごとに画質の値を探します。通常より時間がかかります）")
        if resume:
            common.append("--resume")
            log("前回の途中から再開します（av1an）")

        if IS_WIN:
            cmd = [str(A.av1an_exe()), "-i", info.path, "-o", vid, "--temp", state_dir, "-m", chunk,
                   *common]
            env = A.windows_env()
            if override:
                d = Path(override)
                d = d.parent if d.suffix.lower() == ".exe" else d
                env["PATH"] = str(d) + os.pathsep + env["PATH"]
                log(f"エンコーダ: {A.encoder_exe(s.encoder, override)}")
        else:
            uid = f"{os.getuid()}:{os.getgid()}"
            name = f"av1gui_{key}"
            subprocess.run([A.docker_bin(), "rm", "-f", name], capture_output=True)
            cmd = [A.docker_bin(), "run", "--rm", "--name", name, "--user", uid,
                   "--label", f"av1an_gui.root={APP_ROOT}",          # アンインストールで、このフォルダのものだけを止める
                   "-v", f"{info.path.parent}:/in:ro", "-v", f"{tmp}:/work", A.DOCKER_IMAGE,
                   "-i", f"/in/{info.path.name}", "-o", "/work/video.mkv", "--temp", "/work/tmp", "-m", chunk,
                   *common]
            env = None
            runner.on_cancel = lambda: subprocess.run([A.docker_bin(), "kill", name], capture_output=True)
        log(f"av1an: {s.encoder} / 分割 {chunk} / 並列 {workers}")
        try:
            reader = A.input_reader() if IS_WIN else A.input_reader(name, f"/in/{info.path.name}")
            A.run_av1an(runner, cmd, env, state_dir, workers, lambda lbl, f: prog("encode", lbl, f), vid, log,
                        sc_prog=lambda lbl, f: prog("scene", lbl, f), src_size=info.path.stat().st_size,
                        reader=reader)
        except RuntimeError as e:
            raise JobError(str(e)) from None

        # 3. 音声
        opus = None
        if job.audio == AUDIO_OPUS:
            opus = self._opus(runner, ffmpeg, info, tmp, lambda lbl, f: prog("audio", lbl, f), log)

        # 4. まとめる
        suffix = s.suffix or f"_{E.CODEC_OF[s.encoder]}"
        out = unique_path(out_dir / f"{job.src.stem}{suffix}.mkv")
        prog("mux", "MKV を作成中", 0.1)
        self._mux(runner, mkvmerge, info, vid, opus, job.audio, out)
        res.output = out
        for w in check_tracks(info, out, job.audio, ffprobe):
            res.warnings.append(w)
            log(f"警告: {w}")
        if not s.keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)
        a, b = info.path.stat().st_size, out.stat().st_size
        log(f"完了: {out}（{a / 1e9:.1f} GB → {b / 1e9:.1f} GB）")
        prog("mux", "完了", 1.0)
        return res

    def _opus(self, runner, ffmpeg, info, tmp: Path, prog, log) -> Path | None:
        auds = [x for x in info.streams if x.get("codec_type") == "audio"]
        if not auds:
            return None
        out = tmp / "audio_opus.mka"
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", info.path, "-map", "0:a",
               "-c:a", "libopus", "-vbr", "on", "-mapping_family", "1"]
        desc = []
        for i, a in enumerate(auds):
            ch = int(a.get("channels") or 2)
            kbps = OPUS_KBPS.get(ch, ch * 56)
            cmd += [f"-b:a:{i}", f"{kbps}k", f"-filter:a:{i}", "aformat=channel_layouts=7.1|5.1|stereo|mono"]
            desc.append(f"{ch}ch {kbps}kbps")
        cmd.append(out)
        log("音声を Opus に変換: " + ", ".join(desc))
        prog("音声を変換中", 0.2)
        runner.run(cmd)
        prog("音声を変換中", 1.0)
        return out if out.exists() else None

    def _mux(self, runner, mkvmerge, info: P.MediaInfo, vid: Path, opus: Path | None, audio: str, out: Path):
        v = info.video
        cmd = [mkvmerge, "--gui-mode", "-o", out]
        if v.is_pq or v.transfer == "arib-std-b67":
            cmd += ["--colour-matrix-coefficients", "0:9", "--colour-range", "0:1",
                    "--colour-transfer-characteristics", f"0:{16 if v.is_pq else 18}", "--colour-primaries", "0:9"]
            m = v.mastering
            if v.max_cll:
                cmd += ["--max-content-light", f"0:{v.max_cll}", "--max-frame-light", f"0:{v.max_fall}"]
            if m and all(k in m for k in ("red_x", "red_y", "green_x", "green_y", "blue_x", "blue_y")):
                cmd += ["--chromaticity-coordinates",
                        f"0:{m['red_x']},{m['red_y']},{m['green_x']},{m['green_y']},{m['blue_x']},{m['blue_y']}"]
            if m and "white_point_x" in m:
                cmd += ["--white-colour-coordinates", f"0:{m['white_point_x']},{m['white_point_y']}"]
            if m and m.get("max_lum"):
                cmd += ["--max-luminance", f"0:{m['max_lum']}", "--min-luminance", f"0:{m.get('min_lum', 0.005)}"]
        cmd += ["--no-audio", "--no-subtitles", "--no-chapters", "--no-attachments", "--no-global-tags", vid]
        if opus:
            cmd += [opus]
        src = ["--no-video"]
        if audio != AUDIO_COPY:
            src.append("--no-audio")
        cmd += [*src, info.path]
        rc, = runner.wait(runner.start(cmd, on_line=lambda ln: ln.startswith("#GUI#progress")))  # 警告だけなら 1
        if rc not in (0, 1) or not out.exists():
            raise JobError("MKV の作成に失敗しました")
