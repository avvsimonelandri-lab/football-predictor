"""Impostazioni utente (dati/impostazioni.json)."""
from __future__ import annotations

import json
import os

DEFAULTS = {
    "fdorg_key": "",            # chiave gratuita football-data.org (risultati in giornata)
    "github_repo": "",          # es. "mariorossi/football-predictor" (aggiornamento automatico del programma)
    "notify_value": True,       # notifica Windows per nuove segnalazioni di valore
    "notify_update": False,     # notifica Windows a ogni aggiornamento dati
    "auto_app_update": True,    # controlla da solo se c'è una nuova versione del programma
    "last_app_check": "",
    "theme": "light",           # aspetto: light (sfondo bianco), dark, system
}


def _path(data_dir: str) -> str:
    return os.path.join(data_dir, "impostazioni.json")


def load(data_dir: str) -> dict:
    s = dict(DEFAULTS)
    try:
        with open(_path(data_dir), encoding="utf-8") as f:
            s.update(json.load(f))
    except Exception:
        pass
    # su GitHub Actions la chiave arriva come segreto d'ambiente
    if not s.get("fdorg_key") and os.environ.get("FDORG_KEY"):
        s["fdorg_key"] = os.environ["FDORG_KEY"]
    return s


def save(data_dir: str, s: dict):
    p = _path(data_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({k: s.get(k, v) for k, v in DEFAULTS.items()}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
