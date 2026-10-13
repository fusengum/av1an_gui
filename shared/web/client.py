"""デスクトップ版から Web 画面のサーバーを使うためのクライアント。
キューと進捗はサーバーが 1 つだけ持ち、デスクトップ版も Web 画面も同じものを表示する。
サーバーが動いていなければ、このフォルダの Python で裏に起動する（デスクトップ版を閉じてもエンコードは続く）。"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from shared.core.config import APP_ROOT, IS_WIN

INFO = APP_ROOT / "logs" / "web.json"
PORTS = range(8765, 8800)
# localhost への接続がプロキシ設定（HTTP_PROXY など）に取られないようにする
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ServerError(Exception):
    pass


def write_info(host: str, port: int) -> None:
    try:
        INFO.parent.mkdir(parents=True, exist_ok=True)
        INFO.write_text(json.dumps({"host": host, "port": port, "pid": os.getpid()}), encoding="utf-8")
    except OSError:
        pass


def read_info() -> dict:
    try:
        return json.loads(INFO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


class Client:
    def __init__(self):
        self.base = ""
        self._lock = threading.Lock()
        self._spawned = 0.0

    # ------------------------------------------------------------ 通信
    def _req(self, method: str, path: str, body=None, timeout: float = 5, base: str | None = None):
        url = (base or self.base) + path
        if not (base or self.base):
            raise ServerError("Web サーバーに接続していません")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with _OPENER.open(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("detail") or str(e)
            except Exception:  # noqa: BLE001
                msg = str(e)
            raise ServerError(str(msg)) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            if base is None:
                self.base = ""
            raise ServerError(f"Web サーバーに接続できません（{e}）") from None

    def get(self, path: str, **kw):
        return self._req("GET", path, **kw)

    def post(self, path: str, body=None, **kw):
        return self._req("POST", path, body if body is not None else {}, **kw)

    def delete(self, path: str, **kw):
        return self._req("DELETE", path, **kw)

    # ------------------------------------------------------------ 接続
    def _ours(self, port: int) -> bool:
        """そのポートで動いているのが、このフォルダのサーバーか（別のツールの Web 画面と取り違えない）"""
        if not _open(port):
            return False
        base = f"http://127.0.0.1:{port}"
        try:
            m = self._req("GET", "/api/meta", timeout=3, base=base)
        except ServerError:
            return False
        try:
            same = Path(m.get("root", "")).resolve() == APP_ROOT
        except OSError:
            same = False
        if same:
            self.base = base
        return same

    def find(self) -> bool:
        if self.base:
            return True
        port = read_info().get("port")
        ports = [int(port)] if port else []
        ports += [p for p in PORTS if p not in ports]
        return any(self._ours(p) for p in ports)

    def ensure(self, wait: float = 20) -> None:
        """サーバーにつなぐ。動いていなければ起動する。つながらなければ ServerError"""
        with self._lock:
            if self.find():
                return
            if time.time() - self._spawned > 30:        # 起動に失敗しているときに何度も起動しない
                self._spawned = time.time()
                self._spawn()
            end = time.time() + wait
            while time.time() < end:
                time.sleep(0.5)
                if self.find():
                    return
        raise ServerError("Web サーバーを起動できませんでした（logs/web.log を確認してください）")

    def _spawn(self) -> None:
        info = read_info()
        # インストール時に登録した Web 画面（LAN 向け）があればその設定で、無ければこの PC の中だけで待ち受ける
        host = info.get("host") or "127.0.0.1"
        port = int(info.get("port") or 0)
        if not port or _open(port):
            port = next((p for p in PORTS if not _open(p)), PORTS[0])
        exe = Path(sys.executable)
        if IS_WIN and exe.name.lower() == "python.exe" and exe.with_name("pythonw.exe").exists():
            exe = exe.with_name("pythonw.exe")             # コンソール画面を出さない
        logs = APP_ROOT / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        out = open(logs / "web.log", "a", encoding="utf-8")
        kw = {"creationflags": 0x00000008 | 0x00000200} if IS_WIN else {"start_new_session": True}
        try:
            subprocess.Popen([str(exe), "-m", "shared.web", "--host", host, "--port", str(port)], cwd=str(APP_ROOT),
                             stdin=subprocess.DEVNULL, stdout=out, stderr=out, close_fds=True, **kw)
        finally:
            out.close()

    # ------------------------------------------------------------ キュー
    def state(self) -> dict:
        return self.get("/api/state", timeout=5)

    def add(self, kind: str, path: Path, options: dict) -> str:
        r = self.post("/api/jobs", {"kind": kind, "paths": [str(path)], "options": options})
        return r["added"][0]

    def retry(self, jid: str, options: dict | None = None) -> None:
        self.post(f"/api/jobs/{jid}/retry", {"options": options})

    def remove(self, jid: str) -> None:
        self.delete(f"/api/jobs/{jid}")

    def stop_all(self) -> None:
        self.post("/api/jobs/stop")

    def log(self, jid: str, since: int) -> dict:
        return self.get(f"/api/jobs/{urllib.parse.quote(jid)}/log?since={since}")
