"""football-data.org (API v4, piano gratuito con chiave personale).

Serve a una cosa sola: avere i risultati poche ore dopo il fischio finale
(football-data.co.uk aggiorna i suoi file un paio di volte a settimana).
I risultati in più vengono aggiunti ai dati del campionato; se la chiave manca
o l'API non risponde, l'app funziona come prima.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import date, datetime, timedelta

from data_sources import _CTX, match_team

API = "https://api.football-data.org/v4/competitions/{code}/matches?season={season}"

# campionati coperti dal piano gratuito
CODES = {
    "Serie A": "SA", "Premier League": "PL", "LaLiga": "PD", "Bundesliga": "BL1",
    "Ligue 1": "FL1", "Eredivisie": "DED", "Primeira Liga": "PPL", "Championship": "ELC",
}


def _last_sunday(year, month):
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def utc_to_rome(dt: datetime) -> datetime:
    """Ora italiana senza dipendenze esterne (ora legale: ultima domenica di marzo -> ultima di ottobre, alle 01:00 UTC)."""
    start = datetime.combine(_last_sunday(dt.year, 3), datetime.min.time()).replace(hour=1)
    end = datetime.combine(_last_sunday(dt.year, 10), datetime.min.time()).replace(hour=1)
    return dt + timedelta(hours=2 if start <= dt < end else 1)


def fetch(code: str, season: int, key: str) -> list | None:
    req = urllib.request.Request(API.format(code=code, season=season),
                                 headers={"X-Auth-Token": key, "User-Agent": "FootballPredictorPro"})
    try:
        with urllib.request.urlopen(req, timeout=25, context=_CTX) as r:
            return json.loads(r.read().decode("utf-8")).get("matches", [])
    except Exception as e:
        print(f"[football-data.org] {code}: {e}")
        return None


def _parse(m):
    try:
        dt = utc_to_rome(datetime.strptime(m["utcDate"], "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return None
    ht, at = m.get("homeTeam") or {}, m.get("awayTeam") or {}
    return {"dt": dt, "status": m.get("status"),
            "home_names": [n for n in (ht.get("shortName"), ht.get("name")) if n],
            "away_names": [n for n in (at.get("shortName"), at.get("name")) if n],
            "ft": ((m.get("score") or {}).get("fullTime") or {}),
            "ht": ((m.get("score") or {}).get("halfTime") or {})}


def _map(names, teams):
    for n in names:
        t = match_team(n, teams)
        if t:
            return t
    return None


def enrich(name: str, ld: dict, season_start: int, key: str, today: date | None = None) -> int:
    """Aggiunge a ld i risultati che la fonte principale non ha ancora. Restituisce quanti ne ha aggiunti."""
    code = CODES.get(name)
    if not code or not key:
        return 0
    raw = fetch(code, season_start, key)
    if raw is None:
        return 0
    today = today or date.today()
    teams = ld.get("teams") or []
    if not teams:
        return 0
    cur = [m for m in ld["matches"] if m.get("season") == "cur"]
    have = {(m["home"], m["away"]) for m in cur}
    dates = {}
    for m in cur:
        dates.setdefault((m["home"], m["away"]), []).append(date.fromisoformat(m["date"]))

    def known(h, a, d):   # stessa partita già presente (entro 3 giorni)?
        return any(abs((d - x).days) <= 3 for x in dates.get((h, a), []))

    added = 0
    upcoming = []
    for r in map(_parse, raw):
        if not r:
            continue
        h, a = _map(r["home_names"], teams), _map(r["away_names"], teams)
        if not h or not a or h == a:
            continue
        if r["status"] == "FINISHED" and r["ft"].get("home") is not None:
            if known(h, a, r["dt"].date()):
                continue
            m = {"date": r["dt"].date().isoformat(), "time": r["dt"].strftime("%H:%M"), "home": h, "away": a,
                 "hg": int(r["ft"]["home"]), "ag": int(r["ft"]["away"]), "odds": None, "season": "cur", "src": "fdorg"}
            if r["ht"].get("home") is not None:
                m["hthg"], m["htag"] = int(r["ht"]["home"]), int(r["ht"]["away"])
            ld["matches"].append(m)
            have.add((h, a))
            dates.setdefault((h, a), []).append(r["dt"].date())
            added += 1
        elif r["status"] in ("SCHEDULED", "TIMED") and today <= r["dt"].date() <= today + timedelta(days=21):
            upcoming.append({"date": r["dt"].date().isoformat(), "time": r["dt"].strftime("%H:%M"),
                             "home": h, "away": a, "odds": None})
    if added:
        # le partite appena giocate non sono più "prossime"
        ld["fixtures"] = [f for f in ld.get("fixtures", []) if not known(f["home"], f["away"], date.fromisoformat(f["date"]))]
        cur = [m for m in ld["matches"] if m.get("season") == "cur"]
        ld["played"] = len(cur)
        ld["last_result"] = max(m["date"] for m in cur)
        ld["source"] += " + football-data.org"
    if not ld.get("fixtures") and upcoming:
        ld["fixtures"] = sorted(upcoming, key=lambda f: (f["date"], f["time"]))[:40]
    return added


def enrich_all(data: dict, season_start: int, key: str, log=print) -> int:
    if not key:
        return 0
    tot = 0
    for i, name in enumerate([n for n in CODES if n in data.get("leagues", {})]):
        if i:
            time.sleep(6.5)   # piano gratuito: max 10 richieste al minuto
        try:
            n = enrich(name, data["leagues"][name], season_start, key)
            tot += n
        except Exception as e:
            log(f"football-data.org {name}: {e}")
    return tot
