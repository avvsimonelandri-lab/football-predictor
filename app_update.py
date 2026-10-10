"""Aggiornamento automatico del programma tramite le "release" di GitHub.

Ogni volta che sul repository GitHub viene pubblicata una nuova versione (tag vX.Y),
GitHub costruisce da solo FootballPredictorPro.exe (vedi .github/workflows/build.yml)
e lo allega alla release. L'app controlla la release più recente e, se è più nuova,
la scarica e si sostituisce da sola al riavvio.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request

from data_sources import _CTX

API = "https://api.github.com/repos/{repo}/releases/latest"
ASSET = "FootballPredictorPro.exe"


def _ver(s: str) -> tuple:
    s = (s or "").strip().lstrip("vV")
    out = []
    for p in s.split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (3 - len(out)))


def check(repo: str, current: str) -> dict | None:
    """Restituisce {'version','url','notes','page'} se c'è una versione più nuova, altrimenti None."""
    repo = (repo or "").strip().strip("/")
    if repo.startswith("https://github.com/"):
        repo = repo[len("https://github.com/"):]
    if repo.count("/") != 1:
        return None
    req = urllib.request.Request(API.format(repo=repo), headers={"User-Agent": "FootballPredictorPro",
                                                                 "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=20, context=_CTX) as r:
            rel = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[aggiornamento programma] {e}")
        return None
    tag = rel.get("tag_name", "")
    if _ver(tag) <= _ver(current):
        return None
    url = next((a["browser_download_url"] for a in rel.get("assets", []) if a.get("name") == ASSET), None)
    if not url:
        return None
    return {"version": tag.lstrip("vV"), "url": url, "notes": (rel.get("body") or "")[:1500],
            "page": rel.get("html_url", "")}


def can_self_update() -> bool:
    return os.name == "nt" and getattr(sys, "frozen", False)


def download_and_restart(info: dict, progress=None) -> tuple[bool, str]:
    """Scarica il nuovo exe accanto a quello attuale e lo sostituisce appena l'app si chiude."""
    if not can_self_update():
        return False, "L'aggiornamento automatico funziona solo con il programma .exe su Windows."
    exe = sys.executable
    folder = os.path.dirname(exe)
    new = os.path.join(folder, "FootballPredictorPro.nuovo.exe")
    try:
        req = urllib.request.Request(info["url"], headers={"User-Agent": "FootballPredictorPro"})
        with urllib.request.urlopen(req, timeout=120, context=_CTX) as r, open(new, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)
        if os.path.getsize(new) < 5_000_000:
            raise RuntimeError("file scaricato incompleto")
    except Exception as e:
        try:
            os.remove(new)
        except OSError:
            pass
        return False, f"Download non riuscito: {e}"

    backup = os.path.join(folder, "FootballPredictorPro_vecchia_versione.exe")
    bat = os.path.join(tempfile.gettempdir(), "fpp_aggiorna.bat")
    with open(bat, "w", encoding="mbcs" if os.name == "nt" else "utf-8") as f:
        f.write(f'''@echo off
set /a n=0
:attendi
tasklist /FI "PID eq {os.getpid()}" | find "{os.getpid()}" >nul
if not errorlevel 1 (
  set /a n+=1
  if %n% gtr 60 goto fine
  timeout /t 1 /nobreak >nul
  goto attendi
)
copy /y "{exe}" "{backup}" >nul
move /y "{new}" "{exe}" >nul
start "" "{exe}"
:fine
del "%~f0"
''')
    subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000)
    return True, "Aggiornamento scaricato: il programma si riavvia."
