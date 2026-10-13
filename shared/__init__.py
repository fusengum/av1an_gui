"""ツール共通の部分

環境構築（外部ツールの自動取得）・処理の実行・映像の解析・設定画面・Web 画面・セットアップのコマンド。
ツールはアプリのフォルダ直下のパッケージで、app.json（名前・説明など）と env.py（使う外部ツール）を持つ。
apps() はそれを探して見つかったものだけを対象にする（ツールの組み合わせは配布物ごとに違ってよい）。
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def app_infos() -> list[dict]:
    """入っているツールの app.json（order 順）"""
    out = []
    for f in ROOT.glob("*/app.json"):
        if not (f.parent / "__init__.py").exists():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        d.setdefault("pkg", f.parent.name)
        d.setdefault("name", f.parent.name)
        d.setdefault("order", 100)
        d.setdefault("folders", [])
        out.append(d)
    return sorted(out, key=lambda d: (d["order"], d["pkg"]))


def apps() -> list[str]:
    """入っているツールのパッケージ名"""
    return [d["pkg"] for d in app_infos()]


def app_name(pkg: str) -> str:
    return next((d["name"] for d in app_infos() if d["pkg"] == pkg), pkg)


def folders(pkgs: list[str] | None = None) -> list[dict]:
    """ツールが手動指定を求めるフォルダ（自動取得できない外部ツールの場所など）"""
    pk = pkgs or apps()
    out = []
    for d in app_infos():
        if d["pkg"] in pk:
            for fd in d["folders"]:
                out.append({**fd, "pkg": d["pkg"]})
    return out


def env_module(pkg: str):
    try:
        return importlib.import_module(f"{pkg}.env")
    except Exception:  # noqa: BLE001
        return None


def tool_list(pkgs: list[str] | None = None) -> list[tuple]:
    """入っているツールが使う外部ツールの一覧（重複なし。どれかで必須なら必須）"""
    out, seen = [], set()
    for p in pkgs or apps():
        m = env_module(p)
        for t in getattr(m, "TOOL_LIST", []):
            if t[0] not in seen:
                seen.add(t[0])
                out.append(t)
            elif t[2]:
                i = next(i for i, x in enumerate(out) if x[0] == t[0])
                if not out[i][2]:
                    out[i] = t
    return out
