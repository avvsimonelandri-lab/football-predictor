"""Modello di previsione — Dixon-Coles con pesatura temporale.

  * fattore campo stimato dai dati;
  * attacco e difesa stimati partita per partita, con più peso alle partite recenti;
  * usa anche la stagione precedente (stime sensate a inizio campionato);
  * neopromosse con stima iniziale prudente;
  * correzione Dixon-Coles per i risultati con pochi gol;
  * primo tempo / secondo tempo (quota di gol per tempo stimata per campionato);
  * angoli, cartellini e tiri in porta con modelli dedicati (dove la fonte fornisce i dati);
  * stima combinata modello + quote dei bookmaker, con peso calibrato automaticamente.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import nbinom, poisson

MAX_GOALS = 10

# Parametri calibrati con un backtest su 1.722 partite 2025/26 (Serie A, Premier, LaLiga, Bundesliga, Ligue 1)
DEFAULT_XI = 0.0025        # decadimento giornaliero del peso (emivita ~280 giorni)
DEFAULT_ALPHA = 5.0        # forza della regolarizzazione verso la media
PREV_SEASON_FACTOR = 0.85  # sconto ulteriore sulla stagione precedente (mercato estivo)
PROMOTED_PRIOR = (-0.20, 0.18)  # (attacco, difesa) iniziali per le neopromosse
DEFAULT_XG_BETA = 0.0      # peso degli xG stimati dai tiri (0 = solo gol); calibrato a ogni aggiornamento
XG_BETAS = (0.0, 0.3, 0.5, 0.7)
DEFAULT_BLEND = 0.35       # peso del modello nella stima combinata, finché non c'è abbastanza storico per calibrarlo
VALUE_EV = 0.03            # vantaggio atteso minimo (sulla stima combinata) per segnalare "valore"
VALUE_MIN_P = 0.25         # probabilità minima per segnalare "valore" (evita quote altissime)

# statistiche: nome -> (colonna casa, colonna ospite) dentro m["st"]
STATS = {
    "Angoli": (("hc",), ("ac",)),
    "Cartellini": (("hy", "hr"), ("ay", "ar")),
    "TiriPorta": (("hst",), ("ast",)),
}
STAT_LINES = {
    "Angoli": {"tot": (7.5, 8.5, 9.5, 10.5, 11.5, 12.5), "team": (2.5, 3.5, 4.5, 5.5, 6.5)},
    "Cartellini": {"tot": (2.5, 3.5, 4.5, 5.5, 6.5), "team": (0.5, 1.5, 2.5, 3.5)},
    "TiriPorta": {"tot": (6.5, 7.5, 8.5, 9.5, 10.5), "team": (2.5, 3.5, 4.5, 5.5)},
}


def _days(d: str, ref: date) -> int:
    return (ref - date.fromisoformat(d)).days


def _poisson_ratings(h, a, yh, ya, w, n, alpha, pa=None, pd=None):
    """Regressione di Poisson log-lineare con fattore campo e regolarizzazione:
    log E[casa] = mu + campo + att[casa] + dif[ospite];  log E[ospite] = mu + att[ospite] + dif[casa]."""
    pa = np.zeros(n) if pa is None else pa
    pd = np.zeros(n) if pd is None else pd

    def f(x):
        mu, ha = x[0], x[1]
        att, dfn = x[2:2 + n], x[2 + n:]
        lh = mu + ha + att[h] + dfn[a]
        la = mu + att[a] + dfn[h]
        eh, ea = np.exp(lh), np.exp(la)
        nll = np.sum(w * (eh - yh * lh)) + np.sum(w * (ea - ya * la))
        nll += alpha * (np.sum((att - pa) ** 2) + np.sum((dfn - pd) ** 2))
        rh, ra = w * (eh - yh), w * (ea - ya)
        g = np.zeros_like(x)
        g[0] = rh.sum() + ra.sum()
        g[1] = rh.sum()
        ga, gd = np.zeros(n), np.zeros(n)
        np.add.at(ga, h, rh); np.add.at(ga, a, ra)
        np.add.at(gd, a, rh); np.add.at(gd, h, ra)
        g[2:2 + n] = ga + 2 * alpha * (att - pa)
        g[2 + n:] = gd + 2 * alpha * (dfn - pd)
        return nll, g

    x0 = np.zeros(2 + 2 * n)
    x0[0] = math.log(max(0.3, (np.average(yh, weights=w) + np.average(ya, weights=w)) / 2))
    x0[1] = 0.1
    x0[2:2 + n], x0[2 + n:] = pa, pd
    x = minimize(f, x0, jac=True, method="L-BFGS-B").x
    return float(x[0]), float(x[1]), x[2:2 + n], x[2 + n:]


def count_pmf(mean: float, phi: float, kmax: int) -> np.ndarray:
    """Distribuzione di un conteggio: Poisson, oppure binomiale negativa se c'è sovradispersione (phi > 1)."""
    k = np.arange(kmax + 1)
    if phi <= 1.05 or mean <= 0:
        p = poisson.pmf(k, max(mean, 1e-6))
    else:
        r = mean / (phi - 1)
        p = nbinom.pmf(k, r, r / (r + mean))
    return p / p.sum()


class FootballPredictor:
    def __init__(self, league_data: dict, ref_date: date | None = None, xi: float = DEFAULT_XI,
                 alpha: float = DEFAULT_ALPHA, matches: list | None = None, blend_w: float | None = None,
                 extras: bool = True, xg_beta: float | None = None):
        self.league = league_data
        self.ref = ref_date or date.today()
        self.xi, self.alpha = xi, alpha
        self.blend_w = DEFAULT_BLEND if blend_w is None else blend_w
        self.xg_beta = DEFAULT_XG_BETA if xg_beta is None else xg_beta
        self.xg_coef = None
        ms = matches if matches is not None else league_data.get("matches", [])
        self.matches = [m for m in ms if m["date"] <= self.ref.isoformat()]
        self.current_teams = list(league_data.get("teams") or [])
        self.stat_models = {}
        self.ht_share = (0.44, 0.44)
        self._fit()
        if extras:
            self._fit_halftime()
            self._fit_stats()

    def _weights(self, ms):
        return np.array([math.exp(-self.xi * max(0, _days(m["date"], self.ref))) *
                         (PREV_SEASON_FACTOR if m.get("season") == "prev" else 1.0) for m in ms])

    # ------------------------------------------------------------------ gol
    def _fit(self):
        ms = self.matches
        teams = sorted({m["home"] for m in ms} | {m["away"] for m in ms} | set(self.current_teams))
        self.teams = teams
        idx = {t: i for i, t in enumerate(teams)}
        self.idx = idx
        n = len(teams)
        self.promoted = []
        if n < 2 or not ms:
            self.mu, self.home_adv, self.rho = math.log(1.35), 0.25, -0.05
            self.att, self.dfn = np.zeros(n), np.zeros(n)
            self.n_matches = 0
            return

        h = np.array([idx[m["home"]] for m in ms])
        a = np.array([idx[m["away"]] for m in ms])
        yh = np.array([m["hg"] for m in ms], float)
        ya = np.array([m["ag"] for m in ms], float)
        w = self._weights(ms)
        self.n_matches = len(ms)

        prev_teams = {m["home"] for m in ms if m.get("season") == "prev"} | \
                     {m["away"] for m in ms if m.get("season") == "prev"}
        pa, pd = np.zeros(n), np.zeros(n)
        if prev_teams:
            for t in teams:
                if t not in prev_teams:
                    pa[idx[t]], pd[idx[t]] = PROMOTED_PRIOR
        self.promoted = [t for t in self.current_teams if prev_teams and t not in prev_teams]

        # obiettivo della regressione: gol, oppure un misto gol / xG stimati dai tiri
        th, ta = yh, ya
        if self.xg_beta > 0:
            xg = self._xg_proxy(ms)
            if xg is not None:
                xh, xa, ok = xg
                b = self.xg_beta
                th = np.where(ok, (1 - b) * yh + b * xh, yh)
                ta = np.where(ok, (1 - b) * ya + b * xa, ya)
        self.mu, self.home_adv, self.att, self.dfn = _poisson_ratings(h, a, th, ta, w, n, self.alpha, pa, pd)

        lh = np.exp(self.mu + self.home_adv + self.att[h] + self.dfn[a])
        la = np.exp(self.mu + self.att[a] + self.dfn[h])

        def nll_rho(rho):
            tau = np.ones_like(lh)
            m00 = (yh == 0) & (ya == 0); m01 = (yh == 0) & (ya == 1)
            m10 = (yh == 1) & (ya == 0); m11 = (yh == 1) & (ya == 1)
            tau[m00] = 1 - lh[m00] * la[m00] * rho
            tau[m01] = 1 + lh[m01] * rho
            tau[m10] = 1 + la[m10] * rho
            tau[m11] = 1 - rho
            if np.any(tau <= 0):
                return 1e9
            return -np.sum(w * np.log(tau))

        self.rho = float(minimize_scalar(nll_rho, bounds=(-0.25, 0.15), method="bounded").x)

    # ------------------------------------------------------------------ xG stimati dai tiri
    def _xg_proxy(self, ms):
        """xG approssimati: gol attesi = a·(tiri in porta) + b·(tiri fuori), coefficienti stimati
        sul campionato stesso. Misurano la qualità delle occasioni e risentono meno della fortuna."""
        keys = ("hs", "as", "hst", "ast")
        ok = np.array([bool(m.get("st")) and all(k in m["st"] for k in keys) for m in ms])
        if ok.sum() < 60:
            return None
        sel = [m for m, o in zip(ms, ok) if o]
        sot = np.array([m["st"]["hst"] for m in sel] + [m["st"]["ast"] for m in sel], float)
        off = np.array([max(0, m["st"]["hs"] - m["st"]["hst"]) for m in sel] +
                       [max(0, m["st"]["as"] - m["st"]["ast"]) for m in sel], float)
        g = np.array([m["hg"] for m in sel] + [m["ag"] for m in sel], float)
        X = np.column_stack([sot, off])
        coef, *_ = np.linalg.lstsq(X, g, rcond=None)
        coef = np.clip(coef, [0.15, 0.0], [0.6, 0.15])
        self.xg_coef = (float(coef[0]), float(coef[1]))

        def xg(m, side):
            if not (m.get("st") and all(k in m["st"] for k in keys)):
                return 0.0
            s_, t_ = (m["st"]["hs"], m["st"]["hst"]) if side == "h" else (m["st"]["as"], m["st"]["ast"])
            return coef[0] * t_ + coef[1] * max(0, s_ - t_)

        return np.array([xg(m, "h") for m in ms]), np.array([xg(m, "a") for m in ms]), ok

    # ------------------------------------------------------------------ primo tempo
    def _fit_halftime(self):
        ms = [m for m in self.matches if m.get("hthg") is not None]
        if len(ms) < 40:
            return
        w = self._weights(ms)
        gh = np.array([m["hg"] for m in ms]); ga = np.array([m["ag"] for m in ms])
        hh = np.array([m["hthg"] for m in ms]); ha = np.array([m["htag"] for m in ms])
        sh = float(np.sum(w * hh) / max(1e-9, np.sum(w * gh)))
        sa = float(np.sum(w * ha) / max(1e-9, np.sum(w * ga)))
        self.ht_share = (min(0.6, max(0.3, sh)), min(0.6, max(0.3, sa)))
        self.n_ht = len(ms)

    # ------------------------------------------------------------------ angoli / cartellini / tiri
    def _fit_stats(self):
        for name, (kh, ka) in STATS.items():
            ms = [m for m in self.matches if m.get("st") and all(k in m["st"] for k in kh + ka)]
            if len(ms) < 60:
                continue
            teams = sorted({m["home"] for m in ms} | {m["away"] for m in ms})
            idx = {t: i for i, t in enumerate(teams)}
            n = len(teams)
            h = np.array([idx[m["home"]] for m in ms]); a = np.array([idx[m["away"]] for m in ms])
            yh = np.array([sum(m["st"][k] for k in kh) for m in ms], float)
            ya = np.array([sum(m["st"][k] for k in ka) for m in ms], float)
            w = self._weights(ms)
            mu, ha, att, dfn = _poisson_ratings(h, a, yh, ya, w, n, self.alpha)
            lh = np.exp(mu + ha + att[h] + dfn[a]); la = np.exp(mu + att[a] + dfn[h])
            phi = float((np.sum(w * (yh - lh) ** 2 / lh) + np.sum(w * (ya - la) ** 2 / la)) / (2 * np.sum(w)))
            self.stat_models[name] = {"idx": idx, "mu": mu, "ha": ha, "att": att, "dfn": dfn,
                                      "phi": max(1.0, phi), "n": len(ms)}

    def stat_expected(self, name, home, away):
        sm = self.stat_models.get(name)
        if not sm or home not in sm["idx"] or away not in sm["idx"]:
            return None
        i, j = sm["idx"][home], sm["idx"][away]
        return (math.exp(sm["mu"] + sm["ha"] + sm["att"][i] + sm["dfn"][j]),
                math.exp(sm["mu"] + sm["att"][j] + sm["dfn"][i]))

    def stat_markets(self, name, home, away):
        ex = self.stat_expected(name, home, away)
        if not ex:
            return None
        mh, ma = ex
        phi = self.stat_models[name]["phi"]
        K = 40
        ph, pa_ = count_pmf(mh, phi, K), count_pmf(ma, phi, K)
        pt = count_pmf(mh + ma, phi, 2 * K)
        lines = STAT_LINES[name]
        k = np.arange(K + 1); kt = np.arange(2 * K + 1)
        J = np.outer(ph, pa_)
        H, A = np.indices(J.shape)
        return {
            "exp": (mh, ma),
            "tot": {l: {"Under": float(pt[kt < l].sum()), "Over": float(pt[kt > l].sum())} for l in lines["tot"]},
            "home": {l: {"Under": float(ph[k < l].sum()), "Over": float(ph[k > l].sum())} for l in lines["team"]},
            "away": {l: {"Under": float(pa_[k < l].sum()), "Over": float(pa_[k > l].sum())} for l in lines["team"]},
            "1X2": {"1": float(J[H > A].sum()), "X": float(J[H == A].sum()), "2": float(J[H < A].sum())},
            "n": self.stat_models[name]["n"],
        }

    # ------------------------------------------------------------------ previsioni
    def team_ratings(self) -> list[dict]:
        out = []
        for t in self.current_teams or self.teams:
            if t not in self.idx:
                continue
            i = self.idx[t]
            out.append({"team": t, "attack": float(math.exp(self.att[i])), "defense": float(math.exp(self.dfn[i]))})
        return out

    def expected_goals(self, home: str, away: str) -> tuple[float, float] | None:
        if home not in self.idx or away not in self.idx:
            return None
        i, j = self.idx[home], self.idx[away]
        lh = math.exp(self.mu + self.home_adv + self.att[i] + self.dfn[j])
        la = math.exp(self.mu + self.att[j] + self.dfn[i])
        return lh, la

    def score_matrix(self, home: str, away: str) -> np.ndarray | None:
        xg = self.expected_goals(home, away)
        if not xg:
            return None
        lh, la = xg
        g = np.arange(MAX_GOALS + 1)
        M = np.outer(poisson.pmf(g, lh), poisson.pmf(g, la))
        r = self.rho
        M[0, 0] *= max(1e-6, 1 - lh * la * r)
        M[0, 1] *= max(1e-6, 1 + lh * r)
        M[1, 0] *= max(1e-6, 1 + la * r)
        M[1, 1] *= max(1e-6, 1 - r)
        return M / M.sum()

    def halftime(self, lh: float, la: float) -> dict:
        sh, sa = self.ht_share
        K = 8
        g = np.arange(K + 1)
        h1, a1 = poisson.pmf(g, lh * sh), poisson.pmf(g, la * sa)
        h2, a2 = poisson.pmf(g, lh * (1 - sh)), poisson.pmf(g, la * (1 - sa))

        def diff_dist(ph, pa_):
            M = np.outer(ph, pa_)
            D = np.zeros(2 * K + 1)
            for i in range(K + 1):
                for j in range(K + 1):
                    D[i - j + K] += M[i, j]
            return D

        D1, D2 = diff_dist(h1, a1), diff_dist(h2, a2)
        d = np.arange(-K, K + 1)
        sign = lambda x: "1" if x > 0 else "X" if x == 0 else "2"
        ptft = {f"{r1}/{r2}": 0.0 for r1 in "1X2" for r2 in "1X2"}
        for i, d1 in enumerate(d):
            if D1[i] < 1e-12:
                continue
            for j, d2 in enumerate(d):
                ptft[f"{sign(d1)}/{sign(d1 + d2)}"] += D1[i] * D2[j]
        s = sum(ptft.values())
        ptft = {k: v / s for k, v in ptft.items()}
        m1, m2 = lh * sh + la * sa, lh * (1 - sh) + la * (1 - sa)
        g2 = np.arange(21)
        t1, t2 = poisson.pmf(g2, m1), poisson.pmf(g2, m2)
        T = np.outer(t1, t2)
        I, J = np.indices(T.shape)
        uo = lambda m, l: {"Under": float(poisson.cdf(math.floor(l), m)), "Over": float(1 - poisson.cdf(math.floor(l), m))}
        return {
            "1X2": {"1": float(D1[d > 0].sum()), "X": float(D1[d == 0].sum()), "2": float(D1[d < 0].sum())},
            "1X2_ST": {"1": float(D2[d > 0].sum()), "X": float(D2[d == 0].sum()), "2": float(D2[d < 0].sum())},
            "PTFT": ptft,
            "UO_PT": {l: uo(m1, l) for l in (0.5, 1.5, 2.5)},
            "UO_ST": {l: uo(m2, l) for l in (0.5, 1.5, 2.5)},
            "TempoPiuGol": {"1° tempo": float(T[I > J].sum()), "Uguale": float(T[I == J].sum()),
                            "2° tempo": float(T[I < J].sum())},
            "Gol1T": float(1 - t1[0]), "Gol2T": float(1 - t2[0]),
            "xg_pt": (lh * sh, la * sa),
        }

    def predict_detailed(self, home: str, away: str, odds: dict | None = None, extras: bool = True) -> dict | None:
        M = self.score_matrix(home, away)
        if M is None:
            return None
        lh, la = self.expected_goals(home, away)
        H, A = np.indices(M.shape)
        T = H + A
        p = lambda mask: float(M[mask].sum())

        p1, px, p2 = p(H > A), p(H == A), p(H < A)
        flat = sorted(((int(i), int(j), float(M[i, j])) for i in range(M.shape[0]) for j in range(M.shape[1])),
                      key=lambda x: -x[2])
        res = {
            "home": home, "away": away,
            "xg": (lh, la),
            "exact_scores": [((i, j), q) for i, j, q in flat[:10]],
            "1X2": {"1": p1, "X": px, "2": p2},
            "DoppiaChance": {"1X": p1 + px, "X2": px + p2, "12": p1 + p2},
            "UnderOver": {f"{l}": {"Under": p(T < l), "Over": p(T > l)} for l in (0.5, 1.5, 2.5, 3.5, 4.5)},
            "UnderOver25": {"Under": p(T < 2.5), "Over": p(T > 2.5)},
            "GoalNoGoal": {"Goal": p((H > 0) & (A > 0)), "NoGoal": p((H == 0) | (A == 0))},
            "SommaGol": {**{str(k): p(T == k) for k in range(6)}, "6+": p(T >= 6)},
            "Multigol": {k: p((T >= a_) & (T <= b_)) for k, (a_, b_) in
                         {"1-2": (1, 2), "1-3": (1, 3), "2-3": (2, 3), "2-4": (2, 4), "2-5": (2, 5), "3-6": (3, 6)}.items()},
            "MultigolHome": {k: p((H >= a_) & (H <= b_)) for k, (a_, b_) in
                             {"0": (0, 0), "1-2": (1, 2), "1-3": (1, 3), "2-4": (2, 4), "2-5": (2, 5)}.items()},
            "MultigolAway": {k: p((A >= a_) & (A <= b_)) for k, (a_, b_) in
                             {"0": (0, 0), "1-2": (1, 2), "1-3": (1, 3), "2-4": (2, 4), "2-5": (2, 5)}.items()},
            "Combo": {
                "1 + Over 1.5": p((H > A) & (T > 1.5)),
                "1X + Under 3.5": p((H >= A) & (T < 3.5)),
                "X2 + Under 3.5": p((H <= A) & (T < 3.5)),
                "2 + Over 1.5": p((H < A) & (T > 1.5)),
                "Goal + Over 2.5": p((H > 0) & (A > 0) & (T > 2.5)),
            },
            "market": None,
            "blend": None,
        }
        if extras:
            res["PrimoTempo"] = self.halftime(lh, la)
            res["Stats"] = {name: self.stat_markets(name, home, away) for name in STATS}
        if odds:
            res["market"] = market_analysis(res, odds, self.blend_w)
            res["blend"] = res["market"].get("blend")
        return res

    def best_1x2(self, res: dict) -> dict:
        """Probabilità 1X2 da mostrare: combinata se ci sono le quote, altrimenti del modello."""
        return (res.get("blend") or {}).get("1X2") or res["1X2"]

    def predict_score(self, home, away):
        r = self.predict_detailed(home, away, extras=False)
        return r["exact_scores"] if r else "Squadra non trovata"


# ---------------------------------------------------------------------- quote
def implied(odds_list):
    inv = [1 / o for o in odds_list]
    s = sum(inv)
    return [x / s for x in inv], s - 1  # probabilità senza margine, margine del bookmaker


def market_analysis(res: dict, odds: dict, w: float = DEFAULT_BLEND) -> dict:
    out = {"rows": [], "w": w, "blend": {}}
    if odds.get("H") and odds.get("D") and odds.get("A"):
        probs, margin = implied([odds["H"], odds["D"], odds["A"]])
        out["margin"] = margin
        b = {}
        for k, q, mp in zip(("1", "X", "2"), (odds["H"], odds["D"], odds["A"]), probs):
            pm = res["1X2"][k]
            pb = w * pm + (1 - w) * mp
            b[k] = pb
            out["rows"].append({"market": k, "odds": q, "p_market": mp, "p_model": pm, "p_blend": pb, "ev": pb * q - 1})
        out["blend"]["1X2"] = b
    if odds.get("O25") and odds.get("U25"):
        probs, _ = implied([odds["O25"], odds["U25"]])
        b = {}
        for k, q, mp in zip(("Over 2.5", "Under 2.5"), (odds["O25"], odds["U25"]), probs):
            key = "Over" if k.startswith("Over") else "Under"
            pm = res["UnderOver25"][key]
            pb = w * pm + (1 - w) * mp
            b[key] = pb
            out["rows"].append({"market": k, "odds": q, "p_market": mp, "p_model": pm, "p_blend": pb, "ev": pb * q - 1})
        out["blend"]["UnderOver25"] = b
    for r in out["rows"]:
        r["value"] = r["ev"] >= VALUE_EV and r["p_blend"] >= VALUE_MIN_P
    return out


def best_value(res: dict) -> dict | None:
    mk = res.get("market")
    if not mk:
        return None
    c = [r for r in mk["rows"] if r["value"]]
    return max(c, key=lambda r: r["ev"]) if c else None


# ---------------------------------------------------------------------- classifica
def standings(league_data: dict) -> list[dict]:
    cur = [m for m in league_data.get("matches", []) if m.get("season") == "cur"]
    teams = league_data.get("teams") or sorted({m["home"] for m in cur} | {m["away"] for m in cur})
    tab = {t: {"team": t, "pg": 0, "v": 0, "n": 0, "p": 0, "gf": 0, "gs": 0, "pts": 0, "form": [],
               "home_pts": 0, "away_pts": 0} for t in teams}
    for m in sorted(cur, key=lambda m: m["date"]):
        for t, gf, gs, side in ((m["home"], m["hg"], m["ag"], "home"), (m["away"], m["ag"], m["hg"], "away")):
            if t not in tab:
                continue
            r = tab[t]
            r["pg"] += 1; r["gf"] += gf; r["gs"] += gs
            if gf > gs:
                r["v"] += 1; r["pts"] += 3; r["form"].append("V"); r[f"{side}_pts"] += 3
            elif gf == gs:
                r["n"] += 1; r["pts"] += 1; r["form"].append("N"); r[f"{side}_pts"] += 1
            else:
                r["p"] += 1; r["form"].append("P")
    rows = list(tab.values())
    rows.sort(key=lambda r: (-r["pts"], -(r["gf"] - r["gs"]), -r["gf"], r["team"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        r["form"] = "".join(r["form"][-5:])
    return rows


# ---------------------------------------------------------------------- affidabilità
def backtest(league_data: dict, days: int = 70, step: int = 7, ref: date | None = None,
             xi: float = DEFAULT_XI, alpha: float = DEFAULT_ALPHA, matches: list | None = None,
             return_recs: bool = False, blend_w: float | None = None, xg_beta: float | None = None):
    """Simula il modello sul passato: ogni settimana lo ri-stima usando solo i dati
    disponibili fino a quel giorno e lo confronta con i risultati della settimana dopo."""
    ms = sorted(matches if matches is not None else league_data.get("matches", []), key=lambda m: m["date"])
    if not ms:
        return ({"n": 0}, []) if return_recs else {"n": 0}
    ref = ref or date.fromisoformat(ms[-1]["date"]) + timedelta(days=1)
    start = ref - timedelta(days=days)
    recs = []
    cut = start
    while cut < ref:
        nxt = cut + timedelta(days=step)
        test = [m for m in ms if cut.isoformat() <= m["date"] < nxt.isoformat()]
        if test:
            train = [m for m in ms if m["date"] < cut.isoformat()]
            if len(train) >= 30:
                ld = {"teams": sorted({m["home"] for m in test} | {m["away"] for m in test})}
                model = FootballPredictor(ld, ref_date=cut, xi=xi, alpha=alpha, matches=train, extras=False,
                                          xg_beta=xg_beta)
                for m in test:
                    r = model.predict_detailed(m["home"], m["away"], extras=False)
                    if not r:
                        continue
                    out = "1" if m["hg"] > m["ag"] else "X" if m["hg"] == m["ag"] else "2"
                    rec = {"p": r["1X2"], "out": out, "over": (m["hg"] + m["ag"]) > 2.5,
                           "p_over": r["UnderOver25"]["Over"], "gg": m["hg"] > 0 and m["ag"] > 0,
                           "p_gg": r["GoalNoGoal"]["Goal"], "pm": None, "pmo": None}
                    o = m.get("odds")
                    if o and o.get("H") and o.get("D") and o.get("A"):
                        pr, _ = implied([o["H"], o["D"], o["A"]])
                        rec["pm"] = dict(zip(("1", "X", "2"), pr))
                    if o and o.get("O25") and o.get("U25"):
                        rec["pmo"] = implied([o["O25"], o["U25"]])[0][0]
                    recs.append(rec)
        cut = nxt
    summ = summarize_backtest(recs, blend_w)
    return (summ, recs) if return_recs else summ


def has_shots(league_data: dict, min_share: float = 0.6) -> bool:
    cur = [m for m in league_data.get("matches", []) if m.get("season") == "cur"]
    allm = league_data.get("matches", [])
    ok = [m for m in allm if m.get("st") and "hst" in m["st"] and "hs" in m["st"]]
    return len(allm) > 0 and len(ok) / len(allm) >= min_share and len(ok) >= 60 and bool(cur)


def _rps(p, out):
    o = {"1": (1, 0, 0), "X": (0, 1, 0), "2": (0, 0, 1)}[out]
    c1, c2 = p["1"], p["1"] + p["X"]
    return 0.5 * ((c1 - o[0]) ** 2 + (c2 - o[0] - o[1]) ** 2)


def _mix(p, pm, w):
    return {k: w * p[k] + (1 - w) * pm[k] for k in p}


def optimal_blend(recs: list, min_n: int = 150) -> dict | None:
    """Sceglie il peso del modello (0 = solo quote, 1 = solo modello) che avrebbe sbagliato meno
    sulle partite recenti di tutti i campionati con quote disponibili."""
    mk = [r for r in recs if r.get("pm")]
    if len(mk) < min_n:
        return None
    grid = [i / 20 for i in range(21)]
    scores = {w: sum(_rps(_mix(r["p"], r["pm"], w), r["out"]) for r in mk) / len(mk) for w in grid}
    best = min(scores, key=scores.get)
    # prudenza: non oltre 0.7 di peso al modello, il mercato è di norma molto informato
    best = min(best, 0.7)
    return {"w": best, "n": len(mk), "rps_model": scores[1.0], "rps_market": scores[0.0], "rps_blend": scores[best]}


def summarize_backtest(recs: list, blend_w: float | None = None) -> dict:
    n = len(recs)
    if not n:
        return {"n": 0}
    acc = sum(max(r["p"], key=r["p"].get) == r["out"] for r in recs) / n
    ll = -sum(math.log(max(1e-9, r["p"][r["out"]])) for r in recs) / n
    rps = sum(_rps(r["p"], r["out"]) for r in recs) / n
    over_acc = sum((r["p_over"] > 0.5) == r["over"] for r in recs) / n
    gg_acc = sum((r["p_gg"] > 0.5) == r["gg"] for r in recs) / n
    conf = [r for r in recs if max(r["p"].values()) >= 0.55]
    conf_acc = (sum(max(r["p"], key=r["p"].get) == r["out"] for r in conf) / len(conf)) if conf else None
    out = {"n": n, "acc_1x2": acc, "logloss": ll, "rps": rps, "acc_over25": over_acc, "acc_gg": gg_acc,
           "n_conf": len(conf), "acc_conf": conf_acc}
    mk = [r for r in recs if r["pm"]]
    if mk:
        out["market"] = {
            "n": len(mk),
            "acc_1x2": sum(max(r["pm"], key=r["pm"].get) == r["out"] for r in mk) / len(mk),
            "rps": sum(_rps(r["pm"], r["out"]) for r in mk) / len(mk),
            "rps_model_same": sum(_rps(r["p"], r["out"]) for r in mk) / len(mk),
        }
        if blend_w is not None:
            bl = [_mix(r["p"], r["pm"], blend_w) for r in mk]
            out["market"]["w"] = blend_w
            out["market"]["rps_blend"] = sum(_rps(b, r["out"]) for b, r in zip(bl, mk)) / len(mk)
            out["market"]["acc_blend"] = sum(max(b, key=b.get) == r["out"] for b, r in zip(bl, mk)) / len(mk)
    return out
