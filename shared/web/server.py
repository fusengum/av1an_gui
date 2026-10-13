"""Web 画面のサーバー（FastAPI）。このフォルダに入っているツールのジョブをキューで順番に処理する。
キューを持つのはこのサーバーだけで、デスクトップ版もここにジョブを送って進捗を受け取る（shared.web.client）。
認証は無し（LAN 内での利用を想定）。"""
from __future__ import annotations

import os
import string
import threading
import time
import uuid
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from shared import app_name, apps, tool_list
from shared.settings import tool_settings
from shared.core import probe as P
from shared.core import tools
from shared.core.config import APP_ROOT, IS_WIN
from shared.core.runner import Cancelled

from .apps import load_apps

STATIC = Path(__file__).parent / "static"
MEDIA_EXTS = {".mkv", ".mka", ".mp4", ".m4v", ".mov", ".m2ts", ".mts", ".ts", ".hevc", ".h264", ".264", ".ivf",
              ".wav", ".flac", ".webm", ".avi", ".y4m"}
HIDDEN_SETTINGS = {"window_geometry", "recent_dir"}
VERSION = "0.3.0"
INSTANCE = uuid.uuid4().hex[:12]       # 起動ごとに変わる（デスクトップ版がサーバーの再起動を見分ける）


# ------------------------------------------------------------------ ジョブ

@dataclass
class WebJob:
    id: str
    kind: str                       # ツールのパッケージ名
    path: str
    options: dict
    state: str = "queued"           # queued | running | done | failed | cancelled
    progress: float = 0.0
    label: str = "待機"
    output: str = ""
    error: str = ""
    created: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0
    log: list[str] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def summary(self) -> dict:
        d = {k: getattr(self, k) for k in ("id", "kind", "path", "options", "state", "progress", "label", "output",
                                           "error", "created", "started", "finished")}
        d["name"] = Path(self.path).name
        d["log_lines"] = len(self.log)
        a = load_apps().get(self.kind)
        d["option_text"] = a.option_text(self.options) if a else ""
        return d


class JobQueue:
    def __init__(self):
        self.jobs: dict[str, WebJob] = {}
        self.order: list[str] = []
        self.lock = threading.Lock()
        self.wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def add(self, kind: str, path: str, options: dict) -> WebJob:
        j = WebJob(id=uuid.uuid4().hex[:10], kind=kind, path=path, options=options)
        with self.lock:
            self.jobs[j.id] = j
            self.order.append(j.id)
        self.wake.set()
        return j

    def remove(self, jid: str):
        with self.lock:
            j = self.jobs.get(jid)
            if j and j.state != "running":
                del self.jobs[jid]
                self.order.remove(jid)

    def list(self) -> list[WebJob]:
        with self.lock:
            return [self.jobs[i] for i in self.order]

    def _next(self) -> WebJob | None:
        with self.lock:
            for i in self.order:
                if self.jobs[i].state == "queued":
                    return self.jobs[i]
        return None

    def _loop(self):
        while True:
            j = self._next()
            if j is None:
                self.wake.wait(2)
                self.wake.clear()
                continue
            self._run(j)

    def _run(self, j: WebJob):
        if j.cancel.is_set():                 # 取り出した直後に中止された
            j.state, j.label = "cancelled", "中止"
            return
        j.state, j.started, j.label = "running", time.time(), "開始"

        def log(m: str):
            j.log.append(m)
            if len(j.log) > 20000:
                del j.log[:5000]

        def prog(label: str, f: float):
            j.label, j.progress = label, max(0.0, min(f, 1.0))

        try:
            a = load_apps()[j.kind]
            j.output = str(a.run(Path(j.path), j.options, log, prog, j.cancel) or "")
            j.state, j.progress, j.label = "done", 1.0, "完了"
        except Cancelled:
            j.state, j.label = "cancelled", "中止"
            log("中止しました")
        except Exception as e:  # noqa: BLE001
            j.state, j.error, j.label = "failed", str(e), "失敗"
            log(f"エラー: {e}")
        j.finished = time.time()


# ------------------------------------------------------------------ 環境セットアップ（バックグラウンド）

def _tool_settings():
    return tool_settings()


def _media_exts() -> set:
    return MEDIA_EXTS | {x for a in load_apps().values() for x in a.media_exts}


class EnvInstaller:
    def __init__(self):
        self.running = False
        self.message = ""
        self.progress = -1.0
        self.error = ""

    def start(self, names: list[str] | None):
        if self.running:
            return
        self.running, self.error, self.message = True, "", "開始"

        def cb(m, f):
            self.message, self.progress = m, f

        def run():
            lst = {t[0]: t for t in tool_list()}
            targets = names or [n for n in lst if n in tools.INSTALLERS]
            errs = []
            for n in targets:
                try:
                    tools.install_missing(_tool_settings(), cb, force=bool(names), tool_list=[lst[n]])
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{n}: {e}")
            self.error = " / ".join(errs)
            self.message = "完了" if not errs else "一部失敗"
            self.running = False

        threading.Thread(target=run, daemon=True).start()


# ------------------------------------------------------------------ API

app = FastAPI(title="web", version=VERSION)
Q = JobQueue()
ENV = EnvInstaller()


class AddJobs(BaseModel):
    kind: str
    paths: list[str]
    options: dict = {}
    recursive: bool = False


class ProbeReq(BaseModel):
    path: str
    kind: str = ""


def _title() -> tuple[str, str]:
    ids = list(load_apps())
    if len(ids) == 1:
        a = load_apps()[ids[0]]
        return f"{a.name} web", a.desc
    return "Media Tools", " / ".join(load_apps()[i].name for i in ids)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/icon/{name}")
def icon(name: str):
    a = load_apps().get(name) or next(iter(load_apps().values()), None)
    if not a:
        raise HTTPException(404)
    return FileResponse(a.icon)


@app.get("/api/meta")
def meta():
    title, sub = _title()
    return {
        "version": VERSION, "title": title, "subtitle": sub, "root": str(APP_ROOT), "instance": INSTANCE,
        "apps": [{"id": a.id, "name": a.name, "desc": a.desc, "job_fields": a.job_fields,
                  "job_defaults": (a.job_defaults() if a.job_defaults else {}), "assist": bool(a.assist_encoders),
                  "settings_fields": a.settings_fields, "settings_note": a.settings_note, "tips": a.tips,
                  **a.extra_meta} for a in load_apps().values()],
    }


@app.get("/api/state")
def state():
    return {"instance": INSTANCE, "jobs": [j.summary() for j in Q.list()],
            "env": {"running": ENV.running, "message": ENV.message, "progress": ENV.progress, "error": ENV.error}}


@app.get("/api/jobs/{jid}/log")
def job_log(jid: str, since: int = 0):
    j = Q.jobs.get(jid)
    if not j:
        raise HTTPException(404)
    return {"lines": j.log[since:], "next": len(j.log)}


@app.post("/api/jobs")
def add_jobs(req: AddJobs):
    a = load_apps().get(req.kind)
    if not a:
        raise HTTPException(400, "kind")
    skip = re.compile(a.skip_re) if a.skip_re else None
    files: list[Path] = []
    for p in map(Path, req.paths):
        if p.is_dir():
            it = p.rglob("*") if req.recursive else p.iterdir()
            files += sorted(f for f in it if f.is_file() and f.suffix.lower() in _media_exts()
                            and not (skip and skip.search(f.stem))
                            and not any(x in str(f.parent) for x in a.work_prefixes))
        elif p.is_file():
            files.append(p)
    if not files:
        raise HTTPException(400, "対象のファイルがありません")
    return {"added": [Q.add(req.kind, str(f), dict(req.options)).id for f in files]}


@app.post("/api/jobs/{jid}/cancel")
def cancel(jid: str):
    j = Q.jobs.get(jid)
    if not j:
        raise HTTPException(404)
    if j.state == "queued":
        j.state, j.label = "cancelled", "中止"
    j.cancel.set()
    return {"ok": True}


class RetryReq(BaseModel):
    options: dict | None = None        # 指定すればファイルごとの選択肢を変えてやり直す


@app.post("/api/jobs/{jid}/retry")
def retry(jid: str, req: RetryReq | None = None):
    j = Q.jobs.get(jid)
    if not j or j.state in ("running", "queued"):
        raise HTTPException(400)
    if req and req.options is not None:
        j.options = dict(req.options)
    j.state, j.progress, j.label, j.error, j.cancel = "queued", 0.0, "待機", "", threading.Event()
    j.output, j.started, j.finished = "", 0.0, 0.0
    Q.wake.set()
    return {"ok": True}


@app.post("/api/jobs/stop")
def stop_all():
    """待機中をすべて中止してから、処理中のものを止める（先に止めると次の待機が始まってしまう）"""
    jobs = Q.list()
    for j in jobs:
        if j.state == "queued":
            j.state, j.label = "cancelled", "中止"
            j.cancel.set()
    for j in jobs:
        if j.state == "running":
            j.cancel.set()
    return {"ok": True}


@app.delete("/api/jobs/{jid}")
def delete(jid: str):
    Q.remove(jid)
    return {"ok": True}


@app.post("/api/jobs/clear")
def clear_done():
    for j in Q.list():
        if j.state in ("done", "cancelled", "failed"):
            Q.remove(j.id)
    return {"ok": True}


@app.get("/api/fs")
def fs(path: str = ""):
    if not path:
        if IS_WIN:
            roots = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        else:
            roots = ["/", str(Path.home())] + [str(p) for p in Path("/mnt").glob("*")] + \
                    [str(p) for p in Path("/media").glob("*/*")]
        return {"path": "", "parent": None, "dirs": [{"name": r, "path": r} for r in roots], "files": []}
    p = Path(path)
    if not p.is_dir():
        raise HTTPException(404, "フォルダがありません")
    hide = tuple(x for a in load_apps().values() for x in a.work_prefixes)
    dirs, files = [], []
    try:
        for c in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            if c.name.startswith(".") or (hide and c.name.startswith(hide)):
                continue
            try:
                if c.is_dir():
                    dirs.append({"name": c.name, "path": str(c)})
                elif c.suffix.lower() in _media_exts():
                    files.append({"name": c.name, "path": str(c), "size": c.stat().st_size})
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(403, "アクセスできません")
    parent = str(p.parent) if p.parent != p else ""
    return {"path": str(p), "parent": parent, "dirs": dirs, "files": files}


@app.post("/api/probe")
def probe(req: ProbeReq):
    ffprobe = tools.find_tool("ffprobe", _tool_settings())
    if not ffprobe:
        raise HTTPException(400, "ffprobe がありません")
    info = P.probe(req.path, ffprobe)
    v = info.video
    extra: list[str] = []
    a = load_apps().get(req.kind)
    if a and a.probe_extra:
        try:
            extra = a.probe_extra(info)
        except Exception as e:  # noqa: BLE001
            extra = [str(e)]
    return {"video": v.describe() if v else None, "video_detail": v.detail() if v else "",
            "audio": [{"index": x.index, "label": x.describe()} for x in info.audio], "extra": extra}


def _settings_obj(kind: str):
    a = load_apps().get(kind)
    if not a:
        raise HTTPException(404)
    return a.settings_cls.load()


@app.get("/api/settings/{kind}")
def get_settings(kind: str):
    s = _settings_obj(kind)
    return {k: v for k, v in asdict(s).items() if k not in HIDDEN_SETTINGS}


@app.put("/api/settings/{kind}")
def put_settings(kind: str, data: dict):
    s = _settings_obj(kind)
    names = {f.name for f in fields(s)}
    for k, v in data.items():
        if "." in k:                          # 辞書の要素（例: extra.svt-av1）
            top, sub = k.split(".", 1)
            if top in names and isinstance(getattr(s, top), dict):
                d = dict(getattr(s, top))
                d[sub] = v
                setattr(s, top, d)
            continue
        if k in names and k not in HIDDEN_SETTINGS:
            cur = getattr(s, k)
            try:
                v = type(cur)(v) if cur is not None and not isinstance(cur, dict) else v
            except (TypeError, ValueError):
                raise HTTPException(400, f"{k} の値が不正です")
            setattr(s, k, v)
    s.save()
    return {"ok": True}


class AssistReq(BaseModel):
    res: str = "both"
    cpu: str = "all"
    mem_reserve: float | None = None
    save: bool = False


@app.get("/api/assist")
def assist_info():
    from shared.core import av1an as A
    return {"cores": A.cpu_count(), "mem": round(A.total_memory_gb(), 1), "avail": round(A.available_memory_gb(), 1),
            "cpu_options": {k: v[0] for k, v in A.CPU_USE.items()},
            "res_options": {"both": "両方（ファイルごとに自動で切り替える）", "4k": "ほぼ 4K", "hd": "ほぼ 1080p 以下"}}


@app.post("/api/assist/{kind}")
def assist(kind: str, req: AssistReq):
    from shared.core import av1an as A
    a = load_apps().get(kind)
    if not a or not a.assist_encoders:
        raise HTTPException(404)
    cores, mem = A.cpu_count(), A.total_memory_gb()
    reserve = req.mem_reserve if req.mem_reserve is not None else max(4.0, round(mem * 0.1))
    res = {"both": ["4k", "hd"], "4k": ["4k"], "hd": ["hd"]}.get(req.res, ["4k", "hd"])
    profiles, rows = {}, []
    for enc, label in a.assist_encoders.items():
        profiles[enc] = {}
        for rc in res:
            p = A.plan_workers(enc, 3840 if rc == "4k" else 1920, cores, mem, req.cpu, reserve)
            profiles[enc][rc] = {"workers": p["workers"], "threads": p["threads"]}
            rows.append({"encoder": label, "res": A.RES_CLASS_LABELS[rc], **p})
    if req.save:
        s = a.settings_cls.load()
        s.worker_profiles = profiles
        s.assist_answers = {"res": req.res, "cpu": req.cpu, "mem_reserve": reserve}
        if hasattr(s, "workers"):
            s.workers = 0
        if hasattr(s, "threads"):
            s.threads = 0
        s.save()
    return {"rows": rows, "mem_reserve": reserve, "saved": req.save}


@app.get("/api/env")
def env():
    s = _tool_settings()
    rows = []
    for t in tools.check_all(s, tool_list()):
        rows.append({"name": t.name, "label": t.label, "ok": t.ok, "required": t.required, "path": t.path or "",
                     "version": t.version, "note": t.note, "downloadable": t.name in tools.INSTALLERS})
    extra = []
    for a in load_apps().values():
        if a.env_extra:
            try:
                extra += a.env_extra()
            except Exception as e:  # noqa: BLE001
                extra.append(f"{a.name}: {e}")
    return {"tools": rows, "extra": extra, "apps": [app_name(p) for p in apps()],
            "installer": {"running": ENV.running, "message": ENV.message, "progress": ENV.progress,
                          "error": ENV.error}}


class InstallReq(BaseModel):
    names: list[str] | None = None


@app.post("/api/env/install")
def env_install(req: InstallReq):
    ENV.start(req.names)
    return {"ok": True}


@app.exception_handler(Exception)
def on_error(_req, exc):
    return JSONResponse({"detail": str(exc)}, status_code=500)
