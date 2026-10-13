"""Web 画面用の定義（av1an_gui: av1an で分割並列エンコード）"""
from __future__ import annotations

from pathlib import Path

from shared.core import av1an as A
from shared.web.apps import WebApp, plain

from . import encoders as E
from .config import AvSettings
from .encode import AUDIO_LABELS, CHUNK_LABELS, CROP_LABELS, RES_LABELS, SC_LABELS, AvJob, AvPipeline, video_notes
from .gui.help import TIPS


def _run(path: Path, o: dict, log, prog, cancel):
    s = AvSettings.load()
    job = AvJob(path, content=o.get("content", s.default_content), res=o.get("res", s.default_res),
                crop=o.get("crop", s.default_crop), audio=o.get("audio", s.default_audio))
    return AvPipeline(s, log, prog, cancel).run(job).output


def _probe(info) -> list[str]:
    s = AvSettings.load()
    v = info.video
    lines = []
    if v:
        n, t, how = A.resolve_workers(s.encoder, v.width, s.workers, s.threads, (s.worker_profiles or {}).get(s.encoder))
        lines.append(f"{E.ENCODER_LABELS[s.encoder].split('（')[0]} で {n} 本 × {t} スレッド（{how}）"
                     f"（CPU {A.cpu_count()} / 空きメモリ {A.available_memory_gb():.0f} GB）")
    lines += [f"注意: {x}" for x in video_notes(info)]
    return lines


def _env() -> list[str]:
    s = AvSettings.load()
    out = []
    for k in E.ENCODERS:
        ok, msg = A.encoder_status(k, (s.encoder_paths or {}).get(k, ""))
        out.append(f"av1an_gui — {E.ENCODER_LABELS[k].split('（')[0]}: {'使用可' if ok else '使用不可'}（{msg}）")
    return out


def _defaults() -> dict:
    s = AvSettings.load()
    return {"content": s.default_content, "res": s.default_res, "crop": s.default_crop, "audio": s.default_audio}


def make() -> WebApp:
    fields = [
        ["encoder", "エンコーダ", "select", "encoder", E.ENCODER_LABELS],
        ["quality", "画質", "select", "quality", E.QUALITY_LABELS],
        ["custom_q", "画質の数値（数値で指定のとき。尺度はエンコーダごと）", "number", "quality", None],
        ["speed", "速度", "select", "speed", E.SPEED_LABELS],
        ["workers", "並列数（0 = 自動）", "number", "workers", None],
        ["threads", "1 本あたりのスレッド（0 = 自動）", "number", "threads", None],
        ["target_quality", "目標画質（VMAF）を使う", "bool", "target", None],
        ["target_vmaf", "目標の VMAF", "number", "target", None],
        ["target_probes", "試行回数", "number", "probes", None],
        ["chunk_method", "分割方法", "select", "chunk", CHUNK_LABELS],
        ["sc_method", "シーン検出", "select", "sc", SC_LABELS],
        ["max_scene_sec", "1 分割の最大秒数", "number", "max_scene", None],
        ["min_scene_len", "最小の長さ（フレーム）", "number", "min_scene", None],
    ]
    for k in E.ENCODERS:
        fields.append([f"extra.{k}", f"追加パラメータ（{E.ENCODER_LABELS[k].split('（')[0]}）", "text", "extra", None])
    for k in E.ENCODERS:
        fields.append([f"encoder_paths.{k}", f"エンコーダの場所（{E.ENCODER_LABELS[k].split('（')[0]}、空欄 = 自動）",
                       "text", "encoder_path", None])
    fields += [
        ["default_content", "種類の既定", "select", "content", E.CONTENT_LABELS],
        ["default_audio", "音声の既定", "select", "audio", AUDIO_LABELS],
        ["output_dir", "保存先（空欄 = 入力と同じ）", "text", "out_dir", None],
        ["temp_dir", "作業フォルダ（空欄 = 保存先）", "text", "temp_dir", None],
        ["suffix", "ファイル名の末尾（空欄 = 自動）", "text", "suffix", None],
        ["keep_temp", "作業ファイルを残す", "bool", "keep_temp", None],
    ]
    return WebApp(
        id="av1gui", name="av1an_gui", desc="av1an で分割並列エンコード（AV1 / HEVC / H.264）",
        icon=Path(__file__).parent / "assets" / "icon.png", settings_cls=AvSettings, tips=plain(TIPS),
        job_fields=[
            {"key": "content", "label": "種類", "choices": E.CONTENT_LABELS, "tip": "content"},
            {"key": "res", "label": "解像度", "choices": RES_LABELS, "tip": "res"},
            {"key": "crop", "label": "黒帯", "choices": CROP_LABELS, "tip": "crop"},
            {"key": "audio", "label": "音声", "choices": AUDIO_LABELS, "tip": "audio"},
        ],
        job_defaults=_defaults, settings_fields=fields, assist_encoders=dict(E.ENCODER_LABELS),
        settings_note="並列数・スレッド 0 は CPU のコア数と空きメモリから自動で決めます（上限はありません）。"
                      "途中で止めても、同じファイルを同じ設定でもう一度追加すると続きから再開します。",
        run=_run, skip_re=r"_(AV1|HEVC|AVC)$", probe_extra=_probe, env_extra=_env, work_prefixes=("av1gui_",),
    )
