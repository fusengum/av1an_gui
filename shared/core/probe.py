"""入力ファイルの解析（映像・音声トラック、HDR の情報）。

ツールごとの追加の読み取りは ENRICHERS などのフックで足す。"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .tools import popen_kwargs

# ツールごとの拡張（解析結果への追加・表示名の差し替え）
ENRICHERS: list = []            # (MediaInfo) -> None
VIDEO_KIND_HOOKS: list = []     # (VideoTrack) -> 種類の表示名 or None
VIDEO_DETAIL_HOOKS: list = []   # (VideoTrack) -> 詳細の追加行
AUDIO_NAME_HOOKS: list = []     # (AudioTrack) -> 表示名 or None

@dataclass
class VideoTrack:
    index: int
    codec: str
    width: int
    height: int
    fps: str                      # "24000/1001" など
    transfer: str
    start_time: float
    primaries: str = ""
    matrix: str = ""
    pix_fmt: str = ""
    hdr10plus: bool = False
    mastering: dict = field(default_factory=dict)   # max_lum, min_lum（cd/m2）
    max_cll: int = 0
    max_fall: int = 0
    bitrate: int = 0              # bps（推定）

    @property
    def is_pq(self) -> bool:
        return self.transfer == "smpte2084"

    def describe(self) -> str:
        res = "4K" if self.width >= 3200 else ("フルHD" if self.width >= 1900 else f"{self.width}x{self.height}")
        codec = {"hevc": "HEVC", "h264": "H.264", "av1": "AV1"}.get(self.codec, self.codec)
        kind = next((k for k in (h(self) for h in VIDEO_KIND_HOOKS) if k), None)
        if kind:
            pass
        elif self.hdr10plus:
            kind = "HDR10+"
        elif self.is_pq:
            kind = "HDR10"
        elif self.transfer == "arib-std-b67":
            kind = "HLG"
        else:
            kind = "SDR"
        return f"{kind} / {res} {codec}"

    def detail(self) -> str:
        lines = [f"コーデック: {self.codec}  {self.width}x{self.height}  {self.fps} fps"]
        for h in VIDEO_DETAIL_HOOKS:
            lines += h(self)
        if self.hdr10plus:
            lines.append("HDR10+ メタデータあり")
        if self.mastering:
            lines.append(f"マスタリング: {self.mastering.get('max_lum', 0):.0f} nits / "
                         f"MaxCLL {self.max_cll}  MaxFALL {self.max_fall}")
        return "\n".join(lines)


@dataclass
class AudioTrack:
    index: int            # ffprobe stream index（= mkvmerge track ID）
    order: int            # 音声トラック内での順番（0 起点）
    codec: str
    profile: str
    channels: int
    layout: str
    sample_rate: int
    language: str
    title: str
    start_time: float
    default: bool

    def describe(self) -> str:
        name = next((n for n in (h(self) for h in AUDIO_NAME_HOOKS) if n), None) or \
            {"dts": "DTS", "flac": "FLAC", "aac": "AAC", "opus": "Opus", "mp3": "MP3", "vorbis": "Vorbis"}.get(
                self.codec, self.codec.upper())
        if self.codec == "dts" and self.profile:
            name = self.profile
        ch = {2: "2.0", 6: "5.1", 8: "7.1"}.get(self.channels, f"{self.channels}ch")
        parts = [f"#{self.order + 1}", name, ch]
        if self.language and self.language != "und":
            parts.append(self.language)
        if self.title:
            parts.append(f"「{self.title}」")
        return " ".join(parts)


@dataclass
class MediaInfo:
    path: Path
    duration: float
    audio: list[AudioTrack] = field(default_factory=list)
    has_video: bool = False
    video_start: float = 0.0
    streams: list[dict] = field(default_factory=list)
    video: VideoTrack | None = None


def probe(path: str | Path, ffprobe: str) -> MediaInfo:
    path = Path(path)
    out = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         **popen_kwargs())
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "ffprobe に失敗しました")
    data = json.loads(out.stdout)
    fmt = data.get("format", {})
    info = MediaInfo(path=path, duration=float(fmt.get("duration") or 0), streams=data.get("streams", []))

    order = 0
    for s in info.streams:
        t = s.get("codec_type")
        if t == "video" and s.get("disposition", {}).get("attached_pic", 0) == 0:
            if info.video is not None:
                continue            # 2 本目以降の映像（追加のレイヤーなど）は拡張側で扱う
            info.has_video = True
            info.video_start = float(s.get("start_time") or 0)
            info.video = _video_track(s, path, fmt, info.duration)
        elif t == "audio":
            tags = {k.lower(): v for k, v in (s.get("tags") or {}).items()}
            info.audio.append(AudioTrack(
                index=s["index"], order=order, codec=s.get("codec_name", ""), profile=s.get("profile", "") or "",
                channels=int(s.get("channels") or 0), layout=s.get("channel_layout", "") or "",
                sample_rate=int(s.get("sample_rate") or 0), language=tags.get("language", "und"),
                title=tags.get("title", ""), start_time=float(s.get("start_time") or 0),
                default=bool(s.get("disposition", {}).get("default", 0))))
            order += 1

    for e in ENRICHERS:
        e(info)
    try:
        probe_frames(info, ffprobe)
    except Exception:  # noqa: BLE001  HDR10+ 判定が取れなくても続行
        pass
    return info


def _frac(v, default=0.0) -> float:
    try:
        if isinstance(v, str) and "/" in v:
            a, b = v.split("/")
            return float(a) / float(b) if float(b) else default
        return float(v)
    except (TypeError, ValueError):
        return default


def _video_track(s: dict, path: Path, fmt: dict, duration: float) -> VideoTrack:
    tags = {k.lower(): v for k, v in (s.get("tags") or {}).items()}
    br = int(s.get("bit_rate") or tags.get("bps") or 0)
    if not br and duration:
        br = int(int(fmt.get("size") or 0) * 8 / duration * 0.9)
    v = VideoTrack(index=s["index"], codec=s.get("codec_name", ""), width=int(s.get("width") or 0),
                   height=int(s.get("height") or 0), fps=s.get("r_frame_rate") or s.get("avg_frame_rate") or "24000/1001",
                   transfer=s.get("color_transfer", "") or "", start_time=float(s.get("start_time") or 0), bitrate=br,
                   primaries=s.get("color_primaries", "") or "", matrix=s.get("color_space", "") or "",
                   pix_fmt=s.get("pix_fmt", "") or "")
    _apply_hdr_side_data(v, s.get("side_data_list", []))
    return v


def _apply_hdr_side_data(v: VideoTrack, side: list[dict]) -> None:
    for d in side:
        t = d.get("side_data_type", "")
        if t.startswith("Mastering display"):
            if d.get("max_luminance"):
                v.mastering = {"max_lum": _frac(d.get("max_luminance")), "min_lum": _frac(d.get("min_luminance"))}
                for k in ("red_x", "red_y", "green_x", "green_y", "blue_x", "blue_y", "white_point_x", "white_point_y"):
                    if d.get(k) is not None:
                        v.mastering[k] = _frac(d.get(k))
        elif t.startswith("Content light level"):
            v.max_cll = int(d.get("max_content") or 0) or v.max_cll
            v.max_fall = int(d.get("max_average") or 0) or v.max_fall
        elif "2094-40" in t or "HDR10+" in t:
            v.hdr10plus = True


def probe_frames(info: MediaInfo, ffprobe: str, n: int = 6) -> None:
    """先頭数フレームを読み、HDR10+ の有無とマスタリング情報を補う（デコードを伴うため別関数）"""
    v = info.video
    if v is None or v.codec not in ("hevc", "av1"):
        return
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", f"{v.index}", "-read_intervals", f"%+#{n}",
                          "-show_frames", "-show_entries", "frame=side_data_list", "-of", "json", str(info.path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                         **popen_kwargs())
    try:
        frames = json.loads(out.stdout or "{}").get("frames", [])
    except json.JSONDecodeError:
        return
    for fr in frames:
        _apply_hdr_side_data(v, fr.get("side_data_list", []))
