"""Raccolta dati: risultati, calendario e quote.

Sostituisce il vecchio scraper Selenium su diretta.it (lento, richiedeva Chrome,
si rompeva a ogni cambio grafico del sito e leggeva solo la classifica).

Fonti:
  1. football-data.co.uk  -> CSV con tutte le partite giocate + quote medie dei bookmaker
                              + file fixtures.csv con le prossime partite e relative quote.
  2. openfootball (GitHub) -> JSON con risultati e calendario completo (riserva).

Per ogni campionato si sceglie la fonte con il risultato più recente; se una fonte
non risponde si usa l'altra; se nessuna risponde si conservano i dati già salvati.
"""
from __future__ import annotations

import csv
import io
import json
import re
import ssl
import time
import unicodedata
import urllib.request
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher

from leagues import LEAGUES

FD_BASE = "https://www.football-data.co.uk/mmz4281/{code}/{div}.csv"
FD_FIXTURES = "https://www.football-data.co.uk/fixtures.csv"
OF_BASE = "https://raw.githubusercontent.com/openfootball/football.json/master/{season}/{key}.json"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FootballPredictorPro/2.0"


# --------------------------------------------------------------------------- rete
def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_CTX = _ssl_context()


def http_get(url: str, timeout: int = 25, retries: int = 2) -> bytes | None:
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
        except Exception as e:  # rete assente, timeout, DNS...
            last = e
        time.sleep(1.5 * (attempt + 1))
    print(f"[rete] {url} -> {last}")
    return None


# --------------------------------------------------------------------------- stagioni
def season_start_year(d: date | None = None) -> int:
    d = d or date.today()
    return d.year if d.month >= 7 else d.year - 1


def fd_season_code(start: int) -> str:
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def of_season_code(start: int) -> str:
    return f"{start}-{(start + 1) % 100:02d}"


def season_label(start: int) -> str:
    return f"{start}/{(start + 1) % 100:02d}"


# --------------------------------------------------------------------------- utilità
def _parse_date(s: str) -> str | None:
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _num(v):
    try:
        f = float(v)
        return f if f > 1.0 else None
    except (TypeError, ValueError):
        return None


def _first(row: dict, *keys):
    for k in keys:
        v = _num(row.get(k))
        if v:
            return v
    return None


def _uk_to_it(t: str) -> str:
    """football-data.co.uk indica gli orari in ora inglese: in Italia è sempre un'ora avanti."""
    m = re.match(r"^(\d{1,2}):(\d{2})$", (t or "").strip())
    if not m:
        return (t or "").strip()
    return f"{(int(m.group(1)) + 1) % 24:02d}:{m.group(2)}"


def _odds_from_row(row: dict) -> dict | None:
    odds = {
        "H": _first(row, "AvgH", "BbAvH", "B365H", "PSH"),
        "D": _first(row, "AvgD", "BbAvD", "B365D", "PSD"),
        "A": _first(row, "AvgA", "BbAvA", "B365A", "PSA"),
        "O25": _first(row, "Avg>2.5", "BbAv>2.5", "B365>2.5", "P>2.5"),
        "U25": _first(row, "Avg<2.5", "BbAv<2.5", "B365<2.5", "P<2.5"),
    }
    return odds if odds["H"] and odds["D"] and odds["A"] else None


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def normalize_team(name: str) -> str:
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"\b(fc|cf|ac|as|ss|ssc|us|sc|afc|calcio|club|de|real|1\.|sv|vfb|vfl|tsg|rc|ogc|losc|stade|olympique|ud|cd|rcd|sd|ca|bc|hellas|acf|fk|sk)\b", " ", n)
    n = re.sub(r"\b\d{4}\b", " ", n)
    n = re.sub(r"[^a-z ]", " ", n)
    return " ".join(n.split())


def match_team(name: str, candidates: list[str]) -> str | None:
    """Associa un nome squadra di una fonte al nome usato dall'altra fonte."""
    if name in candidates:
        return name
    nn = normalize_team(name)
    best, score = None, 0.0
    for c in candidates:
        cn = normalize_team(c)
        if not cn:
            continue
        s = SequenceMatcher(None, nn, cn).ratio()
        if cn in nn or nn in cn:
            s = max(s, 0.9)
        if s > score:
            best, score = c, s
    return best if score >= 0.72 else None


# --------------------------------------------------------------------------- football-data.co.uk
def fd_results(div: str, start: int) -> list[dict] | None:
    raw = http_get(FD_BASE.format(code=fd_season_code(start), div=div))
    if not raw:
        return None
    out = []
    for row in csv.DictReader(io.StringIO(_decode(raw))):
        d = _parse_date(row.get("Date"))
        home, away = (row.get("HomeTeam") or "").strip(), (row.get("AwayTeam") or "").strip()
        try:
            hg, ag = int(float(row["FTHG"])), int(float(row["FTAG"]))
        except (KeyError, TypeError, ValueError):
            continue
        if not (d and home and away):
            continue
        item = {"date": d, "time": _uk_to_it(row.get("Time")), "home": home, "away": away,
                "hg": hg, "ag": ag, "odds": _odds_from_row(row)}
        hthg, htag = _int(row.get("HTHG")), _int(row.get("HTAG"))
        if hthg is not None and htag is not None and hthg <= hg and htag <= ag:
            item["hthg"], item["htag"] = hthg, htag
        st = {k: _int(row.get(col)) for k, col in STAT_COLS.items()}
        if any(v is not None for v in st.values()):
            item["st"] = {k: v for k, v in st.items() if v is not None}
        out.append(item)
    return out


# colonne statistiche di football-data.co.uk -> nomi interni
STAT_COLS = {"hs": "HS", "as": "AS", "hst": "HST", "ast": "AST", "hc": "HC", "ac": "AC",
             "hy": "HY", "ay": "AY", "hr": "HR", "ar": "AR", "hf": "HF", "af": "AF"}


def _int(v):
    try:
        if v is None or str(v).strip() == "":
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


_fd_fixtures_cache: dict | None = None


def fd_fixtures_all() -> dict:
    """Prossime partite di tutte le divisioni (scaricato una volta per aggiornamento)."""
    global _fd_fixtures_cache
    if _fd_fixtures_cache is not None:
        return _fd_fixtures_cache
    res: dict[str, list] = {}
    raw = http_get(FD_FIXTURES)
    if raw:
        for row in csv.DictReader(io.StringIO(_decode(raw))):
            div = (row.get("Div") or "").strip()
            d = _parse_date(row.get("Date"))
            home, away = (row.get("HomeTeam") or "").strip(), (row.get("AwayTeam") or "").strip()
            if div and d and home and away:
                res.setdefault(div, []).append({"date": d, "time": _uk_to_it(row.get("Time")),
                                                "home": home, "away": away, "odds": _odds_from_row(row)})
    _fd_fixtures_cache = res
    return res


# --------------------------------------------------------------------------- openfootball
def of_season(key: str, start: int) -> tuple[list, list] | None:
    raw = http_get(OF_BASE.format(season=of_season_code(start), key=key))
    if not raw:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    results, calendar = [], []
    for m in data.get("matches", []):
        sc = m.get("score")
        # i risultati normali sono {"ft": [h, a], "ht": [...]}, ma alcuni (es. gli 0-0) sono solo [h, a]
        ht = None
        if isinstance(sc, dict):
            ft, ht = sc.get("ft"), sc.get("ht")
        elif isinstance(sc, list):
            ft = sc
            if list(sc) == [0, 0]:
                ht = [0, 0]
        else:
            ft = None
        item = {"date": m.get("date"), "time": m.get("time", ""), "home": m.get("team1"), "away": m.get("team2")}
        if not (item["date"] and item["home"] and item["away"]):
            continue
        if ft and len(ft) == 2:
            item.update(hg=int(ft[0]), ag=int(ft[1]), odds=None)
            if ht and len(ht) == 2 and int(ht[0]) <= item["hg"] and int(ht[1]) <= item["ag"]:
                item["hthg"], item["htag"] = int(ht[0]), int(ht[1])
            results.append(item)
        else:
            item["odds"] = None
            calendar.append(item)
    return results, calendar


# --------------------------------------------------------------------------- aggiornamento campionato
def _last_date(matches):
    return max((m["date"] for m in matches), default="0000-00-00")


def fetch_league(name: str, today: date | None = None) -> dict | None:
    cfg = LEAGUES[name]
    today = today or date.today()
    start = season_start_year(today)
    today_s = today.isoformat()
    horizon = (today + timedelta(days=21)).isoformat()

    # Fonte 1: football-data
    fd_cur = fd_results(cfg["fd"], start) if cfg.get("fd") else None
    # Fonte 2: openfootball
    of_cur = of_season(cfg["of"], start) if cfg.get("of") else None

    use = None
    if fd_cur:
        use = "fd"
        if of_cur and of_cur[0] and _last_date(of_cur[0]) > _last_date(fd_cur):
            # openfootball più aggiornato: lo si usa per entrambe le stagioni (stessi nomi squadra)
            use = "of"
    elif of_cur and (of_cur[0] or of_cur[1]):
        use = "of"
    elif cfg.get("fd") and fd_cur == []:
        use = "fd"   # stagione appena iniziata, nessuna partita ancora giocata
    if use is None:
        return None

    if use == "fd":
        cur = fd_cur or []
        prev = fd_results(cfg["fd"], start - 1) or []
        teams_cur = sorted({m["home"] for m in cur} | {m["away"] for m in cur})
        fixtures = [f for f in fd_fixtures_all().get(cfg["fd"], []) if today_s <= f["date"] <= horizon]
        if not fixtures and of_cur and of_cur[1]:
            # calendario dalla riserva, nomi convertiti in quelli di football-data
            names = teams_cur or sorted({m["home"] for m in prev} | {m["away"] for m in prev})
            for f in of_cur[1]:
                if today_s <= f["date"] <= horizon:
                    h, a = match_team(f["home"], names), match_team(f["away"], names)
                    if h and a and h != a:
                        fixtures.append({**f, "home": h, "away": a})
        source = "football-data.co.uk"
    else:
        cur, cal = of_cur
        prev_of = of_season(cfg["of"], start - 1)
        prev = prev_of[0] if prev_of else []
        teams_cur = sorted({m["home"] for m in cur + cal} | {m["away"] for m in cur + cal})
        fixtures = [f for f in cal if today_s <= f["date"] <= horizon]
        # quote da football-data, se il calendario fd contiene la stessa partita
        fd_fx = fd_fixtures_all().get(cfg.get("fd") or "", [])
        if fd_fx:
            fd_names = sorted({f["home"] for f in fd_fx} | {f["away"] for f in fd_fx})
            for f in fixtures:
                h, a = match_team(f["home"], fd_names), match_team(f["away"], fd_names)
                for g in fd_fx:
                    if g["home"] == h and g["away"] == a and g.get("odds"):
                        f["odds"] = g["odds"]
        source = "openfootball (GitHub)"

    if not teams_cur:
        teams_cur = sorted({f["home"] for f in fixtures} | {f["away"] for f in fixtures})

    for m in cur:
        m["season"] = "cur"
    for m in prev:
        m["season"] = "prev"
    fixtures.sort(key=lambda f: (f["date"], f.get("time") or ""))

    return {
        "source": source,
        "season": season_label(start),
        "teams": teams_cur,
        "matches": prev + cur,
        "fixtures": fixtures[:40],
        "last_result": _last_date(cur) if cur else None,
        "played": len(cur),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def fetch_all(existing: dict | None = None, progress=None) -> tuple[dict, list[str]]:
    """Aggiorna tutti i campionati. Restituisce (dati, elenco_errori).
    I campionati che non si riescono a scaricare mantengono i dati precedenti."""
    global _fd_fixtures_cache
    _fd_fixtures_cache = None
    existing = existing or {}
    leagues_old = existing.get("leagues", {})
    leagues_new, errors = {}, []
    names = list(LEAGUES)
    for i, name in enumerate(names):
        if progress:
            progress(i, len(names), name)
        try:
            data = fetch_league(name)
        except Exception as e:
            print(f"[{name}] errore: {e}")
            data = None
        if data:
            leagues_new[name] = data
        elif name in leagues_old:
            leagues_new[name] = leagues_old[name]
            errors.append(name)
        else:
            errors.append(name)
    if progress:
        progress(len(names), len(names), "")
    ok = len(leagues_new) > 0
    return ({"version": 2, "updated_at": datetime.now().isoformat(timespec="seconds") if ok else existing.get("updated_at"),
             "leagues": leagues_new}, errors)
