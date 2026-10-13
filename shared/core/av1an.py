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


def run_av1an(runner, cmd: list, env, state_dir: Path, workers: int, prog, out: Path, log=None) -> None:
    """av1an を実行し、done.json から進捗を出す。失敗したら RuntimeError"""
    import json
    import threading
    import time

    done_json = state_dir / "done.json"
    t0 = time.time()
    start_done = [None]

    def poll():
        while p.poll() is None:
            try:
                d = json.loads(done_json.read_text())
                total = d.get("frames") or 1
                done = sum(d.get("done", {}).values())
                if start_done[0] is None:
                    start_done[0] = done          # 再開時は、それまでの分を速度計算から除く
                el = time.time() - t0
                fps = (done - start_done[0]) / el if el > 5 else 0
                eta = ""
                if fps > 0:
                    rest = (total - done) / fps
                    eta = f", 残り {int(rest // 3600)}:{int(rest % 3600 // 60):02d}"
                prog(f"並列エンコード中（{workers} 本, {fps:.1f} fps{eta}）", done / total)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)

    p = runner.start(cmd, env=env)
    th = threading.Thread(target=poll, daemon=True)
    th.start()
    rc, = runner.wait(p)
    th.join(timeout=3)
    if rc != 0 or not out.exists():
        raise RuntimeError(f"av1an がエラー終了しました（code {rc}）")
