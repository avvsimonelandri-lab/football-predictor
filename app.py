"""Football Predictor Pro 2.0 — interfaccia."""
from __future__ import annotations

import os
import queue
import sys
import threading
from datetime import date, datetime

# modalità senza finestra per l'Utilità di pianificazione: FootballPredictorPro.exe --update
if "--update" in sys.argv:
    import updater
    updater.run_update()
    sys.exit(0)

import customtkinter as ctk

import updater
from leagues import DEFAULT_LEAGUE, LEAGUES
import app_update
import exporter
import history
import notifier
import settings
from predictor import DEFAULT_BLEND, FootballPredictor, best_value, standings

try:
    from version import VERSION as APP_VERSION
except Exception:
    APP_VERSION = "2.2"

# colori (tema chiaro, tema scuro): customtkinter sceglie quello giusto in base al tema attivo
C = {
    "bg": ("#f3f4f7", "#0f1115"), "panel": ("#ffffff", "#171a21"), "panel2": ("#eef0f4", "#1e222b"),
    "line": ("#d9dce3", "#2a2f3a"), "text": ("#14171f", "#e7e9ee"), "muted": ("#5d6475", "#8a90a0"),
    "accent": ("#2E7D32", "#2E7D32"), "accent_h": ("#1B5E20", "#1B5E20"),
    "red": ("#D32F2F", "#D32F2F"), "red_h": ("#B71C1C", "#B71C1C"),
    "gold": ("#9a6700", "#E0B03A"), "blue": ("#2f6fe0", "#4F8EF7"),
}
THEMES = {"Chiaro": "light", "Scuro": "dark", "Come Windows": "system"}
GIORNI = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]


def pct(x):
    return f"{x * 100:5.1f}%"


def fair(x):
    return f"{1 / x:5.2f}" if x > 0.001 else "  -  "


def fmt_date(iso, t=""):
    try:
        d = date.fromisoformat(iso)
        s = f"{GIORNI[d.weekday()]} {d:%d/%m}"
    except Exception:
        s = iso or ""
    return f"{s} {t}".strip()


def fmt_dt(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return "mai"


class FootballApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode(settings.load(updater.data_dir()).get("theme", "light"))
        ctk.set_default_color_theme("blue")
        self.title(f"Football Predictor Pro {APP_VERSION}")
        self.geometry("1240x860")
        self.minsize(1050, 720)
        self.configure(fg_color=C["bg"])
        self.after(100, self.lift)
        self.after(150, lambda: self.attributes("-topmost", False))

        self.data = updater.load_data()
        self.models: dict[str, FootballPredictor] = {}
        self.current_league = DEFAULT_LEAGUE
        self.updating = False
        self.q: queue.Queue = queue.Queue()
        self.fixture_odds = None   # quote della partita scelta dall'elenco "Prossime partite"

        self._build_header()
        self._build_tabs()
        self._build_statusbar()
        self.refresh_all()

        self.after(200, self._poll_queue)
        self.after(1500, self._auto_update_check)
        self.after(4000, lambda: self.check_app_update(silent=True))

    # ================================================================== layout
    def _build_header(self):
        top = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=0)
        top.pack(fill="x")
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left", padx=20, pady=12)
        ctk.CTkLabel(left, text="Football Predictor Pro", font=("Helvetica", 26, "bold")).pack(anchor="w")
        ctk.CTkLabel(left, text="Pronostici statistici · modello Dixon-Coles con forma recente e fattore campo",
                     font=("Helvetica", 12), text_color=C["muted"]).pack(anchor="w")

        right = ctk.CTkFrame(top, fg_color="transparent")
        right.pack(side="right", padx=20, pady=12)
        ctk.CTkLabel(right, text="Campionato", font=("Helvetica", 13, "bold")).grid(row=0, column=0, padx=(0, 8))
        self.combo_league = ctk.CTkComboBox(right, values=self._league_options(), command=self.change_league,
                                            width=230, state="readonly")
        self.combo_league.grid(row=0, column=1, padx=(0, 12))
        self.btn_update = ctk.CTkButton(right, text="AGGIORNA DATI", command=self.start_update,
                                        fg_color=C["accent"], hover_color=C["accent_h"], width=170, height=34,
                                        font=("Helvetica", 13, "bold"))
        self.btn_update.grid(row=0, column=2)
        ctk.CTkButton(right, text="Impostazioni", command=self.open_settings, width=110, height=34,
                      fg_color=C["panel2"], hover_color=C["line"], text_color=C["text"]).grid(row=0, column=3, padx=(10, 0))
        self.label_round = ctk.CTkLabel(right, text="", font=("Helvetica", 12, "italic"), text_color=C["muted"])
        self.label_round.grid(row=1, column=0, columnspan=4, sticky="e", pady=(4, 0))

    def _build_tabs(self):
        self.tabs = ctk.CTkTabview(self, fg_color=C["panel"], segmented_button_selected_color=C["red"],
                                   segmented_button_selected_hover_color=C["red_h"])
        self.tabs.pack(fill="both", expand=True, padx=14, pady=(10, 4))
        for name in ("Prossime partite", "Analisi partita", "Classifica", "Storico pronostici", "Affidabilità"):
            self.tabs.add(name)
        self._build_fixtures_tab(self.tabs.tab("Prossime partite"))
        self._build_analysis_tab(self.tabs.tab("Analisi partita"))
        self._build_table_tab(self.tabs.tab("Classifica"))
        self._build_history_tab(self.tabs.tab("Storico pronostici"))
        self._build_reliability_tab(self.tabs.tab("Affidabilità"))

    def _build_fixtures_tab(self, tab):
        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.pack(fill="x", padx=4, pady=(0, 4))
        ctk.CTkLabel(bar, text="Esporta i pronostici dei prossimi 7 giorni:", text_color=C["muted"],
                     font=("Helvetica", 12)).pack(side="left", padx=(4, 8))
        self.var_export_all = ctk.BooleanVar(value=True)
        ctk.CTkButton(bar, text="Excel", width=80, height=28, command=lambda: self.export("xlsx"),
                      fg_color=C["accent"], hover_color=C["accent_h"]).pack(side="left", padx=3)
        ctk.CTkButton(bar, text="PDF", width=80, height=28, command=lambda: self.export("pdf"),
                      fg_color=C["accent"], hover_color=C["accent_h"]).pack(side="left", padx=3)
        ctk.CTkCheckBox(bar, text="tutti i campionati", variable=self.var_export_all,
                        font=("Helvetica", 12)).pack(side="left", padx=10)
        head = ctk.CTkFrame(tab, fg_color=C["panel2"], corner_radius=6)
        head.pack(fill="x", padx=4, pady=(4, 0))
        self._fixture_cols(head, ["Data", "Partita", "1", "X", "2", "Over 2.5", "Goal", "Ris. più prob.", "Quote 1-X-2", "Valore", ""],
                           header=True)
        self.fixtures_frame = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self.fixtures_frame.pack(fill="both", expand=True, padx=0, pady=4)
        self.fixtures_note = ctk.CTkLabel(tab, text="", text_color=C["muted"], font=("Helvetica", 11), justify="left")
        self.fixtures_note.pack(anchor="w", padx=8, pady=(0, 4))

    WIDTHS = [95, 300, 58, 58, 58, 72, 62, 92, 128, 110, 90]

    def _fixture_cols(self, parent, values, header=False, colors=None):
        colors = colors or {}
        for i, (v, w) in enumerate(zip(values, self.WIDTHS)):
            if isinstance(v, ctk.CTkBaseClass):
                v.grid(row=0, column=i, padx=3, pady=4)
                continue
            lbl = ctk.CTkLabel(parent, text=v, width=w, anchor="w" if i in (0, 1) else "center",
                               font=("Helvetica", 12, "bold" if header or i in colors else "normal"),
                               text_color=colors.get(i, C["muted"] if header else C["text"]))
            lbl.grid(row=0, column=i, padx=3, pady=6)

    def _build_analysis_tab(self, tab):
        sel = ctk.CTkFrame(tab, fg_color=C["panel2"], corner_radius=8)
        sel.pack(fill="x", padx=4, pady=4)
        ctk.CTkLabel(sel, text="Casa", font=("Helvetica", 13, "bold")).grid(row=0, column=0, padx=(16, 6), pady=12)
        self.combo_home = ctk.CTkComboBox(sel, values=[], width=260, command=lambda _: self._clear_fixture_odds())
        self.combo_home.grid(row=0, column=1, padx=6)
        ctk.CTkLabel(sel, text="Ospite", font=("Helvetica", 13, "bold")).grid(row=0, column=2, padx=(18, 6))
        self.combo_away = ctk.CTkComboBox(sel, values=[], width=260, command=lambda _: self._clear_fixture_odds())
        self.combo_away.grid(row=0, column=3, padx=6)
        self.btn_predict = ctk.CTkButton(sel, text="ANALIZZA PARTITA", command=self.run_prediction,
                                         font=("Helvetica", 15, "bold"), height=40, width=210,
                                         fg_color=C["red"], hover_color=C["red_h"])
        self.btn_predict.grid(row=0, column=4, padx=(24, 16))

        summ = ctk.CTkFrame(tab, fg_color=C["panel2"], corner_radius=8)
        summ.pack(fill="x", padx=4, pady=4)
        self.label_match = ctk.CTkLabel(summ, text="Scegli due squadre e premi ANALIZZA PARTITA",
                                        font=("Helvetica", 17, "bold"))
        self.label_match.grid(row=0, column=0, columnspan=6, sticky="w", padx=16, pady=(10, 2))
        self.label_xg = ctk.CTkLabel(summ, text="", text_color=C["muted"], font=("Helvetica", 12))
        self.label_xg.grid(row=1, column=0, columnspan=6, sticky="w", padx=16)
        self.bars = {}
        for i, (k, lab) in enumerate((("1", "1 Casa"), ("X", "X Pareggio"), ("2", "2 Ospite"))):
            ctk.CTkLabel(summ, text=lab, font=("Helvetica", 12, "bold")).grid(row=2, column=i * 2, padx=(16, 6), pady=10, sticky="e")
            bar = ctk.CTkProgressBar(summ, width=210, height=14,
                                     progress_color=[C["blue"], C["muted"], C["gold"]][i])
            bar.set(0)
            bar.grid(row=2, column=i * 2 + 1, padx=(0, 8), sticky="w")
            val = ctk.CTkLabel(summ, text="", font=("Helvetica", 12, "bold"))
            val.grid(row=3, column=i * 2 + 1, sticky="w", pady=(0, 8))
            self.bars[k] = (bar, val)

        self.subtabs = ctk.CTkTabview(tab, fg_color="transparent", height=420,
                                      segmented_button_selected_color=C["blue"])
        self.subtabs.pack(fill="both", expand=True, padx=0, pady=(0, 2))
        for name in ("Esito e gol", "Primo tempo", "Angoli · Cartellini · Tiri"):
            self.subtabs.add(name)

        def boxes(parent, n):
            out = []
            for i in range(n):
                tb = self._textbox(parent)
                tb.grid(row=0, column=i, padx=4, sticky="nsew")
                parent.grid_columnconfigure(i, weight=1)
                out.append(tb)
            parent.grid_rowconfigure(0, weight=1)
            return out

        self.result_exact, self.result_main, self.result_extra = boxes(self.subtabs.tab("Esito e gol"), 3)
        self.ht_boxes = boxes(self.subtabs.tab("Primo tempo"), 3)
        self.stat_boxes = boxes(self.subtabs.tab("Angoli · Cartellini · Tiri"), 3)

    def _textbox(self, parent):
        return ctk.CTkTextbox(parent, font=("Consolas" if os.name == "nt" else "Courier New", 13),
                              fg_color=C["panel2"], wrap="none")

    def _build_table_tab(self, tab):
        self.table_box = self._textbox(tab)
        self.table_box.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_history_tab(self, tab):
        self.hist_summary = ctk.CTkTextbox(tab, font=("Helvetica", 13), fg_color=C["panel2"], wrap="word", height=250)
        self.hist_summary.pack(fill="x", padx=4, pady=4)
        self.hist_box = self._textbox(tab)
        self.hist_box.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_reliability_tab(self, tab):
        self.rel_box = ctk.CTkTextbox(tab, font=("Helvetica", 14), fg_color=C["panel2"], wrap="word")
        self.rel_box.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=0, height=34)
        bar.pack(fill="x", side="bottom")
        self.status = ctk.CTkLabel(bar, text="", text_color=C["muted"], font=("Helvetica", 11))
        self.status.pack(side="left", padx=14, pady=6)
        self.progress = ctk.CTkProgressBar(bar, width=160, height=8)
        self.progress.set(0)
        self.var_task = ctk.BooleanVar(value=updater.task_exists())
        self.switch_task = ctk.CTkSwitch(bar, text="Aggiorna anche ad app chiusa (ogni 6 ore)",
                                         variable=self.var_task, command=self.toggle_task, font=("Helvetica", 11))
        if updater.scheduler_available():
            self.switch_task.pack(side="right", padx=14)

    # ================================================================== dati
    def _league_options(self):
        have = list(self.data.get("leagues", {}).keys()) if self.data else []
        return [n for n in LEAGUES if n in have] or ["Nessun dato: premi AGGIORNA DATI"]

    def league_data(self):
        return self.data.get("leagues", {}).get(self.current_league) if self.data else None

    def model(self) -> FootballPredictor | None:
        ld = self.league_data()
        if not ld:
            return None
        if self.current_league not in self.models:
            self.models[self.current_league] = FootballPredictor(ld, blend_w=self.blend_w(), xg_beta=self.xg_beta())
        return self.models[self.current_league]

    def xg_beta(self) -> float:
        return float(((self.data or {}).get("xg") or {}).get("beta", 0.0))

    def blend_w(self) -> float:
        return float(((self.data or {}).get("blend") or {}).get("w", DEFAULT_BLEND))

    def refresh_all(self):
        opts = self._league_options()
        self.combo_league.configure(values=opts)
        if self.current_league not in opts:
            self.current_league = opts[0]
        self.combo_league.set(self.current_league)
        self.update_round_label()
        self.update_team_combos()
        self.render_fixtures()
        self.render_table()
        self.render_history()
        self.render_reliability()
        self.update_status()

    def change_league(self, name):
        self.current_league = name
        self._clear_fixture_odds()
        self.update_round_label()
        self.update_team_combos()
        self.render_fixtures()
        self.render_table()
        self.render_history()
        self.render_reliability()

    def update_round_label(self):
        ld = self.league_data()
        if not ld:
            self.label_round.configure(text="Nessun dato disponibile")
            return
        last = ld.get("last_result")
        last_s = date.fromisoformat(last).strftime("%d/%m/%Y") if last else "nessuna partita giocata"
        self.label_round.configure(text=f"Stagione {ld['season']} · {ld['played']} partite giocate · "
                                        f"ultimo risultato {last_s} · fonte {ld['source']}")

    def update_team_combos(self):
        ld = self.league_data()
        teams = sorted(ld.get("teams", [])) if ld else []
        self.combo_home.configure(values=teams)
        self.combo_away.configure(values=teams)
        if teams:
            self.combo_home.set(teams[0])
            self.combo_away.set(teams[1] if len(teams) > 1 else teams[0])
        else:
            self.combo_home.set("")
            self.combo_away.set("")

    def update_status(self):
        if not self.data:
            self.status.configure(text="Nessun dato salvato: premi AGGIORNA DATI (serve la connessione internet).")
            return
        age = updater.data_age_hours(self.data)
        nxt = "a breve" if age is None or age >= updater.AUTO_MAX_AGE_H else \
            f"tra circa {max(0.1, updater.AUTO_MAX_AGE_H - age):.1f} ore"
        self.status.configure(text=f"Dati aggiornati il {fmt_dt(self.data.get('updated_at'))} · "
                                   f"prossimo aggiornamento automatico {nxt} · cartella dati: {updater.data_dir()}")

    # ================================================================== prossime partite
    def render_fixtures(self):
        for w in self.fixtures_frame.winfo_children():
            w.destroy()
        ld, m = self.league_data(), self.model()
        if not ld or not m:
            self.fixtures_note.configure(text="Nessun dato. Premi AGGIORNA DATI.")
            return
        fx = ld.get("fixtures", [])
        if not fx:
            self.fixtures_note.configure(text="Nessuna partita in calendario nei prossimi 21 giorni "
                                              "(pausa per le nazionali o calendario non ancora pubblicato). "
                                              "Puoi comunque analizzare qualsiasi partita nella scheda Analisi partita.")
            return
        n_val = 0
        for i, f in enumerate(fx):
            r = m.predict_detailed(f["home"], f["away"], f.get("odds"))
            if not r:
                continue
            row = ctk.CTkFrame(self.fixtures_frame, fg_color=C["panel2"] if i % 2 == 0 else C["panel"], corner_radius=6)
            row.pack(fill="x", pady=1)
            p = m.best_1x2(r)
            best = max(p, key=p.get)
            (sh, sa), _ = r["exact_scores"][0]
            odds = f.get("odds")
            q = f"{odds['H']:.2f} {odds['D']:.2f} {odds['A']:.2f}" if odds else "—"
            val = ""
            bv = best_value(r)
            if bv:
                val = f"{bv['market']} +{bv['ev'] * 100:.0f}%"
                n_val += 1
            over = ((r.get("blend") or {}).get("UnderOver25") or r["UnderOver25"])["Over"]
            btn = ctk.CTkButton(row, text="Analizza", width=80, height=26, fg_color=C["red"], hover_color=C["red_h"],
                                command=lambda f=f: self.open_fixture(f))
            colors = {2 + "1X2".index(best): C["gold"]}
            if val:
                colors[9] = C["gold"]
            self._fixture_cols(row, [fmt_date(f["date"], f.get("time", "")), f"{f['home']}  –  {f['away']}",
                                     pct(p["1"]).strip(), pct(p["X"]).strip(), pct(p["2"]).strip(),
                                     pct(over).strip(), pct(r["GoalNoGoal"]["Goal"]).strip(),
                                     f"{sh}-{sa}", q, val, btn], colors=colors)
        note = ("In giallo l'esito più probabile. Dove ci sono le quote, 1X2 e Over sono la stima combinata "
                f"modello + bookmaker (peso del modello {self.blend_w() * 100:.0f}%, ricalibrato a ogni aggiornamento).")
        if n_val:
            note += ("\n«Valore» = la quota paga più di quanto la stima combinata ritiene giusto "
                     "(vantaggio atteso ≥ 3%). È una stima statistica, non una certezza.")
        self.fixtures_note.configure(text=note)

    def open_fixture(self, f):
        self.combo_home.set(f["home"])
        self.combo_away.set(f["away"])
        self.fixture_odds = f.get("odds")
        self.tabs.set("Analisi partita")
        self.run_prediction(keep_odds=True)

    def _clear_fixture_odds(self):
        self.fixture_odds = None

    # ================================================================== analisi partita
    def _write(self, tb, text):
        tb.configure(state="normal")
        tb.delete("1.0", "end")
        tb.insert("end", text)
        tb.configure(state="disabled")

    def run_prediction(self, keep_odds=False):
        home, away = self.combo_home.get(), self.combo_away.get()
        if not keep_odds:
            self.fixture_odds = self._find_fixture_odds(home, away)
        m = self.model()
        if not m:
            self._write(self.result_exact, "Nessun dato: premi AGGIORNA DATI.")
            return
        if home == away:
            self._write(self.result_exact, "Errore: hai scelto la stessa squadra.")
            return
        try:
            r = m.predict_detailed(home, away, self.fixture_odds)
        except Exception as e:
            self._write(self.result_exact, f"Errore: {e}")
            return
        if not r:
            self._write(self.result_exact, "Squadra non trovata nei dati.")
            return

        lh, la = r["xg"]
        self.label_match.configure(text=f"{home}  –  {away}")
        extra = ""
        if home in getattr(m, "promoted", []) or away in getattr(m, "promoted", []):
            extra = "  ·  attenzione: neopromossa, stima meno affidabile a inizio stagione"
        self.label_xg.configure(text=f"Gol attesi: {home} {lh:.2f}  –  {la:.2f} {away}{extra}")
        pb = m.best_1x2(r)
        for k, (bar, val) in self.bars.items():
            bar.set(pb[k])
            if r.get("blend"):
                mk_p = {x["market"]: x["p_market"] for x in r["market"]["rows"]}
                val.configure(text=f"{pct(pb[k]).strip()} combinata  ·  modello {pct(r['1X2'][k]).strip()}  ·  "
                                   f"quote {pct(mk_p[k]).strip()}")
            else:
                val.configure(text=f"{pct(pb[k]).strip()}   quota equa {fair(pb[k]).strip()}")

        W = 30
        t = "RISULTATI ESATTI PIÙ PROBABILI\n" + "─" * W + "\n"
        t += f"{'Risultato':<12}{'Prob.':>8}{'Q. equa':>10}\n"
        for (h, a), q in r["exact_scores"]:
            t += f"{h} - {a:<8}{pct(q):>8}{fair(q):>10}\n"
        if r.get("blend"):
            t += "\nESITO 1X2\n" + "─" * W + "\n"
            t += f"{'':<5}{'Modello':>9}{'Combinata':>11}{'Q.eq':>6}\n"
            for k in ("1", "X", "2"):
                t += f" {k:<4}{pct(r['1X2'][k]):>9}{pct(pb[k]):>11}{fair(pb[k]):>6}\n"
        else:
            t += "\nESITO 1X2\n" + "─" * W + "\n"
            for k in ("1", "X", "2"):
                t += f" {k:<11}{pct(r['1X2'][k]):>8}{fair(r['1X2'][k]):>10}\n"
        t += "\nDOPPIA CHANCE\n" + "─" * W + "\n"
        for k, v in r["DoppiaChance"].items():
            t += f" {k:<11}{pct(v):>8}{fair(v):>10}\n"
        self._write(self.result_exact, t)

        t = "UNDER / OVER\n" + "─" * W + "\n"
        t += f"{'Linea':<8}{'Under':>10}{'Over':>10}\n"
        for l, v in r["UnderOver"].items():
            t += f" {l:<7}{pct(v['Under']):>10}{pct(v['Over']):>10}\n"
        t += "\nGOAL / NO GOAL\n" + "─" * W + "\n"
        t += f" Goal      {pct(r['GoalNoGoal']['Goal']):>8}{fair(r['GoalNoGoal']['Goal']):>10}\n"
        t += f" No Goal   {pct(r['GoalNoGoal']['NoGoal']):>8}{fair(r['GoalNoGoal']['NoGoal']):>10}\n"
        t += "\nMULTIGOL TOTALE\n" + "─" * W + "\n"
        for k, v in r["Multigol"].items():
            t += f" MG {k:<7}{pct(v):>8}{fair(v):>10}\n"
        t += "\nCOMBO\n" + "─" * W + "\n"
        for k, v in r["Combo"].items():
            t += f" {k:<17}{pct(v):>8}\n"
        self._write(self.result_main, t)

        t = f"MULTIGOL CASA ({home[:16]})\n" + "─" * W + "\n"
        for k, v in r["MultigolHome"].items():
            t += f" MG {k:<7}{pct(v):>8}{fair(v):>10}\n"
        t += f"\nMULTIGOL OSPITE ({away[:16]})\n" + "─" * W + "\n"
        for k, v in r["MultigolAway"].items():
            t += f" MG {k:<7}{pct(v):>8}{fair(v):>10}\n"
        t += "\nSOMMA GOL ESATTA\n" + "─" * W + "\n"
        for k, v in r["SommaGol"].items():
            t += f" {k:>2} gol    {pct(v):>8}{fair(v):>10}\n"
        mk = r.get("market")
        if mk and mk.get("rows"):
            t += "\nCONFRONTO CON LE QUOTE\n" + "─" * W + "\n"
            t += f"{'Esito':<10}{'Quota':>6}{'Book':>6}{'Mod.':>6}{'Comb':>6}\n"
            for x in mk["rows"]:
                flag = " ◄" if x["value"] else ""
                t += (f" {x['market']:<9}{x['odds']:>6.2f}{x['p_market'] * 100:>5.0f}%{x['p_model'] * 100:>5.0f}%"
                      f"{x['p_blend'] * 100:>5.0f}%{flag}\n")
            t += f"\n ◄ = valore (vantaggio atteso ≥ 3%)\n Comb = {mk['w'] * 100:.0f}% modello + {(1 - mk['w']) * 100:.0f}% quote\n"
            if mk.get("margin") is not None:
                t += f"\n Margine bookmaker: {mk['margin'] * 100:.1f}%\n"
        else:
            t += "\nQuote dei bookmaker non disponibili\nper questa partita.\n"
        self._write(self.result_extra, t)
        self._render_halftime(r, home, away)
        self._render_stats(r, home, away)

    def _render_halftime(self, r, home, away):
        pt = r.get("PrimoTempo")
        W = 30
        if not pt:
            for b in self.ht_boxes:
                self._write(b, "")
            return
        t = "ESITO 1° TEMPO\n" + "─" * W + "\n"
        for k in "1X2":
            t += f" {k:<11}{pct(pt['1X2'][k]):>8}{fair(pt['1X2'][k]):>10}\n"
        t += "\nESITO 2° TEMPO (solo gol del 2°T)\n" + "─" * W + "\n"
        for k in "1X2":
            t += f" {k:<11}{pct(pt['1X2_ST'][k]):>8}{fair(pt['1X2_ST'][k]):>10}\n"
        lh1, la1 = pt["xg_pt"]
        t += f"\nGol attesi nel 1° tempo:\n {home[:14]} {lh1:.2f} - {la1:.2f} {away[:14]}\n"
        self._write(self.ht_boxes[0], t)
        t = "PARZIALE / FINALE\n" + "─" * W + "\n"
        for k, v in sorted(pt["PTFT"].items(), key=lambda kv: -kv[1]):
            t += f" {k:<11}{pct(v):>8}{fair(v):>10}\n"
        self._write(self.ht_boxes[1], t)
        t = "UNDER / OVER 1° TEMPO\n" + "─" * W + "\n"
        t += f"{'Linea':<8}{'Under':>10}{'Over':>10}\n"
        for l, v in pt["UO_PT"].items():
            t += f" {l:<7}{pct(v['Under']):>10}{pct(v['Over']):>10}\n"
        t += "\nUNDER / OVER 2° TEMPO\n" + "─" * W + "\n"
        for l, v in pt["UO_ST"].items():
            t += f" {l:<7}{pct(v['Under']):>10}{pct(v['Over']):>10}\n"
        t += "\nTEMPO CON PIÙ GOL\n" + "─" * W + "\n"
        for k, v in pt["TempoPiuGol"].items():
            t += f" {k:<11}{pct(v):>8}{fair(v):>10}\n"
        t += f"\n Gol nel 1° tempo   {pct(pt['Gol1T']):>8}\n Gol nel 2° tempo   {pct(pt['Gol2T']):>8}\n"
        self._write(self.ht_boxes[2], t)

    def _render_stats(self, r, home, away):
        st = r.get("Stats") or {}
        titles = {"Angoli": "CALCI D'ANGOLO", "Cartellini": "CARTELLINI (gialli + rossi)", "TiriPorta": "TIRI IN PORTA"}
        W = 30
        for box, name in zip(self.stat_boxes, ("Angoli", "Cartellini", "TiriPorta")):
            s = st.get(name)
            t = titles[name] + "\n" + "─" * W + "\n"
            if not s:
                t += ("Dati non disponibili per questo\ncampionato o per questa fonte.\n\n"
                      "Servono le statistiche partita\ndi football-data.co.uk.\n")
                self._write(box, t)
                continue
            mh, ma = s["exp"]
            t += f" Attesi: {home[:11]} {mh:.1f}\n         {away[:11]} {ma:.1f}\n         Totale {mh + ma:.1f}\n"
            t += f"\nTOTALE PARTITA\n{'Linea':<8}{'Under':>10}{'Over':>10}\n"
            for l, v in s["tot"].items():
                t += f" {l:<7}{pct(v['Under']):>10}{pct(v['Over']):>10}\n"
            t += f"\n{home[:22].upper()}\n"
            for l, v in s["home"].items():
                t += f" {l:<7}{pct(v['Under']):>10}{pct(v['Over']):>10}\n"
            t += f"\n{away[:22].upper()}\n"
            for l, v in s["away"].items():
                t += f" {l:<7}{pct(v['Under']):>10}{pct(v['Over']):>10}\n"
            lab = {"Angoli": "più angoli", "Cartellini": "più cartellini", "TiriPorta": "più tiri in porta"}[name]
            t += f"\nCHI NE FA DI PIÙ ({lab})\n"
            t += f" 1 {pct(s['1X2']['1'])}   X {pct(s['1X2']['X'])}   2 {pct(s['1X2']['2'])}\n"
            t += f"\n(stima su {s['n']} partite; i cartellini\n dipendono molto dall'arbitro)\n" if name == "Cartellini" \
                else f"\n(stima su {s['n']} partite)\n"
            self._write(box, t)

    def _find_fixture_odds(self, home, away):
        ld = self.league_data() or {}
        for f in ld.get("fixtures", []):
            if f["home"] == home and f["away"] == away:
                return f.get("odds")
        return None

    # ================================================================== classifica
    def render_table(self):
        ld, m = self.league_data(), self.model()
        if not ld:
            self._write(self.table_box, "Nessun dato.")
            return
        rows = standings(ld)
        rat = {x["team"]: x for x in (m.team_ratings() if m else [])}
        t = f"CLASSIFICA {self.current_league.upper()} {ld['season']}   (calcolata dai risultati; eventuali penalizzazioni non incluse)\n\n"
        t += f"{'#':>3}  {'Squadra':<26}{'PG':>4}{'V':>4}{'N':>4}{'P':>4}{'GF':>5}{'GS':>5}{'DR':>5}{'Pt':>5}   {'Forma':<7}{'Att.':>7}{'Dif.':>7}\n"
        t += "─" * 104 + "\n"
        for r in rows:
            a = rat.get(r["team"])
            at = f"{a['attack']:.2f}" if a else "-"
            de = f"{a['defense']:.2f}" if a else "-"
            t += (f"{r['rank']:>3}  {r['team'][:25]:<26}{r['pg']:>4}{r['v']:>4}{r['n']:>4}{r['p']:>4}{r['gf']:>5}"
                  f"{r['gs']:>5}{r['gf'] - r['gs']:>+5}{r['pts']:>5}   {r['form']:<7}{at:>7}{de:>7}\n")
        t += ("\nForma = ultime 5 partite (V vinta, N pari, P persa), la più recente a destra.\n"
              "Att. / Dif. = forza stimata dal modello (1.00 = media del campionato).\n"
              "Attacco sopra 1 = segna più della media; difesa sotto 1 = subisce meno della media.\n")
        if m:
            t += f"Fattore campo stimato: +{(pow(2.718281828, m.home_adv) - 1) * 100:.0f}% gol per chi gioca in casa.\n"
        self._write(self.table_box, t)

    # ================================================================== storico
    def render_history(self):
        h = history.load(updater.data_dir())
        lg = self.current_league
        s_l, s_all = history.summary(h, lg), history.summary(h)

        def block(title, s):
            if not s.get("n"):
                return (f"{title}: nessuna partita ancora conclusa"
                        f"{' (' + str(s['pending']) + ' pronostici in attesa del risultato)' if s.get('pending') else ''}.\n")
            t = (f"{title}: {s['n']} partite concluse, {s['pending']} in attesa\n"
                 f"   1X2 indovinato {s['acc_1x2'] * 100:.0f}%  ·  Over/Under 2.5 {s['acc_over'] * 100:.0f}%  ·  "
                 f"Goal/No Goal {s['acc_gg'] * 100:.0f}%  ·  risultato esatto {s['acc_score'] * 100:.0f}%  ·  RPS {s['rps']:.3f}\n")
            if s.get("n_sure"):
                t += f"   Pronostici con almeno il 60%: {s['n_sure']}, indovinati {s['acc_sure'] * 100:.0f}%\n"
            v = s.get("value")
            if v:
                t += (f"   Segnalazioni «valore»: {v['n']}, vinte {v['won']} (quota media {v['avg_odds']:.2f}); "
                      f"puntando 1 € su ciascuna il saldo sarebbe {v['profit']:+.2f} € (rendimento {v['roi'] * 100:+.0f}%)\n")
            return t

        t = block(lg, s_l) + "\n" + block("Tutti i campionati", s_all)
        t += ("\nI pronostici vengono salvati prima della partita a ogni aggiornamento (anche quelli automatici) "
              "e confrontati con il risultato appena disponibile. È la misura più onesta della qualità del modello; "
              "servono almeno 100-200 partite perché i numeri diventino significativi.")
        self._write(self.hist_summary, t)

        rows = history.recent(h, lg)
        t = f"{'Data':<12}{'Partita':<40}{'Pron.':>6}{'Prob.':>7}{'Ris.esatto':>11}{'Valore':>14}{'Risultato':>11}  Esito\n"
        t += "─" * 112 + "\n"
        if not rows:
            t += "Nessun pronostico salvato per questo campionato: verranno salvati al prossimo aggiornamento.\n"
        for e in rows:
            p = e["p"]
            k = max(p, key=p.get)
            v = e.get("value")
            vs = f"{v['market']} @{v['odds']:.2f}" if v else ""
            if e.get("result"):
                rr = e["result"]
                res = f"{rr['hg']}-{rr['ag']}"
                out = "1" if rr["hg"] > rr["ag"] else "X" if rr["hg"] == rr["ag"] else "2"
                esito = "✓" if out == k else "✗"
                if v:
                    esito += "  valore " + ("✓" if history.value_won(v, rr) else "✗")
            else:
                res, esito = "—", "in attesa"
            match = f"{e['home']} – {e['away']}"[:38]
            t += (f"{fmt_date(e['date']):<12}{match:<40}{k:>6}{pct(p[k]):>7}{e['score']:>11}{vs:>14}{res:>11}  {esito}\n")
        self._write(self.hist_box, t)

    # ================================================================== affidabilità
    def render_reliability(self):
        rel = (self.data or {}).get("reliability", {}).get(self.current_league)
        t = f"Quanto è affidabile il modello in {self.current_league}?\n\n"
        if not rel or not rel.get("n"):
            t += ("Non ci sono ancora abbastanza partite recenti per misurarlo. "
                  "Il calcolo viene rifatto a ogni aggiornamento dei dati.\n")
        else:
            t += (f"Ho simulato il modello sulle ultime 10 settimane: ogni settimana l'ho ri-stimato usando solo i "
                  f"dati disponibili fino a quel giorno e ho confrontato le previsioni con i {rel['n']} risultati reali.\n\n")
            t += f"•  Esito 1X2 più probabile indovinato: {rel['acc_1x2'] * 100:.0f}% delle partite\n"
            if rel.get("acc_conf") is not None:
                t += (f"•  Quando il modello dà almeno il 55% a un esito ({rel['n_conf']} partite): "
                      f"indovinato il {rel['acc_conf'] * 100:.0f}%\n")
            t += f"•  Over/Under 2.5 indovinato: {rel['acc_over25'] * 100:.0f}%\n"
            t += f"•  Goal/No Goal indovinato: {rel['acc_gg'] * 100:.0f}%\n"
            t += f"•  Punteggio di errore RPS: {rel['rps']:.3f} (più basso è meglio; tirare a caso darebbe circa 0.24)\n"
            mk = rel.get("market")
            if mk:
                better = "meglio" if mk["rps_model_same"] < mk["rps"] else "peggio"
                t += (f"\nConfronto con i bookmaker sulle stesse {mk['n']} partite: le quote medie avrebbero indovinato "
                      f"il {mk['acc_1x2'] * 100:.0f}% (RPS {mk['rps']:.3f}); il modello ha fatto {better} "
                      f"(RPS {mk['rps_model_same']:.3f}).\n")
                if mk.get("rps_blend") is not None:
                    t += (f"La stima combinata ({mk['w'] * 100:.0f}% modello + {(1 - mk['w']) * 100:.0f}% quote) avrebbe "
                          f"indovinato il {mk['acc_blend'] * 100:.0f}% (RPS {mk['rps_blend']:.3f}).\n")
        bl = (self.data or {}).get("blend") or {}
        if bl:
            if bl.get("n", 0) >= 150 and "rps_blend" in bl:
                t += (f"\nPeso del modello nella stima combinata: {bl['w'] * 100:.0f}%, scelto automaticamente "
                      f"su {bl['n']} partite recenti di tutti i campionati con quote (RPS: solo modello {bl['rps_model']:.3f}, "
                      f"solo quote {bl['rps_market']:.3f}, combinata {bl['rps_blend']:.3f}).\n")
            else:
                t += (f"\nPeso del modello nella stima combinata: {bl.get('w', DEFAULT_BLEND) * 100:.0f}% (valore di partenza; "
                      "verrà calibrato automaticamente appena ci saranno abbastanza partite con quote).\n")
        xg = (self.data or {}).get("xg") or {}
        if xg.get("leagues"):
            rps = xg.get("rps") or {}
            t += (f"\nxG stimati dai tiri: peso attuale {float(xg.get('beta', 0)) * 100:.0f}% (0% = solo gol). "
                  "Ricalcolato a ogni aggiornamento confrontando sulle ultime 10 settimane: "
                  + ", ".join(f"{float(k) * 100:.0f}% → RPS {v:.4f}" for k, v in rps.items()) + ".\n")
            if self.current_league not in xg["leagues"]:
                t += "In questo campionato i tiri non sono disponibili: si usano solo i gol.\n"
        t += ("\nCome leggere questi numeri\n"
              "Nel calcio anche i modelli migliori indovinano l'esito 1X2 circa una volta su due: pareggi e sorprese "
              "sono frequenti. Le percentuali dell'app sono probabilità, non certezze; servono a capire quanto un "
              "esito è plausibile e a confrontarlo con le quote. Le quote dei bookmaker includono un margine e di "
              "solito sono molto precise: battere il mercato in modo costante è raro.\n\n"
              "Cosa il modello non vede: infortuni, squalifiche, turnover, cambi di allenatore, motivazioni di classifica, "
              "meteo. Tienine conto prima di decidere.\n\n"
              "Il gioco è vietato ai minori e può causare dipendenza.")
        self._write(self.rel_box, t)

    # ================================================================== esportazione
    def export(self, kind):
        from tkinter import filedialog, messagebox
        if not self.data or not self.data.get("leagues"):
            messagebox.showinfo("Esporta", "Nessun dato: premi prima AGGIORNA DATI.")
            return
        leagues = None if self.var_export_all.get() else [self.current_league]
        rows = exporter.collect(self.data, days=7, leagues=leagues)
        if not rows:
            messagebox.showinfo("Esporta", "Nessuna partita in calendario nei prossimi 7 giorni.")
            return
        ext = ".xlsx" if kind == "xlsx" else ".pdf"
        default = f"Pronostici_{date.today():%Y-%m-%d}{ext}"
        docs = os.path.join(os.path.expanduser("~"), "Documents")
        path = filedialog.asksaveasfilename(parent=self, defaultextension=ext, initialfile=default,
                                            initialdir=docs if os.path.isdir(docs) else None,
                                            filetypes=[("Excel", "*.xlsx")] if kind == "xlsx" else [("PDF", "*.pdf")])
        if not path:
            return
        try:
            (exporter.to_excel if kind == "xlsx" else exporter.to_pdf)(rows, path)
        except PermissionError:
            messagebox.showerror("Esporta", "Il file è aperto in un altro programma: chiudilo e riprova.")
            return
        except Exception as e:
            messagebox.showerror("Esporta", f"Errore: {e}")
            return
        self.status.configure(text=f"Esportate {len(rows)} partite in {path}")
        exporter.open_file(path)

    # ================================================================== impostazioni
    def phone_url(self, repo=None) -> str:
        repo = (repo if repo is not None else settings.load(updater.data_dir()).get("github_repo", "")).strip().strip("/")
        if repo.startswith("https://github.com/"):
            repo = repo[len("https://github.com/"):]
        if repo.count("/") != 1:
            return ""
        owner, name = repo.split("/")
        return f"https://{owner.lower()}.github.io/{name}/"

    def open_settings(self):
        if getattr(self, "_settings_win", None) and self._settings_win.winfo_exists():
            self._settings_win.focus()
            return
        cfg = settings.load(updater.data_dir())
        w = ctk.CTkToplevel(self)
        self._settings_win = w
        w.title("Impostazioni")
        w.geometry("640x660")
        w.configure(fg_color=C["panel"])
        w.after(100, w.lift)
        w.grid_columnconfigure(0, weight=1)
        pad = {"padx": 18, "sticky": "w"}

        def section(row, title, text):
            ctk.CTkLabel(w, text=title, font=("Helvetica", 15, "bold")).grid(row=row, column=0, pady=(14, 0), **pad)
            ctk.CTkLabel(w, text=text, text_color=C["muted"], font=("Helvetica", 12), justify="left",
                         wraplength=600).grid(row=row + 1, column=0, **pad)

        section(0, "Risultati in giornata (football-data.org)",
                "Chiave gratuita: registrati su football-data.org/client/register e incolla qui la chiave "
                "ricevuta per e-mail. Lascia vuoto per non usarla.")
        e_key = ctk.CTkEntry(w, width=420, placeholder_text="chiave API (32 caratteri)")
        e_key.insert(0, cfg.get("fdorg_key", ""))
        e_key.grid(row=2, column=0, pady=6, **pad)

        section(3, "GitHub: aggiornamento del programma e versione per telefono",
                "Nome del repository, nella forma utente/nome (vedi GUIDA_GITHUB.txt). "
                "Il programma controllerà da solo le nuove versioni pubblicate lì.")
        e_repo = ctk.CTkEntry(w, width=420, placeholder_text="es. mariorossi/football-predictor")
        e_repo.insert(0, cfg.get("github_repo", ""))
        e_repo.grid(row=5, column=0, pady=6, **pad)
        url_lbl = ctk.CTkLabel(w, text="", text_color=C["blue"], font=("Helvetica", 12, "underline"), cursor="hand2")
        url_lbl.grid(row=6, column=0, **pad)

        def refresh_url(*_):
            u = self.phone_url(e_repo.get())
            url_lbl.configure(text=f"Versione per telefono: {u}" if u else "")
        refresh_url()
        e_repo.bind("<KeyRelease>", refresh_url)
        url_lbl.bind("<Button-1>", lambda _: self.phone_url(e_repo.get()) and __import__("webbrowser").open(self.phone_url(e_repo.get())))

        ctk.CTkLabel(w, text="Notifiche e aggiornamenti", font=("Helvetica", 15, "bold")).grid(row=7, column=0, pady=(16, 4), **pad)
        v_val = ctk.BooleanVar(value=cfg.get("notify_value", True))
        v_upd = ctk.BooleanVar(value=cfg.get("notify_update", False))
        v_app = ctk.BooleanVar(value=cfg.get("auto_app_update", True))
        ctk.CTkSwitch(w, text="Notifica di Windows per nuove segnalazioni di valore", variable=v_val).grid(row=8, column=0, pady=3, **pad)
        ctk.CTkSwitch(w, text="Notifica di Windows a ogni aggiornamento dei dati", variable=v_upd).grid(row=9, column=0, pady=3, **pad)
        ctk.CTkSwitch(w, text="Controlla da solo le nuove versioni del programma", variable=v_app).grid(row=10, column=0, pady=3, **pad)
        trow = ctk.CTkFrame(w, fg_color="transparent")
        trow.grid(row=14, column=0, pady=(6, 0), **pad)
        ctk.CTkLabel(trow, text="Aspetto:", font=("Helvetica", 13, "bold")).pack(side="left", padx=(0, 10))
        cur = next((k for k, v in THEMES.items() if v == cfg.get("theme", "light")), "Chiaro")
        v_theme = ctk.StringVar(value=cur)

        def set_theme(label):
            cfg["theme"] = THEMES[label]
            settings.save(updater.data_dir(), cfg)
            ctk.set_appearance_mode(THEMES[label])
        ctk.CTkSegmentedButton(trow, values=list(THEMES), variable=v_theme, command=set_theme).pack(side="left")

        btns = ctk.CTkFrame(w, fg_color="transparent")
        btns.grid(row=11, column=0, pady=(18, 10), **pad)
        ctk.CTkButton(btns, text="Prova notifica", width=130,
                      command=lambda: notifier.notify("Football Predictor", "Le notifiche funzionano.")).pack(side="left", padx=(0, 8))

        def check_now():
            self._save_settings(cfg, e_key, e_repo, v_val, v_upd, v_app)
            self.check_app_update(silent=False)
        ctk.CTkButton(btns, text="Controlla nuova versione", width=190, command=check_now).pack(side="left", padx=8)
        ctk.CTkLabel(w, text=f"Versione installata: {APP_VERSION}", text_color=C["muted"]).grid(row=12, column=0, **pad)

        def save_close():
            self._save_settings(cfg, e_key, e_repo, v_val, v_upd, v_app)
            self.status.configure(text="Impostazioni salvate. La chiave football-data.org verrà usata dal prossimo aggiornamento.")
            w.destroy()
        ctk.CTkButton(w, text="SALVA", width=160, height=36, fg_color=C["accent"], hover_color=C["accent_h"],
                      command=save_close).grid(row=15, column=0, pady=16, padx=18, sticky="e")

    def _save_settings(self, cfg, e_key, e_repo, v_val, v_upd, v_app):
        cfg.update(fdorg_key=e_key.get().strip(), github_repo=e_repo.get().strip(),
                   notify_value=bool(v_val.get()), notify_update=bool(v_upd.get()), auto_app_update=bool(v_app.get()))
        settings.save(updater.data_dir(), cfg)

    # ================================================================== nuova versione del programma
    def check_app_update(self, silent=True):
        from tkinter import messagebox
        cfg = settings.load(updater.data_dir())
        repo = cfg.get("github_repo", "")
        if not repo:
            if not silent:
                messagebox.showinfo("Aggiornamento programma", "Inserisci prima il repository GitHub nelle impostazioni.")
            return
        if silent:
            if not cfg.get("auto_app_update", True):
                return
            try:
                last = datetime.fromisoformat(cfg.get("last_app_check") or "2000-01-01")
                if (datetime.now() - last).total_seconds() < 12 * 3600:
                    return
            except Exception:
                pass

        def worker():
            info = app_update.check(repo, APP_VERSION)
            cfg["last_app_check"] = datetime.now().isoformat(timespec="seconds")
            settings.save(updater.data_dir(), cfg)
            self.q.put(("app_update", info, silent))
        threading.Thread(target=worker, daemon=True).start()

    def _on_app_update(self, info, silent):
        from tkinter import messagebox
        if not info:
            if not silent:
                messagebox.showinfo("Aggiornamento programma", f"Hai già l'ultima versione ({APP_VERSION}).")
            return
        msg = f"È disponibile la versione {info['version']} (installata: {APP_VERSION})."
        if not app_update.can_self_update():
            messagebox.showinfo("Aggiornamento programma", msg + f"\n\nScaricala da:\n{info['page']}")
            return
        if messagebox.askyesno("Aggiornamento programma", msg + "\n\nInstallarla ora? Il programma si riavvierà."):
            self.status.configure(text="Scarico la nuova versione…")
            self.update()
            ok, text = app_update.download_and_restart(info)
            if ok:
                self.destroy()
            else:
                messagebox.showerror("Aggiornamento programma", text)

    # ================================================================== aggiornamento
    def _auto_update_check(self):
        if not self.updating and updater.needs_update(self.data):
            self.start_update(auto=True)
        elif not self.updating:
            self.update_status()
        self.after(20 * 60 * 1000, self._auto_update_check)   # ricontrolla ogni 20 minuti

    def start_update(self, auto=False):
        if self.updating:
            return
        self.updating = True
        self.btn_update.configure(state="disabled", text="Aggiornamento…")
        self.progress.set(0)
        self.progress.pack(side="left", padx=8)
        self.status.configure(text="Aggiornamento automatico in corso…" if auto else "Aggiornamento in corso…")
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self):
        try:
            data, errors = updater.run_update(progress=lambda i, n, name: self.q.put(("progress", i, n, name)))
            self.q.put(("done", data, errors))
        except Exception as e:
            self.q.put(("error", str(e)))

    def _poll_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "progress":
                    _, i, n, name = msg
                    self.progress.set(i / max(1, n))
                    if name:
                        self.btn_update.configure(text=f"{i + 1}/{n} {name[:12]}")
                elif msg[0] == "done":
                    self._finish_update(msg[1], msg[2])
                elif msg[0] == "error":
                    self._finish_update(None, [msg[1]])
                elif msg[0] == "app_update":
                    self._on_app_update(msg[1], msg[2])
        except queue.Empty:
            pass
        self.after(200, self._poll_queue)

    def _finish_update(self, data, errors):
        self.updating = False
        self.progress.pack_forget()
        if data and data.get("leagues"):
            self.data = data
            self.models.clear()
            self.refresh_all()
            if errors and errors != ["in corso"]:
                self.btn_update.configure(state="normal", text="AGGIORNA DATI", fg_color=C["accent"])
                self.status.configure(text=f"Aggiornato, ma non è stato possibile scaricare: {', '.join(errors)} "
                                           f"(restano i dati precedenti).")
            else:
                self.btn_update.configure(state="normal", text="AGGIORNA DATI", fg_color=C["accent"])
        else:
            self.btn_update.configure(state="normal", text="ERRORE · RIPROVA", fg_color=C["red"])
            self.status.configure(text="Aggiornamento non riuscito: controlla la connessione internet. "
                                       "Restano i dati precedenti. Riproverò automaticamente.")

    def toggle_task(self):
        if self.var_task.get():
            ok, msg = updater.enable_task()
        else:
            ok, msg = updater.disable_task()
            msg = "Aggiornamento ad app chiusa disattivato." if ok else msg
        if not ok:
            self.var_task.set(not self.var_task.get())
        self.status.configure(text=msg)


if __name__ == "__main__":
    app = FootballApp()
    app.mainloop()
