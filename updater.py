"""Salvataggio dati e aggiornamento automatico.

L'app si tiene aggiornata in tre modi:
  1. all'avvio, se i dati hanno più di AUTO_MAX_AGE_H ore;
  2. mentre è aperta, con un controllo periodico;
  3. anche ad app chiusa, tramite l'Utilità di pianificazione di Windows
     (attività "FootballPredictorPro_Aggiornamento", che lancia l'exe con --update).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime

import data_sources
import fdorg
import history
import notifier
import predictor
import settings

APP_NAME = "FootballPredictorPro"
DATA_FILE = "football_data.json"
AUTO_MAX_AGE_H = 4          # oltre questa età i dati vengono riscaricati
TASK_NAME = "FootballPredictorPro_Aggiornamento"
TASK_EVERY_H = 6


# ------------------------------------------------------------------ percorsi
def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def data_dir() -> str:
    """Cartella dati accanto all'exe; se non scrivibile, in %LOCALAPPDATA%."""
    d = os.path.join(app_dir(), "dati")
    try:
        os.makedirs(d, exist_ok=True)
        test = os.path.join(d, ".w")
        with open(test, "w") as f:
            f.write("1")
        os.remove(test)
        return d
    except OSError:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, APP_NAME)
        os.makedirs(d, exist_ok=True)
        return d


def data_path() -> str:
    return os.path.join(data_dir(), DATA_FILE)


def log(msg: str):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    try:
        p = os.path.join(data_dir(), "aggiornamenti.log")
        if os.path.exists(p) and os.path.getsize(p) > 500_000:
            os.replace(p, p + ".old")
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------ dati
def load_data() -> dict:
    p = data_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if d.get("version") == 2 else {}   # il vecchio formato (solo classifiche) non è più usato
    except Exception as e:
        log(f"Errore lettura dati: {e}")
        return {}


def save_data(d: dict):
    p = data_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, p)  # scrittura atomica: niente file corrotti se il PC si spegne


def data_age_hours(d: dict) -> float | None:
    try:
        return (datetime.now() - datetime.fromisoformat(d["updated_at"])).total_seconds() / 3600
    except Exception:
        return None


def needs_update(d: dict, max_age_h: float = AUTO_MAX_AGE_H) -> bool:
    age = data_age_hours(d)
    return age is None or age >= max_age_h


# ------------------------------------------------------------------ blocco (app + attività pianificata insieme)
def _lock_path():
    return os.path.join(data_dir(), "update.lock")


def acquire_lock() -> bool:
    p = _lock_path()
    try:
        if os.path.exists(p) and time.time() - os.path.getmtime(p) < 20 * 60:
            return False
        with open(p, "w") as f:
            f.write(str(os.getpid()))
        return True
    except OSError:
        return True


def release_lock():
    try:
        os.remove(_lock_path())
    except OSError:
        pass


# ------------------------------------------------------------------ aggiornamento
def run_update(progress=None) -> tuple[dict, list[str]]:
    if not acquire_lock():
        log("Aggiornamento già in corso (altra istanza): salto.")
        return load_data(), ["in corso"]
    try:
        old = load_data()
        cfg = settings.load(data_dir())
        log("Aggiornamento dati avviato")
        new, errors = data_sources.fetch_all(old, progress=progress)

        # risultati in giornata da football-data.org (se è stata inserita la chiave gratuita)
        if cfg.get("fdorg_key") and new.get("leagues"):
            n = fdorg.enrich_all(new, data_sources.season_start_year(), cfg["fdorg_key"], log=log)
            log(f"football-data.org: {n} risultati recenti aggiunti")

        leagues = new.get("leagues", {})
        # xG stimati dai tiri: quanto peso dare loro rispetto ai gol (scelto sul passato recente)
        shot_leagues = [n for n, ld in leagues.items() if predictor.has_shots(ld)]
        by_beta = {}
        if shot_leagues:
            for b in predictor.XG_BETAS:
                recs = {}
                for name in shot_leagues:
                    try:
                        _, recs[name] = predictor.backtest(leagues[name], days=70, return_recs=True, xg_beta=b)
                    except Exception as e:
                        recs[name] = []
                        log(f"Backtest xG {name}: {e}")
                by_beta[b] = recs
            scores = {}
            for b, recs in by_beta.items():
                allr = [r for rs in recs.values() for r in rs]
                if allr:
                    scores[b] = sum(predictor._rps(r["p"], r["out"]) for r in allr) / len(allr)
            beta = min(scores, key=scores.get) if scores else predictor.DEFAULT_XG_BETA
            n_xg = len([r for rs in by_beta.get(beta, {}).values() for r in rs])
            new["xg"] = {"beta": beta, "n": n_xg, "rps": {str(k): v for k, v in scores.items()},
                         "leagues": shot_leagues}
            log(f"xG dai tiri: peso scelto {beta:.1f} (RPS {', '.join(f'{k}: {v:.4f}' for k, v in scores.items())})")
        else:
            beta = (old.get("xg") or {}).get("beta", predictor.DEFAULT_XG_BETA)
            new["xg"] = {"beta": beta, "n": 0, "rps": {}, "leagues": []}

        # affidabilità (backtest ultime 10 settimane) e peso modello/quote
        rel, all_recs = {}, {}
        for name, ld in leagues.items():
            if name in shot_leagues:
                all_recs[name] = by_beta[beta].get(name, [])
                continue
            try:
                _, all_recs[name] = predictor.backtest(ld, days=70, return_recs=True)
            except Exception as e:
                all_recs[name] = []
                log(f"Backtest {name}: {e}")
        blend = predictor.optimal_blend([r for rs in all_recs.values() for r in rs])
        if not blend:
            prev = old.get("blend") or {}
            blend = {"w": prev.get("w", predictor.DEFAULT_BLEND), "n": prev.get("n", 0), "default": not prev}
        new["blend"] = blend
        for name, recs in all_recs.items():
            rel[name] = predictor.summarize_backtest(recs, blend["w"])
        new["reliability"] = rel

        if leagues:
            save_data(new)
            # storico pronostici: chiude le partite giocate e salva le prossime
            try:
                h = history.load(data_dir())
                closed = history.resolve(h, new)
                saved = history.snapshot(h, new, blend["w"], xg_beta=beta)
                nv = notifier.notify_new_values(h, cfg.get("notify_value", True))
                history.save(data_dir(), h)
                log(f"Storico: {closed} risultati abbinati, {saved} pronostici salvati/aggiornati, "
                    f"{nv} nuove segnalazioni di valore")
            except Exception as e:
                log(f"Errore storico: {e}")
            if cfg.get("notify_update"):
                notifier.notify("Football Predictor: dati aggiornati",
                                f"{len(leagues)} campionati aggiornati" + (f"; problemi: {', '.join(errors)}" if errors else ""))
        ok = len(leagues) - len([e for e in errors if e in leagues])
        log(f"Aggiornamento terminato: {ok} campionati aggiornati, problemi: {', '.join(errors) or 'nessuno'}")
        return new, errors
    finally:
        release_lock()


# ------------------------------------------------------------------ Utilità di pianificazione Windows
def _exe_command() -> str | None:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --update'
    if os.name == "nt":
        pyw = sys.executable.replace("python.exe", "pythonw.exe")
        return f'"{pyw}" "{os.path.join(app_dir(), "app.py")}" --update'
    return None


def _run(args):
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    return subprocess.run(args, capture_output=True, text=True, creationflags=flags)


def scheduler_available() -> bool:
    return os.name == "nt"


def task_exists() -> bool:
    if not scheduler_available():
        return False
    return _run(["schtasks", "/Query", "/TN", TASK_NAME]).returncode == 0


def enable_task() -> tuple[bool, str]:
    cmd = _exe_command()
    if not scheduler_available() or not cmd:
        return False, "Disponibile solo su Windows."
    r = _run(["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/SC", "HOURLY", "/MO", str(TASK_EVERY_H), "/TR", cmd])
    if r.returncode == 0:
        log(f"Attività pianificata creata: ogni {TASK_EVERY_H} ore")
        return True, f"Aggiornamento automatico attivo: ogni {TASK_EVERY_H} ore, anche ad app chiusa."
    return False, (r.stderr or r.stdout or "Errore sconosciuto").strip()


def disable_task() -> tuple[bool, str]:
    if not scheduler_available():
        return False, "Disponibile solo su Windows."
    r = _run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
    return r.returncode == 0, (r.stderr or r.stdout).strip()
