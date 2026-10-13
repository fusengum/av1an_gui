"""av1an_gui の設定（ツールの置き場所は共通設定と共有）"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields

from shared.core.config import APP_ROOT

SETTINGS_PATH = APP_ROOT / "settings_av1gui.json"


@dataclass
class AvSettings:
    ffmpeg: str = ""
    ffprobe: str = ""
    mkvmerge: str = ""

    output_dir: str = ""
    temp_dir: str = ""
    suffix: str = ""                 # 空 = 自動（_AV1 / _HEVC / _AVC）

    encoder: str = "svt-av1"
    quality: str = "standard"        # high | standard | small | custom
    custom_q: int = 25               # quality=custom のときの値（エンコーダごとの尺度）
    speed: str = "slow"
    workers: int = 0                 # 0 = 自動（CPU 数とメモリから）
    threads: int = 0                 # 1 本あたりのスレッド（0 = 自動: 全コア ÷ 並列数）
    worker_profiles: dict = field(default_factory=dict)  # 並列数アシストの結果: エンコーダ → 4k / hd → {workers, threads}
    assist_answers: dict = field(default_factory=dict)   # 並列数アシストの回答（次回の初期値）
    extra: dict = field(default_factory=dict)          # エンコーダ → 追加パラメータ
    encoder_paths: dict = field(default_factory=dict)  # エンコーダ → 実行ファイル（空 = 自動取得したもの）

    # 目標画質（av1an の --target-quality）
    target_quality: bool = False
    target_vmaf: float = 95.0
    target_probes: int = 4

    # 分割
    split: str = "auto"              # auto | short | standard | long | manual（manual のときだけ下の 2 つを使う）
    chunk_method: str = "auto"       # auto | hybrid | segment | lsmash | ffms2 | bestsource
    sc_method: str = "standard"      # standard | fast
    max_scene_sec: float = 10.0      # 1 チャンクの最大秒数（av1an の --extra-split-sec）
    min_scene_len: int = 24

    keep_temp: bool = False
    default_content: str = "film"    # film | anime | standard
    default_res: str = "keep"        # keep | 1080p
    default_crop: str = "auto"       # auto | none
    default_audio: str = "copy"      # copy | opus

    window_geometry: str = ""
    recent_dir: str = ""
    show_log: bool = False

    @classmethod
    def load(cls) -> "AvSettings":
        s = cls()
        if SETTINGS_PATH.exists():
            try:
                data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                names = {f.name for f in fields(cls)}
                for k, v in data.items():
                    if k in names:
                        setattr(s, k, v)
                if s.default_audio not in ("copy", "opus"):
                    s.default_audio = "copy"     # 以前の版の「外す」（none）は無くした
                if "split" not in data:
                    # 以前の版: 既定（10 秒・24 フレーム）のままなら自動に、変えていたら手動のまま使う
                    changed = float(data.get("max_scene_sec", 10)) != 10 or int(data.get("min_scene_len", 24)) != 24
                    s.split = "manual" if changed else "auto"
            except Exception:
                pass
        return s

    def save(self) -> None:
        try:
            SETTINGS_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def quality_value(self) -> int:
        from .encoders import quality_value
        return quality_value(self.encoder, self.quality, self.custom_q)
