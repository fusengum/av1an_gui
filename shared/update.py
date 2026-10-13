"""更新: GitHub のリリースから新しい版を取ってきて、このフォルダを入れ替える

  python -m shared.update check [--force]       新しい版があるか
  python -m shared.update apply [--relaunch PKG,...]
      logs/update/new に展開した新しい版で入れ替える（更新用のプロセス。GUI やサーバーが起動する）

入れ替えの間はサーバーと GUI を止める（Windows では、動いているライブラリの DLL を上書きできないため）。
設定（settings*.json）・logs・tools・.venv には触らない。途中で失敗したら元に戻す。
このモジュールは標準ライブラリと shared の軽い部分だけを読む（PySide6 などを読むと入れ替えられなくなる）。"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import ROOT, app_infos, apps
from .core.config import IS_WIN
from .version import app_version, parse_version

UPD = ROOT / "logs" / "update"
CHECK_FILE = UPD / "check.json"
NEW = UPD / "new"
BACKUP = UPD / "backup"
MANIFEST = UPD / "manifest.json"      # いまの版の ZIP にあったファイル（次の更新で、無くなったものを消すため）
STATUS = UPD / "status.json"          # 最後の更新の結果（画面が次に開いたときに一度だけ知らせる）
LOG = ROOT / "logs" / "update.log"
KEEP_DIRS = {"logs", "tools", ".venv", ".git", "__pycache__"}
CHECK_EVERY = 24 * 3600
NO_WINDOW = 0x08000000


class UpdateError(Exception):
    pass


def _log(msg: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _keep(rel: Path) -> bool:
    """入れ替えないもの（利用者の設定や、このフォルダに取ってきたもの）"""
    parts = rel.parts
    if not parts or parts[0] in KEEP_DIRS or "__pycache__" in parts:
        return True
    return len(parts) == 1 and parts[0].startswith("settings") and parts[0].endswith(".json")


def repo() -> str:
    """更新元の GitHub リポジトリ（ツールの app.json の update.github。最初に見つかったもの）"""
    for i in app_infos():
        r = (i.get("update") or {}).get("github", "")
        if r:
            return r
    return ""


def _get(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "av1an_gui-updater", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ------------------------------------------------------------------ 確認

def latest() -> dict:
    """最新のリリース {"version", "tag", "zip", "notes", "url"}。
    テスト用: 環境変数 AV1AN_GUI_UPDATE_SOURCE にリリース情報の JSON（tag_name, zip のパス）を指定するとそれを使う"""
    src = os.environ.get("AV1AN_GUI_UPDATE_SOURCE")
    if src:
        d = json.loads(Path(src).read_text(encoding="utf-8"))
        return {"version": d["tag_name"].lstrip("vV"), "tag": d["tag_name"], "zip": d["zip"],
                "notes": d.get("body", ""), "url": d.get("html_url", "")}
    r = repo()
    if not r:
        raise UpdateError("更新元が設定されていません（app.json の update.github）")
    try:
        d = json.loads(_get(f"https://api.github.com/repos/{r}/releases/latest", 20))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError(f"{r} にはまだリリースがありません") from None
        raise UpdateError(f"更新を確認できませんでした（{e}）") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise UpdateError(f"更新を確認できませんでした（{e}）") from None
    asset = next((a["browser_download_url"] for a in d.get("assets", []) if a.get("name", "").lower().endswith(".zip")),
                 None)
    return {"version": d["tag_name"].lstrip("vV"), "tag": d["tag_name"], "zip": asset or d["zipball_url"],
            "notes": d.get("body") or "", "url": d.get("html_url", "")}


def check(force: bool = False) -> dict:
    """新しい版があるか。結果は 1 日（失敗したときは 1 時間）保存し、その間は取りに行かない"""
    cur = app_version()
    if not force:
        try:
            c = json.loads(CHECK_FILE.read_text(encoding="utf-8"))
            age = time.time() - c.get("checked", 0)
            if c.get("current") == cur and age < (3600 if c.get("error") else CHECK_EVERY):
                return c
        except (OSError, ValueError):
            pass
    try:
        info = latest()
        res = {"current": cur, "latest": info["version"], "error": "",
               "available": parse_version(info["version"]) > parse_version(cur), **info}
    except Exception as e:  # noqa: BLE001
        res = {"current": cur, "latest": "", "available": False, "error": str(e)}
    res["checked"] = time.time()
    try:
        UPD.mkdir(parents=True, exist_ok=True)
        CHECK_FILE.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return res


# ------------------------------------------------------------------ 取得

def download(info: dict, progress=None) -> Path:
    """リリースの ZIP を取ってきて logs/update/new に展開する。progress(説明, 0..1 or -1)"""
    progress = progress or (lambda m, f: None)
    progress(f"{info['version']} を取得中…", -1)
    z = info["zip"]
    if Path(z).is_file():
        data = Path(z).read_bytes()
    else:
        req = urllib.request.Request(z, headers={"User-Agent": "av1an_gui-updater"})
        buf = io.BytesIO()
        with urllib.request.urlopen(req, timeout=60) as r:
            total = int(r.headers.get("Content-Length") or 0)
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                buf.write(chunk)
                progress(f"{info['version']} を取得中…", buf.tell() / total if total else -1)
        data = buf.getvalue()
    shutil.rmtree(NEW, ignore_errors=True)
    NEW.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        tops = {n.split("/", 1)[0] for n in names}
        # GitHub のソースの ZIP などは owner-repo-sha/ の下にまとまっている: その 1 段を外す
        prefix = next(iter(tops)) + "/" if len(tops) == 1 and all("/" in n for n in names) else ""
        base = NEW.resolve()
        for n in names:
            rel = n[len(prefix):]
            dst = (NEW / rel).resolve()
            if base not in dst.parents:          # ZIP の中の ../ などでフォルダの外に書かせない
                raise UpdateError(f"更新の ZIP に不正なパスがあります: {n}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(zf.read(n))
    if not (NEW / "shared" / "__init__.py").is_file():
        shutil.rmtree(NEW, ignore_errors=True)
        raise UpdateError("更新の ZIP の中身が正しくありません（shared が見つかりません）")
    if not (NEW / "VERSION").is_file():
        (NEW / "VERSION").write_text(info["version"] + "\n", encoding="utf-8")
    progress("取得しました", 1.0)
    return NEW


def _pythonw() -> str:
    exe = Path(sys.executable)
    if IS_WIN and exe.with_name("pythonw.exe").exists():
        return str(exe.with_name("pythonw.exe"))
    return str(exe)


def _detached(cmd: list[str]) -> None:
    """呼び出し元（GUI やサーバー）を止めても続くように、切り離して起動する"""
    UPD.mkdir(parents=True, exist_ok=True)
    out = open(LOG, "a", encoding="utf-8")
    kw: dict = {"start_new_session": True}
    if IS_WIN:
        kw = {"creationflags": 0x00000008 | 0x00000200 | 0x01000000}   # DETACHED / NEW_GROUP / BREAKAWAY_FROM_JOB
    try:
        try:
            subprocess.Popen(cmd, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=out, stderr=out, close_fds=True,
                             **kw)
        except OSError:
            if not IS_WIN:
                raise
            kw["creationflags"] &= ~0x01000000                          # ジョブから抜けられない環境
            subprocess.Popen(cmd, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=out, stderr=out, close_fds=True,
                             **kw)
    finally:
        out.close()


def spawn_apply(relaunch: list[str] = ()) -> None:
    """更新用のプロセスを起動する（呼び出した GUI はこの後すぐ閉じてよい）"""
    _status("running", "更新しています…", (NEW / "VERSION").read_text(encoding="utf-8").strip())
    _detached([_pythonw(), "-m", "shared.update", "apply", "--relaunch", ",".join(relaunch)])


# ------------------------------------------------------------------ 結果

def _status(state: str, message: str, version: str = "") -> None:
    try:
        UPD.mkdir(parents=True, exist_ok=True)
        STATUS.write_text(json.dumps({"state": state, "message": message, "version": version, "time": time.time(),
                                      "shown": False}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    _log(f"[{state}] {message}")


def last_status() -> dict:
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def mark_shown() -> None:
    s = last_status()
    if s:
        s["shown"] = True
        try:
            STATUS.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


# ------------------------------------------------------------------ 入れ替え（更新用のプロセス）

def _files(root: Path) -> list[str]:
    out = []
    for f in root.rglob("*"):
        if f.is_file():
            rel = f.relative_to(root)
            if not _keep(rel):
                out.append(rel.as_posix())
    return sorted(out)


def _python() -> str:
    """ライブラリを入れる先の python（venv の python.exe）"""
    exe = Path(sys.executable)
    if IS_WIN and exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return str(exe)


def _run(cmd: list[str], env=None) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=str(ROOT),
                       creationflags=NO_WINDOW if IS_WIN else 0)
    _log("> " + " ".join(cmd) + "\n" + (r.stdout + r.stderr).strip()[-4000:])
    return r


def _pip_install() -> None:
    """requirements.txt のライブラリを入れる（インストーラーと同じく uv を使う。無ければ pip）"""
    uvs = [ROOT / "tools" / "uv" / ("uv.exe" if IS_WIN else "uv")]
    uv = next((str(p) for p in uvs if p.is_file()), None) or shutil.which("uv")
    env = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(ROOT / "tools" / "python"),
               UV_CACHE_DIR=str(ROOT / "tools" / "uv-cache"), UV_NO_PROGRESS="1", PYTHONUTF8="1")
    req = str(ROOT / "requirements.txt")
    cmd = [uv, "pip", "install", "--python", _python(), "-r", req] if uv else [_python(), "-m", "pip", "install", "-r", req]
    r = _run(cmd, env)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
        raise UpdateError(f"ライブラリを入れ直せませんでした（{tail[0]}）")


def _lock():
    UPD.mkdir(parents=True, exist_ok=True)
    f = open(UPD / "apply.lock", "a+")
    try:
        if IS_WIN:
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


def _digest(p: Path) -> str:
    try:
        return hashlib.sha1(p.read_bytes()).hexdigest()
    except OSError:
        return ""


def _launch_gui(pkg: str) -> None:
    _detached([_pythonw(), "-m", pkg])


def _start_again(relaunch: set[str]) -> None:
    """サーバーと（開いていた）GUI を起動し直す"""
    from .web.client import Client, ServerError, find_procs, kill_procs
    client = Client()
    if not IS_WIN:
        # Linux: GUI はファイルを入れ替えてから止めて開き直す。サーバーは最後（systemd が起動し直すときに、
        # この更新用のプロセスも一緒に止められることがあるため）
        if relaunch:
            kill_procs(find_procs(sorted(relaunch)))
            for m in sorted(relaunch):
                _launch_gui(m)
        try:
            if client.find():
                client.restart()
            else:
                client.ensure()
        except ServerError as e:
            _log(f"サーバーを起動し直せませんでした: {e}")
        return
    try:
        client.ensure()
    except ServerError as e:
        _log(f"サーバーを起動できませんでした: {e}")
    for m in sorted(relaunch):
        _launch_gui(m)


def apply(relaunch: list[str]) -> int:
    lock = _lock()
    if lock is None:
        _log("ほかの更新が動いているため、やめました")
        return 1
    from .web.client import Client, ServerError, find_procs, kill_procs
    if not (NEW / "shared" / "__init__.py").is_file():
        _status("failed", "更新するファイルがありません（取得からやり直してください）", app_version())
        return 1
    version = (NEW / "VERSION").read_text(encoding="utf-8").strip()
    old_version = app_version()
    client = Client()
    try:
        if client.find() and any(j["state"] == "running" for j in client.state()["jobs"]):
            _status("failed", "エンコード中のため更新しませんでした。終わってから、もう一度更新してください", old_version)
            return 1
    except ServerError:
        pass
    time.sleep(2)                              # 呼び出したサーバーが返事を返し終えるまで待つ
    mods = apps()
    again = set(relaunch) | {m for m in mods if find_procs([m])}
    if IS_WIN:                                 # 動いているライブラリの DLL は上書きできないので、先に止める
        kill_procs(find_procs(mods))
        kill_procs(find_procs(["shared.web"]))
        time.sleep(1.5)

    new_files = _files(NEW)
    try:
        old_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old_manifest = None                    # 初めての更新: 何が前の版のファイルか分からないので消さない
    old_req = _digest(ROOT / "requirements.txt")
    old_installer = _digest(ROOT / "install.sh")
    shutil.rmtree(BACKUP, ignore_errors=True)
    BACKUP.mkdir(parents=True)
    created, saved, pip_ran = [], [], False
    _log(f"{old_version} → {version}: {len(new_files)} ファイル")
    try:
        for rel in new_files:
            dst, src = ROOT / rel, NEW / rel
            if dst.is_file():
                if _digest(dst) == _digest(src):
                    continue
                (BACKUP / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, BACKUP / rel)
                saved.append(rel)
            else:
                created.append(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        for rel in sorted(set(old_manifest or []) - set(new_files)):
            dst = ROOT / rel
            if dst.is_file() and not _keep(Path(rel)):
                (BACKUP / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, BACKUP / rel)
                saved.append(rel)
                dst.unlink()
        if _digest(ROOT / "requirements.txt") != old_req:
            pip_ran = True
            _pip_install()
    except Exception as e:  # noqa: BLE001
        _log(f"失敗したので元に戻します: {e}")
        for rel in created:
            (ROOT / rel).unlink(missing_ok=True)
        for rel in saved:
            shutil.copy2(BACKUP / rel, ROOT / rel)
        if pip_ran:
            try:
                _pip_install()                 # 元の requirements で入れ直す
            except Exception as e2:  # noqa: BLE001
                _log(f"元のライブラリにも戻せませんでした: {e2}")
        _status("rolled_back", f"{version} への更新に失敗したため、{old_version} に戻しました: {e}", old_version)
        _start_again(again)
        return 1

    MANIFEST.write_text(json.dumps(new_files), encoding="utf-8")
    notes = []
    # 増えた外部ツールだけ取る（入っているものは取り直さない）。テストでは AV1AN_GUI_UPDATE_SKIP_SETUP で飛ばす
    skip = os.environ.get("AV1AN_GUI_UPDATE_SKIP_SETUP")
    r = None if skip else _run([_python(), "-m", "shared", "setup"])
    if r is not None and r.returncode != 0:
        notes.append("外部ツールの一部を取得できませんでした（設定の「外部ツール」で確認してください）")
    if not IS_WIN and _digest(ROOT / "install.sh") != old_installer:
        notes.append("install.sh が変わりました。必要なら sudo ./install.sh を実行してください")
    _status("done", f"{old_version} から {version} に更新しました" + (f"（{' / '.join(notes)}）" if notes else ""), version)
    shutil.rmtree(NEW, ignore_errors=True)
    _start_again(again)
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="shared.update")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--force", action="store_true")
    a = sub.add_parser("apply")
    a.add_argument("--relaunch", default="", help="更新の後に開き直す GUI（パッケージ名、カンマ区切り）")
    args = ap.parse_args(argv)
    if args.cmd == "check":
        r = check(args.force)
        if r.get("error"):
            print(r["error"])
            return 1
        print(f"いまの版 {r['current']} / 最新 {r['latest']}" + ("（更新できます）" if r["available"] else "（最新です）"))
        return 0
    return apply([x for x in args.relaunch.split(",") if x])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
