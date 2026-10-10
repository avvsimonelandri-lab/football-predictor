"""Storico reale dei pronostici.

A ogni aggiornamento (anche quelli automatici ad app chiusa) l'app salva il pronostico
delle partite dei prossimi 7 giorni. Fino al calcio d'inizio il pronostico viene
rinfrescato con i dati più recenti; dopo il fischio non viene più toccato.
Quando arriva il risultato, la partita viene "chiusa" e conteggiata nelle statistiche.

Il file dati/storico.json non viene mai cancellato dagli aggiornamenti dei dati.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from data_sources import match_team
from predictor import FootballPredictor, _rps, best_value

FILE = "storico.json"
SAVE_DAYS_AHEAD = 7


def path(data_dir: str) -> str:
    return os.path.join(data_dir, FILE)


def load(data_dir: str) -> dict:
    p = path(data_dir)
    if not os.path.exists(p):
        return {"entries": {}}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("entries", {})
        return d
    except Exception:
        return {"entries": {}}


def save(data_dir: str, h: dict):
    p = path(data_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False)
    os.replace(tmp, p)


def _key(league, f):
    return f"{league}|{f['date']}|{f['home']}|{f['away']}"


def _started(f, now: datetime) -> bool:
    if f["date"] < now.date().isoformat():
        return True
    if f["date"] > now.date().isoformat():
        return False
    t = (f.get("time") or "").strip()
    try:
        hh, mm = t.split(":")
        return now >= now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0) - timedelta(minutes=5)
    except Exception:
        return now.hour >= 12   # orario sconosciuto: si smette di aggiornare da mezzogiorno


def snapshot(h: dict, data: dict, blend_w: float, now: datetime | None = None, xg_beta: float | None = None) -> int:
    """Salva/aggiorna i pronostici delle prossime partite. Restituisce quante voci ha scritto."""
    now = now or datetime.now()
    limit = (now.date() + timedelta(days=SAVE_DAYS_AHEAD)).isoformat()
    n = 0
    for league, ld in data.get("leagues", {}).items():
        fx = [f for f in ld.get("fixtures", []) if f["date"] <= limit and not _started(f, now)]
        if not fx:
            continue
        model = FootballPredictor(ld, blend_w=blend_w, extras=False, xg_beta=xg_beta)
        for f in fx:
            r = model.predict_detailed(f["home"], f["away"], f.get("odds"), extras=False)
            if not r:
                continue
            k = _key(league, f)
            old = h["entries"].get(k)
            if old and old.get("result"):
                continue
            p = model.best_1x2(r)
            b = r.get("blend") or {}
            over = (b.get("UnderOver25") or r["UnderOver25"])["Over"]
            bv = best_value(r)
            (sh, sa), _ = r["exact_scores"][0]
            h["entries"][k] = {
                "league": league, "date": f["date"], "time": f.get("time", ""),
                "home": f["home"], "away": f["away"], "saved_at": now.isoformat(timespec="minutes"),
                "p": {kk: round(v, 4) for kk, v in p.items()},
                "p_model": {kk: round(v, 4) for kk, v in r["1X2"].items()},
                "combined": bool(r.get("blend")),
                "over": round(over, 4), "gg": round(r["GoalNoGoal"]["Goal"], 4),
                "score": f"{sh}-{sa}", "odds": f.get("odds"),
                "value": {"market": bv["market"], "odds": bv["odds"], "ev": round(bv["ev"], 4)} if bv else None,
                "result": None,
            }
            n += 1
    return n


def resolve(h: dict, data: dict) -> int:
    """Abbina i risultati arrivati ai pronostici salvati."""
    n = 0
    for e in h["entries"].values():
        if e.get("result"):
            continue
        ld = data.get("leagues", {}).get(e["league"])
        if not ld:
            continue
        d0 = date.fromisoformat(e["date"])
        cands = [m for m in ld.get("matches", []) if m.get("season") == "cur"
                 and abs((date.fromisoformat(m["date"]) - d0).days) <= 4]
        if not cands:
            continue
        names = sorted({m["home"] for m in cands} | {m["away"] for m in cands})
        hh, aa = match_team(e["home"], names), match_team(e["away"], names)
        for m in cands:
            if m["home"] == hh and m["away"] == aa:
                e["result"] = {"hg": m["hg"], "ag": m["ag"]}
                n += 1
                break
    # pulizia: partite mai giocate/abbinate da oltre 30 giorni
    old = (date.today() - timedelta(days=30)).isoformat()
    for k in [k for k, e in h["entries"].items() if not e.get("result") and e["date"] < old]:
        del h["entries"][k]
    return n


def _outcome(r):
    return "1" if r["hg"] > r["ag"] else "X" if r["hg"] == r["ag"] else "2"


def value_won(v, r) -> bool:
    m = v["market"]
    if m in ("1", "X", "2"):
        return _outcome(r) == m
    tot = r["hg"] + r["ag"]
    return tot > 2.5 if m.startswith("Over") else tot < 2.5


def summary(h: dict, league: str | None = None) -> dict:
    es = [e for e in h["entries"].values() if e.get("result") and (league is None or e["league"] == league)]
    n = len(es)
    out = {"n": n, "pending": len([e for e in h["entries"].values()
                                   if not e.get("result") and (league is None or e["league"] == league)])}
    if not n:
        return out
    hit = sum(max(e["p"], key=e["p"].get) == _outcome(e["result"]) for e in es)
    out["acc_1x2"] = hit / n
    out["rps"] = sum(_rps(e["p"], _outcome(e["result"])) for e in es) / n
    out["acc_over"] = sum((e["over"] > 0.5) == (e["result"]["hg"] + e["result"]["ag"] > 2.5) for e in es) / n
    out["acc_gg"] = sum((e["gg"] > 0.5) == (e["result"]["hg"] > 0 and e["result"]["ag"] > 0) for e in es) / n
    out["acc_score"] = sum(e["score"] == f"{e['result']['hg']}-{e['result']['ag']}" for e in es) / n
    sure = [e for e in es if max(e["p"].values()) >= 0.6]
    if sure:
        out["n_sure"] = len(sure)
        out["acc_sure"] = sum(max(e["p"], key=e["p"].get) == _outcome(e["result"]) for e in sure) / len(sure)
    vb = [e for e in es if e.get("value")]
    if vb:
        won = [e for e in vb if value_won(e["value"], e["result"])]
        profit = sum(e["value"]["odds"] - 1 for e in won) - (len(vb) - len(won))
        out["value"] = {"n": len(vb), "won": len(won), "profit": profit, "roi": profit / len(vb),
                        "avg_odds": sum(e["value"]["odds"] for e in vb) / len(vb)}
    return out


def recent(h: dict, league: str | None = None, limit: int = 60) -> list:
    es = [e for e in h["entries"].values() if league is None or e["league"] == league]
    es.sort(key=lambda e: (e["date"], e.get("time") or ""), reverse=True)
    return es[:limit]
