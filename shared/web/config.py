"""Web 画面のサーバーの設定（LAN に公開するか・ポート・ログオン時の自動起動）と、その反映

設定は settings_web.json に置き、サーバーは起動時にこれを読む（--host / --port を指定したときはそちらが優先）。
キューはサーバーが持つため、LAN に公開しない場合もサーバーはこの PC の中だけで動く。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from shared import app_infos
from shared.core.config import APP_ROOT, IS_WIN
from shared.core.tools import popen_kwargs

PATH = APP_ROOT / "settings_web.json"
DEFAULT_PORT = 8765


@dataclass
class WebConfig:
    lan: bool = False                # 他の PC のブラウザから使えるようにする（0.0.0.0 で待ち受け）
    port: int = DEFAULT_PORT
    autostart: bool = False          # ログオン時（Linux は起動時）にサーバーを起動する

    @property
    def host(self) -> str:
        return "0.0.0.0" if self.lan else "127.0.0.1"

    @classmethod
    def load(cls) -> "WebConfig":
        try:
            d = json.loads(PATH.read_text(encoding="utf-8"))
            return cls(lan=bool(d.get("lan", False)), port=int(d.get("port") or DEFAULT_PORT),
                       autostart=bool(d.get("autostart", False)))
        except (OSError, ValueError, TypeError):
            pass
        # 設定ファイルが無い（以前の版で入れた）: 自動起動が登録されていれば LAN 向け（以前のインストーラーは
        # 0.0.0.0 で登録していた）。最後に動いたサーバーの待ち受けだけで決めると、デスクトップ版が立てた
        # 127.0.0.1 のサーバーを引き継いでしまい、LAN から見えなくなる
        from .client import read_info
        info = read_info()
        auto = bool(autostart_state())
        lan = auto or info.get("host") == "0.0.0.0"
        return cls(lan=lan, port=int(info.get("port") or DEFAULT_PORT), autostart=auto)

    def save(self) -> None:
        PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")


def web_name() -> str:
    """自動起動・ファイアウォール規則の名前（shared/apps.ps1 の $WebName と同じ決め方）"""
    infos = app_infos()
    w = next((i.get("web_name") for i in infos if i.get("web_name")), "")
    return w or (f"{infos[0]['name']}_web" if infos else "tools_web")


def service_name() -> str:
    """Linux の systemd サービス名（install.sh と同じ決め方）"""
    return web_name().replace("_", "-")


def _pythonw() -> str:
    exe = Path(sys.executable)
    if IS_WIN and exe.with_name("pythonw.exe").exists():
        exe = exe.with_name("pythonw.exe")
    return str(exe)


def _ps(script: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                          capture_output=True, text=True, timeout=timeout, **popen_kwargs())


def _q(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


# ------------------------------------------------------------------ 自動起動

def autostart_state() -> bool | None:
    """自動起動が登録されているか。分からなければ None"""
    try:
        if IS_WIN:
            name = web_name()
            lnk = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup" / f"{name}.lnk"
            r = subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True, **popen_kwargs())
            return r.returncode == 0 or lnk.exists()
        r = subprocess.run(["systemctl", "is-enabled", service_name()], capture_output=True, text=True)
        return r.stdout.strip() == "enabled"
    except (OSError, subprocess.SubprocessError):
        return None


def set_autostart(on: bool) -> str:
    """Windows: ログオン時のタスク（登録できなければスタートアップのショートカット）を登録・削除する。
    引数なしで起動するので、待ち受けは settings_web.json に従う。戻り値は利用者向けの説明（失敗時は例外）"""
    if not IS_WIN:
        svc = service_name()
        return (f"Linux では自動起動は systemd のサービス（{svc}）で管理しています。"
                f"変えるには sudo systemctl {'enable' if on else 'disable'} {svc} を実行してください")
    name, app, pyw = web_name(), str(APP_ROOT), _pythonw()
    icon = next((str(APP_ROOT / i["pkg"] / "assets" / "icon.ico") for i in app_infos()), "")
    if on:
        script = f"""
$ErrorActionPreference = 'Stop'
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) ({_q(name)} + '.lnk')
try {{
    $act = New-ScheduledTaskAction -Execute {_q(pyw)} -Argument '-m shared.web' -WorkingDirectory {_q(app)}
    $trg = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName {_q(name)} -Action $act -Trigger $trg -Settings $set -Force | Out-Null
    if (Test-Path $lnk) {{ Remove-Item $lnk -Force }}
    'task'
}} catch {{
    $ws = New-Object -ComObject WScript.Shell
    $l = $ws.CreateShortcut($lnk)
    $l.TargetPath = {_q(pyw)}; $l.Arguments = '-m shared.web'; $l.WorkingDirectory = {_q(app)}
    $l.IconLocation = {_q(icon)}; $l.Save()
    'startup'
}}"""
    else:
        script = f"""
Unregister-ScheduledTask -TaskName {_q(name)} -Confirm:$false -ErrorAction SilentlyContinue
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) ({_q(name)} + '.lnk')
if (Test-Path $lnk) {{ Remove-Item $lnk -Force }}
'off'"""
    r = _ps(script)
    if r.returncode != 0:
        raise RuntimeError(f"自動起動を変更できませんでした: {(r.stderr or r.stdout).strip()}")
    out = r.stdout.strip()
    if out == "startup":
        return "タスクを登録できなかったため、スタートアップに登録しました"
    return "ログオン時に自動で起動します" if on else "自動起動を解除しました"


# ------------------------------------------------------------------ ファイアウォール

def open_firewall(port: int) -> str:
    """Windows: 受信を許可する（管理者の確認が 1 回出る）。Linux は手順の説明を返す"""
    if not IS_WIN:
        return (f"Linux では必要に応じて sudo ufw allow {port}/tcp（firewalld なら firewall-cmd）で"
                f"受信を許可してください")
    ps1 = APP_ROOT / "web_firewall.ps1"
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                            "-Port", str(port), "-Name", web_name()], capture_output=True, text=True, timeout=300,
                           **popen_kwargs())
    except subprocess.TimeoutExpired:
        raise RuntimeError("ファイアウォールの設定が終わりませんでした") from None
    if r.returncode != 0:
        raise RuntimeError("ファイアウォールを設定できませんでした（管理者の確認が拒否された可能性があります）。"
                           "後で web_firewall.bat を実行してください")
    return f"ファイアウォールで TCP {port} の受信を許可しました"
