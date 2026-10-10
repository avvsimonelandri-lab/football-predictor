"""Costruisce la versione per telefono (pagina web statica) con dati e pronostici aggiornati.

Viene eseguito da GitHub Actions ogni 4 ore (.github/workflows/site.yml):
  1. scarica i dati come fa il programma (stesso codice);
  2. aggiorna lo storico pronostici in state/storico.json (salvato nel repository);
  3. scrive site/index.html, una pagina unica che funziona anche offline una volta aperta.

Si può provare anche in locale:  python web/build_site.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import history  # noqa: E402
import updater  # noqa: E402
from predictor import FootballPredictor, best_value, standings  # noqa: E402

STATE = os.path.join(ROOT, "state")
SITE = os.path.join(ROOT, "site")
DAYS = 10


def r3(x):
    return None if x is None else round(float(x), 3)


def build_payload(data: dict, h: dict) -> dict:
    blend_w = (data.get("blend") or {}).get("w")
    xg_beta = (data.get("xg") or {}).get("beta")
    today = date.today().isoformat()
    limit = (date.today() + timedelta(days=DAYS)).isoformat()
    out = {"updated": data.get("updated_at"), "generated": datetime.now().isoformat(timespec="minutes"),
           "blend_w": blend_w, "leagues": []}
    for name, ld in data.get("leagues", {}).items():
        m = FootballPredictor(ld, blend_w=blend_w, xg_beta=xg_beta)
        matches = []
        for f in [f for f in ld.get("fixtures", []) if today <= f["date"] <= limit]:
            r = m.predict_detailed(f["home"], f["away"], f.get("odds"))
            if not r:
                continue
            p = m.best_1x2(r)
            bv = best_value(r)
            pt = r.get("PrimoTempo") or {}
            st = r.get("Stats") or {}
            mk = r.get("market") or {}
            item = {
                "d": f["date"], "t": f.get("time", ""), "h": f["home"], "a": f["away"],
                "p": {k: r3(v) for k, v in p.items()},
                "pm": {k: r3(v) for k, v in r["1X2"].items()},
                "comb": bool(r.get("blend")),
                "xg": [r3(r["xg"][0]), r3(r["xg"][1])],
                "dc": {k: r3(v) for k, v in r["DoppiaChance"].items()},
                "uo": {k: r3(v["Over"]) for k, v in r["UnderOver"].items()},
                "o25": r3(((r.get("blend") or {}).get("UnderOver25") or r["UnderOver25"])["Over"]),
                "gg": r3(r["GoalNoGoal"]["Goal"]),
                "sc": [[f"{i}-{j}", r3(q)] for (i, j), q in r["exact_scores"][:6]],
                "mg": {k: r3(v) for k, v in r["Multigol"].items()},
                "pt": {k: r3(v) for k, v in (pt.get("1X2") or {}).items()},
                "ptft": sorted([[k, r3(v)] for k, v in (pt.get("PTFT") or {}).items()], key=lambda x: -x[1])[:4],
                "o05pt": r3((pt.get("UO_PT") or {}).get(0.5, {}).get("Over")),
                "odds": f.get("odds"),
                "mk": [{"m": x["market"], "q": x["odds"], "b": r3(x["p_market"]), "c": r3(x["p_blend"]),
                        "v": x["value"]} for x in mk.get("rows", [])],
                "val": {"m": bv["market"], "q": bv["odds"], "ev": r3(bv["ev"])} if bv else None,
            }
            for key, nm in (("ang", "Angoli"), ("car", "Cartellini"), ("tp", "TiriPorta")):
                s = st.get(nm)
                if s:
                    item[key] = {"e": [round(s["exp"][0], 1), round(s["exp"][1], 1)],
                                 "tot": {str(l): r3(v["Over"]) for l, v in s["tot"].items()}}
            matches.append(item)
        tab = [{"r": x["rank"], "t": x["team"], "pg": x["pg"], "dr": x["gf"] - x["gs"], "pt": x["pts"], "f": x["form"]}
               for x in standings(ld)]
        hs = history.summary(h, name)
        rec = [{"d": e["date"], "h": e["home"], "a": e["away"], "k": max(e["p"], key=e["p"].get),
                "p": r3(max(e["p"].values())), "sc": e["score"],
                "res": f"{e['result']['hg']}-{e['result']['ag']}" if e.get("result") else None,
                "ok": (None if not e.get("result") else
                       max(e["p"], key=e["p"].get) == ("1" if e["result"]["hg"] > e["result"]["ag"] else
                                                       "X" if e["result"]["hg"] == e["result"]["ag"] else "2")),
                "val": e.get("value")} for e in history.recent(h, name, 40)]
        out["leagues"].append({"name": name, "season": ld.get("season"), "source": ld.get("source"),
                               "last": ld.get("last_result"), "played": ld.get("played"),
                               "matches": matches, "table": tab, "hist": hs, "recent": rec,
                               "rel": (data.get("reliability") or {}).get(name)})
    out["hist_all"] = history.summary(h)
    return out


def main():
    os.makedirs(STATE, exist_ok=True)
    os.makedirs(SITE, exist_ok=True)
    updater.data_dir = lambda: STATE   # dati e storico nella cartella state/ del repository
    data, errors = updater.run_update()
    if not data.get("leagues"):
        print("Nessun dato: pagina non aggiornata")
        sys.exit(1)
    h = history.load(STATE)
    payload = build_payload(data, h)
    tpl = open(os.path.join(ROOT, "web", "template.html"), encoding="utf-8").read()
    html = tpl.replace("/*__DATA__*/null", json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(SITE, ".nojekyll"), "w") as f:
        f.write("")
    # icona e manifest per "Aggiungi alla schermata Home" (iPhone / Android)
    import shutil
    static = os.path.join(ROOT, "web", "static")
    for name in os.listdir(static):
        shutil.copy(os.path.join(static, name), os.path.join(SITE, name))
    # resoconto (senza chiavi) salvato nel repository: serve a verificare fonti e aggiornamenti
    stato = {"aggiornato": data.get("updated_at"), "problemi": errors,
             "chiave_football_data_org": bool(os.environ.get("FDORG_KEY")),
             "peso_modello": (data.get("blend") or {}).get("w"), "peso_xg": (data.get("xg") or {}).get("beta"),
             "campionati": {n: {"fonte": ld.get("source"), "giocate": ld.get("played"), "ultimo": ld.get("last_result"),
                                "da_football_data_org": sum(1 for m in ld.get("matches", []) if m.get("src") == "fdorg"),
                                "prossime": len(ld.get("fixtures", []))}
                            for n, ld in data.get("leagues", {}).items()}}
    with open(os.path.join(STATE, "stato.json"), "w", encoding="utf-8") as f:
        json.dump(stato, f, ensure_ascii=False, indent=1)
    print(f"Pagina creata: {sum(len(l['matches']) for l in payload['leagues'])} partite, problemi: {errors}")


if __name__ == "__main__":
    main()
