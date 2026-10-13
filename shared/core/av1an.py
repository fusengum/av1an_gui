"""av1an の導入と実行環境

Windows: tools/av1an/ に av1an.exe と各エンコーダ（MSYS2 パッケージ: x265 / SVT-AV1 / aomenc / rav1e、
         x264 は VideoLAN の配布物）を置く。
         VapourSynth は仮想環境に pip で入っている前提（requirements.txt）。
Linux  : 公式 Docker イメージ masterofzen/av1an:master を使う（x265・VapourSynth・プラグイン同梱）。
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from .config import IS_WIN, TOOLS_DIR
from .tools import ProgressCB, _download, _open, popen_kwargs

AV1AN_DIR = TOOLS_DIR / "av1an"
DOCKER_IMAGE = "masterofzen/av1an:master"
AV1AN_EXE_URL = "https://github.com/rust-av/Av1an/releases/download/latest/av1an.exe"
# 現在の av1an.exe は FFmpeg をライブラリとして持たない（ffmpeg.exe を呼ぶ）。
# 古いビルドで FFmpeg の DLL が必要だった場合だけ、手元にある版から順に試す
FFMPEG_SHARED_PATTERN = re.compile(r"ffmpeg-n(\d+\.\d+)-latest-win64-gpl-shared-[\d.]+\.zip")
MSYS2_REPO = "https://mirror.msys2.org/mingw/ucrt64/"
MSYS2_PREFIX = "mingw-w64-ucrt-x86_64-"
MSYS2_ROOT_PKG = "x265"            # ここから依存パッケージ（libstdc++ など）をたどって DLL をそろえる
X264_INDEX = "https://artifacts.videolan.org/x264/release-win64/"

# av1an の -e 名 → (MSYS2 パッケージ名 or None, 実行ファイル名, 起動確認の引数)
ENCODER_BINS = {
    "svt-av1": ("svt-av1", "SvtAv1EncApp.exe", ["--version"]),
    "aom": ("aom", "aomenc.exe", ["--help"]),
    "rav1e": ("rav1e", "rav1e.exe", ["--version"]),
    "x265": ("x265", "x265.exe", ["--version"]),
    "x264": (None, "x264.exe", ["--version"]),
}
EXTRA_ENCODERS = ("svt-av1", "aom", "rav1e", "x264")   # av1an_gui 用（x265 は av1an 本体と一緒に入れる）


# ------------------------------------------------------------------ 検出

def docker_bin() -> str | None:
    return shutil.which("docker")


def docker_image_ready() -> bool:
    d = docker_bin()
    if not d:
        return False
    try:
        r = subprocess.run([d, "image", "inspect", DOCKER_IMAGE], capture_output=True, timeout=20)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def windows_env() -> dict:
    """av1an.exe を動かすための環境変数（VapourSynth・x265・mkvmerge を見つけられるように）"""
    env = dict(os.environ)
    paths = [str(AV1AN_DIR), str(TOOLS_DIR / "mkvtoolnix"), str(TOOLS_DIR / "bin")]
    try:
        import vapoursynth  # noqa: F401
        vs_file = Path(vapoursynth.__file__)
        site = vs_file.parent if vs_file.parent.name == "site-packages" else vs_file.parent.parent
        paths += [str(vs_file.parent), str(site)]
        # av1an が埋め込みで起動する Python からも、仮想環境の vapoursynth が見えるように
        env["PYTHONPATH"] = os.pathsep.join([str(site), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    except Exception:  # noqa: BLE001
        pass
    paths.append(sys.base_prefix)
    env["PATH"] = os.pathsep.join(paths + [env.get("PATH", "")])
    return env


def status() -> tuple[bool, str]:
    """(使用可能か, 説明)"""
    if IS_WIN:
        exe = av1an_exe()
        if not exe.exists():
            return False, "未導入"
        try:
            r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30,
                               env=windows_env(), errors="replace", **popen_kwargs())
            if r.returncode == 0:
                ok, msg = x265_status()
                if not ok:
                    return False, msg
                return True, (r.stdout.strip().splitlines() or ["av1an"])[0]
            code = r.returncode & 0xFFFFFFFF
            if code == STATUS_DLL_NOT_FOUND:
                return False, "必要な DLL（VapourSynth など）が見つかりません"
            return False, "起動できません: " + (r.stderr.strip()[-200:] or f"code {code:#x}")
        except Exception as e:  # noqa: BLE001
            return False, f"起動できません: {e}"
    if not docker_bin():
        return False, "Docker がありません（install.sh で導入してください）"
    if docker_image_ready():
        return True, f"Docker: {DOCKER_IMAGE}"
    return False, "Docker イメージ未取得"


# ------------------------------------------------------------------ 導入

STATUS_DLL_NOT_FOUND = 0xC0000135


def _local_or_path(name: str) -> Path:
    """tools/av1an にあればそれ、無ければ PATH にあるもの（PC に入っている版を使い、入れ直さない）"""
    local = AV1AN_DIR / name
    if local.exists():
        return local
    found = shutil.which(name)
    return Path(found) if found else local


def av1an_exe() -> Path:
    return _local_or_path("av1an.exe" if IS_WIN else "av1an")


def encoder_exe(enc: str, override: str = "") -> Path:
    """エンコーダの実行ファイル。override は利用者が指定した実行ファイルかフォルダ（SVT-AV1-PSY など）"""
    name = ENCODER_BINS[enc][1]
    if override:
        o = Path(override)
        return o if o.suffix.lower() == ".exe" else o / name
    return _local_or_path(name)


def encoder_status(enc: str, override: str = "") -> tuple[bool, str]:
    """Windows でエンコーダが起動できるか（Linux は Docker イメージに同梱）"""
    if not IS_WIN:
        return (True, "Docker イメージに同梱") if docker_image_ready() else (False, "Docker イメージ未取得")
    exe = encoder_exe(enc, override)
    if not exe.exists():
        return False, f"{exe.name} がありません"
    if exe.name.lower() != ENCODER_BINS[enc][1].lower():
        return False, f"av1an は {ENCODER_BINS[enc][1]} という名前で探すため、その名前にしてください"
    try:
        r = subprocess.run([str(exe), *ENCODER_BINS[enc][2]], capture_output=True, text=True, timeout=30,
                           cwd=str(exe.parent), errors="replace", **popen_kwargs())
    except Exception as e:  # noqa: BLE001
        return False, f"起動できません: {e}"
    code = r.returncode & 0xFFFFFFFF
    if code == STATUS_DLL_NOT_FOUND:
        return False, "実行に必要な DLL が足りません（再導入が必要です）"
    out = (r.stdout + r.stderr).strip()
    if not out:
        return False, f"起動できません（code {code:#x}）"
    first = next((ln for ln in out.splitlines() if ln.strip()), "")
    return True, first[:80]


def _runs(exe: Path, args: list[str]) -> bool:
    if not exe.exists():
        return False
    try:
        r = subprocess.run([str(exe), *args], capture_output=True, timeout=30, env=windows_env() if IS_WIN else None,
                           **popen_kwargs())
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def x265_status() -> tuple[bool, str]:
    return encoder_status("x265")


def _decompress_any(data: bytes) -> bytes:
    if data[:4] == b"\x28\xb5\x2f\xfd":
        import zstandard
        return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)).read()
    if data[:2] == b"\x1f\x8b":
        import gzip
        return gzip.decompress(data)
    if data[:6] == b"\xfd7zXZ\x00":
        import lzma
        return lzma.decompress(data)
    return data


def _parse_pacman_db(data: bytes) -> dict[str, dict]:
    """pacman のリポジトリ DB（tar）から {名前: {filename, depends}}。provides も名前として登録する"""
    pkgs: dict[str, dict] = {}
    with tarfile.open(fileobj=io.BytesIO(_decompress_any(data)), mode="r:") as t:
        for m in t:
            if not (m.isfile() and m.name.endswith("/desc")):
                continue
            sec, info = None, {"depends": [], "provides": []}
            for line in t.extractfile(m).read().decode("utf-8", "replace").splitlines():
                if line.startswith("%") and line.endswith("%"):
                    sec = line.strip("%").lower()
                elif line and sec:
                    if sec in ("depends", "provides"):
                        info[sec].append(re.split(r"[<>=]", line)[0])
                    elif sec in ("name", "filename"):
                        info[sec] = line
            if "name" in info and "filename" in info:
                pkgs[info["name"]] = info
                for pv in info["provides"]:
                    pkgs.setdefault(pv, info)
    return pkgs


def _msys2_closure(db: dict[str, dict], root: str) -> list[dict]:
    """root パッケージと、その実行時依存をすべて（MSYS2 は gcc-libs → cc-libs → libstdc++ のように分割されている）"""
    out, seen, todo = [], set(), [root]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        info = db.get(name)
        if not info:
            continue
        if info["filename"] not in {o["filename"] for o in out}:
            out.append(info)
        todo += [d for d in info["depends"] if d.startswith(MSYS2_PREFIX)]
    return out


def _extract_zst_bin(data: bytes, dst: Path, exes=("x265.exe",)) -> list[str]:
    import zstandard  # requirements.txt（Windows）
    raw = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data))
    got = []
    with tarfile.open(fileobj=raw, mode="r|") as t:
        for m in t:
            p = Path(m.name)
            if m.isfile() and len(p.parts) >= 2 and p.parts[-2] == "bin" and p.suffix.lower() in (".exe", ".dll"):
                if p.suffix.lower() == ".exe" and p.name.lower() not in {e.lower() for e in exes}:
                    continue
                f = t.extractfile(m)
                (dst / p.name).write_bytes(f.read())
                got.append(p.name)
    return got


def install(cb: ProgressCB) -> list[Path]:
    if not IS_WIN:
        d = docker_bin()
        if not d:
            raise RuntimeError("Docker がありません。install.sh を実行して導入してください")
        cb(f"av1an の Docker イメージを取得中（{DOCKER_IMAGE}、数 GB）…", -1)
        r = subprocess.run([d, "pull", DOCKER_IMAGE], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("docker pull に失敗しました: " + r.stderr.strip()[-300:])
        cb("完了", 1.0)
        return []

    AV1AN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # 既に動く av1an.exe（tools/av1an か PATH）があれば取り直さない
        if not _runs(av1an_exe(), ["--version"]):
            _download(AV1AN_EXE_URL, AV1AN_DIR / "av1an.exe", cb, "av1an")

        if not x265_status()[0]:
            _install_msys2(["x265"], td, cb)
        ok, msg = x265_status()
        if not ok:
            raise RuntimeError(msg)

        ok, msg = status()
        if ok:
            cb("完了", 1.0)
            return [av1an_exe()]
        first = msg

        # 起動できない場合: 古いビルドが FFmpeg の DLL を必要としている可能性。配布中の版を新しい順に試す
        try:
            with _open("https://github.com/BtbN/FFmpeg-Builds/releases/expanded_assets/latest") as r:
                page = r.read().decode("utf-8", "replace")
            names = sorted(set(FFMPEG_SHARED_PATTERN.findall(page)), key=lambda v: [int(x) for x in v.split(".")],
                           reverse=True)
            files = [m.group(0) for v in names for m in [FFMPEG_SHARED_PATTERN.search(page[page.find(f"ffmpeg-n{v}-"):])]
                     if m]
        except Exception:  # noqa: BLE001
            files = []
        import zipfile
        for fn in files:
            try:
                arc = _download(f"https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/{fn}", td / fn,
                                cb, "FFmpeg 共有ライブラリ")
            except Exception:  # noqa: BLE001
                continue
            added = []
            with zipfile.ZipFile(arc) as z:
                for info in z.infolist():
                    p = Path(info.filename)
                    if p.suffix.lower() == ".dll" and "bin" in p.parts:
                        (AV1AN_DIR / p.name).write_bytes(z.read(info))
                        added.append(AV1AN_DIR / p.name)
            ok, msg = status()
            if ok:
                cb("完了", 1.0)
                return [av1an_exe()]
            for a in added:   # 効かなかった DLL は残さない
                a.unlink(missing_ok=True)
        raise RuntimeError(f"av1an を起動できません（{first}）")


def _install_msys2(encs: list[str], td: Path, cb: ProgressCB) -> None:
    cb("エンコーダを取得中（MSYS2）…", -1)
    dbf = _download(MSYS2_REPO + "ucrt64.db", td / "ucrt64.db", cb, "MSYS2 パッケージ一覧")
    db = _parse_pacman_db(dbf.read_bytes())
    exes = [ENCODER_BINS[e][1] for e in encs]
    done = set()
    for e in encs:
        pkgs = _msys2_closure(db, MSYS2_PREFIX + ENCODER_BINS[e][0])
        if not pkgs:
            raise RuntimeError(f"MSYS2 に {e} が見つかりません")
        for info in pkgs:
            fn = info["filename"]
            if fn in done:
                continue
            done.add(fn)
            pkg = _download(MSYS2_REPO + fn, td / fn, cb, info["name"].removeprefix(MSYS2_PREFIX))
            _extract_zst_bin(pkg.read_bytes(), AV1AN_DIR, exes)


def _install_x264(td: Path, cb: ProgressCB) -> None:
    """VideoLAN の配布物（単体で動く x264.exe）。MSYS2 版は FFmpeg 一式に依存して大きすぎるため"""
    with _open(X264_INDEX) as r:
        page = r.read().decode("utf-8", "replace")
    found = re.findall(r'href="(x264-r(\d+)-[0-9a-f]+\.exe)"', page)
    if not found:
        raise RuntimeError("x264 の配布物が見つかりません")
    fn = max(found, key=lambda t: int(t[1]))[0]
    _download(X264_INDEX + fn, AV1AN_DIR / "x264.exe", cb, "x264")


def install_encoders(cb: ProgressCB, encs=EXTRA_ENCODERS) -> list[Path]:
    """av1an_gui 用のエンコーダ（Windows）。Linux は Docker イメージに同梱なのでイメージを取得する"""
    if not IS_WIN:
        return install(cb)
    AV1AN_DIR.mkdir(parents=True, exist_ok=True)
    errors = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # 既に動くもの（tools/av1an か PATH）は取り直さない
        need = [e for e in encs if not encoder_status(e)[0]]
        msys = [e for e in need if ENCODER_BINS[e][0]]
        if msys:
            try:
                _install_msys2(msys, td, cb)
            except Exception as e:  # noqa: BLE001
                errors.append(f"MSYS2: {e}")
        if "x264" in need:
            try:
                _install_x264(td, cb)
            except Exception as e:  # noqa: BLE001
                errors.append(f"x264: {e}")
    bad = [f"{e}: {m}" for e in encs for ok, m in [encoder_status(e)] if not ok]
    if bad:
        raise RuntimeError(" / ".join(bad + errors))
    cb("完了", 1.0)
    return [encoder_exe(e) for e in encs]


def encoders_status() -> tuple[bool, str]:
    """av1an_gui 用エンコーダがそろっているか（環境チェック用）"""
    if not IS_WIN:
        return status()
    res = {e: encoder_status(e) for e in EXTRA_ENCODERS}
    bad = [e for e, (ok, _) in res.items() if not ok]
    if bad:
        return False, "未導入: " + ", ".join(bad)
    return True, " / ".join(e for e in EXTRA_ENCODERS)


# ------------------------------------------------------------------ 実行用の補助

def cpu_count() -> int:
    """このプロセスが使える CPU 数（Docker / cgroup の制限も考慮）"""
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 4


# エンコーダ別の 1 本あたりの目安: (4K のスレッド, 4K のメモリ GB, 1080p 以下のスレッド, 1080p 以下のメモリ GB)
WORKER_COST = {
    "x265": (4, 3.5, 3, 1.5),
    "svt-av1": (8, 7.0, 4, 2.5),      # SVT-AV1 は 1 本で多くのスレッドを使え、メモリも多く使う
    "aom": (4, 3.0, 3, 1.2),
    "rav1e": (4, 3.0, 3, 1.2),
    "x264": (4, 2.0, 3, 0.8),
}


def worker_cost(enc: str, width: int) -> tuple[int, float]:
    c = WORKER_COST.get(enc, WORKER_COST["x265"])
    return (c[0], c[1]) if width >= 3200 else (c[2], c[3])


def auto_workers(width: int, enc: str = "x265") -> int:
    """CPU 数と空きメモリから並列数を決める。

    1 本に多くのスレッドを持たせるより、適度なスレッド数で本数を増やす方が速い（エンコーダごとに目安が違う）。"""
    cores = cpu_count()
    per_core, per_mem = worker_cost(enc, width)
    mem = available_memory_gb()
    # 上限は設けない（コア数とメモリだけで決まる。16 コアでも 128 コア以上でも同じ考え方で増減する）。
    # 空きメモリは 1 割を OS・デコード・av1an 本体のために残す
    by_mem = int(mem * 0.9 // per_mem) if mem else max(1, cores // per_core // 2)
    return max(1, min(cores // per_core, by_mem))


def threads_per_worker(workers: int) -> int:
    """余ったコアも無駄にしないよう、全コアを本数で割ったスレッド数を各ワーカーに渡す"""
    return max(1, cpu_count() // max(1, workers))


def svt_lp(threads: int) -> int:
    """SVT-AV1 の --lp（並列度 1〜6）を、1 本あたりのスレッド数から決める"""
    for lp, limit in ((1, 2), (2, 4), (3, 8), (4, 16), (5, 32)):
        if threads <= limit:
            return lp
    return 6


def res_class(width: int) -> str:
    """並列数の設定を分ける解像度の区分（4K かそれ以下か）"""
    return "4k" if width >= 3200 else "hd"


RES_CLASS_LABELS = {"4k": "4K", "hd": "1080p 以下"}
CPU_USE = {"all": ("すべて使う（エンコード専用）", 0.0), "most": ("少し残す（1 割ほど他の作業用に空ける）", 0.1),
           "half": ("半分だけ使う（他の作業や VM と並行）", 0.5)}


def plan_workers(enc: str, width: int, cores: int, mem_gb: float, cpu_use: str = "all",
                 mem_reserve_gb: float | None = None) -> dict:
    """CPU 数・メモリ・解像度から並列数とスレッド数を決める（アシスト用）

    cores / mem_gb はこの PC の論理プロセッサ数と搭載メモリ。cpu_use で他の作業用に CPU を残し、
    mem_reserve_gb（省略時は 1 割、最低 4 GB）を OS や他のアプリ用に残す。"""
    frac = CPU_USE.get(cpu_use, CPU_USE["all"])[1]
    use_cores = max(1, cores - (max(1, round(cores * frac)) if frac else 0))
    reserve = mem_reserve_gb if mem_reserve_gb is not None else max(4.0, mem_gb * 0.1)
    use_mem = max(1.0, mem_gb - reserve)
    per_core, per_mem = worker_cost(enc, width)
    by_cpu = max(1, use_cores // per_core)
    by_mem = max(1, int(use_mem // per_mem))
    workers = min(by_cpu, by_mem)
    threads = max(1, use_cores // workers)
    return {"workers": workers, "threads": threads, "use_cores": use_cores, "use_mem": round(use_mem, 1),
            "mem_need": round(workers * per_mem, 1), "limited_by": "memory" if by_mem < by_cpu else "cpu",
            "short": workers * per_mem > use_mem,
            "per_core": per_core, "per_mem": per_mem}


def resolve_workers(enc: str, width: int, workers: int = 0, threads: int = 0, profiles: dict | None = None) -> tuple[int, int, str]:
    """実際に使う (並列数, スレッド, 決め方)。手動指定 → アシストで決めた解像度別の値 → 自動 の順"""
    if workers:
        return workers, threads or threads_per_worker(workers), "手動"
    prof = (profiles or {}).get(res_class(width))
    if prof and prof.get("workers"):
        return int(prof["workers"]), int(prof.get("threads") or threads_per_worker(int(prof["workers"]))), "アシスト"
    n = auto_workers(width, enc)
    return n, threads or threads_per_worker(n), "自動"


def total_memory_gb() -> float:
    try:
        if IS_WIN:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 1e9
        for line in open("/proc/meminfo"):
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 1e6
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def available_memory_gb() -> float:
    try:
        if IS_WIN:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullAvailPhys / 1e9
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1e6
    except Exception:  # noqa: BLE001
        pass
    return 0.0


# ---------------------------------------------------------------- 進み具合の取得
# av1an はパイプにつないだ stderr には進捗バーを出さない（端末のときだけ描く）。
# シーン検出: 入力ファイルをどこまで読んだかを OS から調べる。
# エンコード: done.json はチャンクが終わったときしか増えないため、作業中のチャンクの出力サイズからも見積もる。

def _fmt_time(sec: float) -> str:
    sec = int(max(sec, 0))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def _win_read_bytes(root_pid: int, names=("ffmpeg.exe", "vspipe.exe")) -> int | None:
    """root_pid の子孫のうち、入力を読むプロセス（ffmpeg / vspipe）の読み込み量。複数あれば少ない方
    （フレーム数を数えるだけの速い方ではなく、シーン検出で読んでいる方）"""
    import ctypes
    from ctypes import wintypes

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                      "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.OpenProcess.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)              # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return None
    procs = []
    try:
        e = PE()
        e.dwSize = ctypes.sizeof(PE)
        ok = k32.Process32FirstW(wintypes.HANDLE(snap), ctypes.byref(e))
        while ok:
            procs.append((e.th32ProcessID, e.th32ParentProcessID, e.szExeFile.lower()))
            ok = k32.Process32NextW(wintypes.HANDLE(snap), ctypes.byref(e))
    finally:
        k32.CloseHandle(wintypes.HANDLE(snap))
    tree, todo = set(), [root_pid]
    while todo:
        cur = todo.pop()
        for pid, ppid, _ in procs:
            if ppid == cur and pid not in tree and pid != root_pid:
                tree.add(pid)
                todo.append(pid)
    best = None
    for pid, _, name in procs:
        if pid not in tree or name not in names:
            continue
        h = k32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            io = IO()
            if k32.GetProcessIoCounters(wintypes.HANDLE(h), ctypes.byref(io)):
                best = io.ReadTransferCount if best is None else min(best, io.ReadTransferCount)
        finally:
            k32.CloseHandle(wintypes.HANDLE(h))
    return best


def _win_own_read_bytes(pid: int) -> int | None:
    """そのプロセス自身の読み込み量（Windows）"""
    import ctypes
    from ctypes import wintypes

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                      "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return None
    try:
        io = IO()
        return io.ReadTransferCount if k32.GetProcessIoCounters(wintypes.HANDLE(h), ctypes.byref(io)) else None
    finally:
        k32.CloseHandle(wintypes.HANDLE(h))


def _dir_bytes(d: Path) -> int:
    try:
        return sum(f.stat().st_size for f in d.iterdir() if f.is_file())
    except OSError:
        return 0


def _linux_read_pos(root_pid: int, target: str) -> int | None:
    """root_pid とその子孫が開いている target（コンテナ内のパス）の読み位置。複数あれば小さい方"""
    kids: dict[int, list[int]] = {}
    for e in os.listdir("/proc"):
        if not e.isdigit():
            continue
        try:
            with open(f"/proc/{e}/stat") as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        kids.setdefault(ppid, []).append(int(e))
    best, todo = None, [root_pid]
    while todo:
        pid = todo.pop()
        todo += kids.get(pid, [])
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(f"/proc/{pid}/fd/{fd}") != target:
                    continue
                with open(f"/proc/{pid}/fdinfo/{fd}") as f:
                    pos = next(int(x.split()[1]) for x in f if x.startswith("pos:"))
                best = pos if best is None else min(best, pos)
            except (OSError, ValueError, StopIteration):
                continue
    return best


def input_reader(container: str | None = None, inner_path: str = ""):
    """シーン検出で入力を読んだバイト数を返す関数 f(av1an のプロセス) -> int | None を作る。
    Windows は子プロセス（ffmpeg / vspipe）の読み込み量、Linux（Docker）はコンテナ内での読み位置"""
    if IS_WIN:
        return lambda p: _win_read_bytes(p.pid)
    pid = [0]

    def f(_p):
        if not pid[0]:
            r = subprocess.run([docker_bin(), "inspect", "-f", "{{.State.Pid}}", container],
                               capture_output=True, text=True, timeout=10)
            pid[0] = int(r.stdout.strip() or 0) if r.returncode == 0 else 0
            if not pid[0]:
                return None
        return _linux_read_pos(pid[0], inner_path)
    return f


def _chunk_files(enc_dir: Path) -> dict[str, int]:
    """作業フォルダ encode/ のチャンク出力（名前 → サイズ）"""
    out = {}
    try:
        for f in enc_dir.iterdir():
            if f.is_file():
                out[f.stem] = out.get(f.stem, 0) + f.stat().st_size
    except OSError:
        pass
    return out


def _done_frames(v) -> int:
    """done.json の 1 チャンク分。古い av1an は数値、新しい av1an は {"frames": n, "size_bytes": n}"""
    if isinstance(v, dict):
        return int(v.get("frames") or 0)
    return int(v or 0)


def _done_bytes(v) -> int:
    return int(v.get("size_bytes") or 0) if isinstance(v, dict) else 0


def _chunk_count(chunks_json: Path) -> int:
    import json
    try:
        d = json.loads(chunks_json.read_text())
    except (OSError, ValueError):
        return 0
    if isinstance(d, dict):
        d = next((v for v in d.values() if isinstance(v, list)), [])
    return len(d) if isinstance(d, list) else 0


def run_av1an(runner, cmd: list, env, state_dir: Path, workers: int, prog, out: Path, log=None,
              sc_prog=None, src_size: int = 0, reader=None) -> None:
    """av1an を実行し、進捗を出す。失敗したら RuntimeError
    - シーン検出: stderr の「Scene detection」で始まりを知り、reader（input_reader）で入力の読み具合を測る → sc_prog
    - エンコード: done.json（終わったチャンク）＋作業中のチャンクの出力サイズからの見積もり → prog"""
    import json
    import threading
    import time

    done_json = state_dir / "done.json"
    scenes_json = state_dir / "scenes.json"
    chunks_json = state_dir / "chunks.json"
    t0 = time.time()
    # av1an は起動してすぐ（シーン検出の前）に空の done.json を作る。エンコードが始まったかは、シーン検出の後に
    # 作る chunks.json で見る。再開（--resume で chunks.json と done.json が残っている）ならシーン検出はしない
    resumed = "--resume" in [str(c) for c in cmd] and chunks_json.exists() and done_json.exists()
    sc_prog = sc_prog or (lambda label, f: prog(label, 0.0))
    st = {"sc_start": 0.0, "sc_f0": None, "sc_done": False, "enc_t0": 0.0, "enc_f0": None, "frac": 0.0,
          "chunks": 0, "queue": False, "prep_t0": 0.0, "prep_r0": None}

    def on_line(line: str) -> bool:
        if not st["sc_start"] and "Scene detection" in line:
            st["sc_start"] = time.time()
        if "Queue" in line and "Workers" in line:        # av1an がチャンクの一覧を作り終え、エンコードを始めた
            st["queue"] = True
            m = re.search(r"Queue\s+(\d+)", line)
            if m and not st["chunks"]:
                st["chunks"] = int(m.group(1))            # チャンクの数（chunks.json を読めないときにも出せるように）
        return False                                      # ログにはそのまま出す

    def fresh(p: Path) -> bool:
        """この実行で作られたもの（前回の残りではない）"""
        try:
            return p.stat().st_mtime >= t0 - 1
        except OSError:
            return False

    def scenes_ready() -> bool:
        return fresh(scenes_json)

    def encoding() -> bool:
        # 合図はどれか 1 つで足りる: chunks.json（この実行で作られたもの）、ログの「Queue … Workers …」、再開
        return done_json.exists() and (resumed or st["queue"] or fresh(chunks_json))

    def prep_step():
        """シーン検出の後、エンコードの前: hybrid / segment は、キーフレームを調べてから映像を切り出す（split）"""
        now = time.time()
        if not st["prep_t0"]:
            st["prep_t0"] = now
        el = _fmt_time(now - st["prep_t0"])
        cut = _dir_bytes(state_dir / "split")
        if cut and src_size:
            f = min(cut / src_size, 1.0)
            sc_prog(f"分割を準備中: 映像を切り出しています {f * 100:.0f}%（経過 {el}）", 1.0)
            return
        what = ""
        if IS_WIN and src_size:
            try:
                n = _win_own_read_bytes(p.pid)
            except Exception:  # noqa: BLE001
                n = None
            if n is not None:
                if st["prep_r0"] is None:
                    st["prep_r0"] = n                     # シーン検出までに読んだ分は除く
                what = f" {min((n - st['prep_r0']) / src_size, 1.0) * 100:.0f}%"
        sc_prog(f"分割を準備中: キーフレームを調べています{what}（経過 {el}）", 1.0)

    def scene_step():
        now = time.time()
        f = None
        if reader and src_size > 0:
            try:
                n = reader(p)
                f = min(n / src_size, 1.0) if n is not None else None
            except Exception:  # noqa: BLE001  測れなくても処理は続ける
                f = None
        if st["sc_start"]:
            el = now - st["sc_start"]
            if f is None:
                sc_prog(f"シーン検出中（経過 {_fmt_time(el)}）", 0.0)
                return
            if st["sc_f0"] is None:
                st["sc_f0"] = f
            rest = ""
            if el > 10 and f > st["sc_f0"]:
                rest = f", 残り {_fmt_time((1 - f) * el / (f - st['sc_f0']))}"
            sc_prog(f"シーン検出中 {f * 100:.0f}%（経過 {_fmt_time(el)}{rest}）", f)
        else:
            what = f"入力を読み込み中 {f * 100:.0f}%、" if f else ""
            sc_prog(f"av1an を準備中（{what}経過 {_fmt_time(now - t0)}）", 0.0)

    def encode_step(d: dict):
        now = time.time()
        total = d.get("frames") or 1
        done_map = d.get("done", {}) or {}
        done = sum(_done_frames(v) for v in done_map.values())
        if not st["chunks"]:
            st["chunks"] = _chunk_count(state_dir / "chunks.json")
        files = _chunk_files(state_dir / "encode")
        # 終わったチャンクの 1 フレームあたりのサイズから、作業中のチャンクが何フレーム進んだかを見積もる
        done_bytes = sum(_done_bytes(v) or files.get(k, 0) for k, v in done_map.items())
        active = {k: v for k, v in files.items() if k not in done_map}
        partial = 0.0
        if done and done_bytes:
            partial = sum(active.values()) / (done_bytes / done)
        f = min((done + partial) / total, 0.999)
        f = st["frac"] = max(st["frac"], f)               # 見積もりの揺れで戻らないようにする
        if st["enc_f0"] is None:
            st["enc_t0"], st["enc_f0"] = now, f           # 再開時は、それまでの分を速度計算から除く
        el = now - st["enc_t0"]
        parts = [f"{workers} 本"]
        n = st["chunks"]
        parts.append(f"チャンク {len(done_map)}/{n} 完了" if n else f"チャンク {len(done_map)} 完了")
        if not done_map:
            mb = sum(active.values()) / 1e6
            parts.append(f"最初のチャンクを処理中・出力 {mb:.1f} MB")
        elif el > 10 and f > st["enc_f0"]:
            fps = (f - st["enc_f0"]) * total / el
            parts.append(f"{fps:.1f} fps")
            parts.append(f"残り {_fmt_time((1 - f) * total / fps)}")
        parts.append(f"経過 {_fmt_time(now - t0)}")
        prog(f"並列エンコード中（{', '.join(parts)}）", f)

    def poll():
        while p.poll() is None:
            try:
                if encoding():
                    encode_step(json.loads(done_json.read_text()))
                elif st["sc_done"] or scenes_ready():
                    if not st["sc_done"]:
                        st["sc_done"] = True
                        if log and st["sc_start"]:
                            log(f"シーン検出が終わりました（{_fmt_time(time.time() - st['sc_start'])}）")
                    prep_step()
                else:
                    scene_step()
            except ValueError:
                pass                                      # av1an が書いている途中の JSON。次の回に読む
            except Exception as e:  # noqa: BLE001  進捗が出せなくてもエンコードは続ける
                if log and not st.get("warned"):
                    st["warned"] = True
                    log(f"警告: 進捗を読み取れません（{type(e).__name__}: {e}）")
            time.sleep(1)

    p = runner.start(cmd, env=env, on_line=on_line)
    th = threading.Thread(target=poll, daemon=True)
    th.start()
    rc, = runner.wait(p)
    th.join(timeout=3)
    if rc != 0 or not out.exists():
        raise RuntimeError(f"av1an がエラー終了しました（code {rc}）")
