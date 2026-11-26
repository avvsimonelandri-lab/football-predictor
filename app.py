"""Football Predictor Pro — interfaccia."""
from __future__ import annotations

import os
import queue
import sys
import threading
from datetime import date, datetime

# avvio senza finestra (Utilità di pianificazione): solo aggiornamento dati
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
from predictor import DEFAULT_BLEND, FootballPredictor, best_value, head_to_head, standings, team_form

try:
    from version import VERSION as APP_VERSION
except Exception:
    APP_VERSION = "2.2"

# colori (chiaro, scuro) — stile sportivo su sfondo chiaro
C = {
    "bg": ("#EDF1F5", "#0f1115"), "panel": ("#FFFFFF", "#171a21"), "panel2": ("#F4F6F9", "#1e222b"),
    "line": ("#DDE3EA", "#2a2f3a"), "text": ("#0F1B2D", "#e7e9ee"), "muted": ("#5E6B7D", "#8a90a0"),
    "accent": ("#0A9F55", "#16B364"), "accent_h": ("#077A41", "#0E8A4C"),
    "red": ("#E5383B", "#E5383B"), "red_h": ("#C1121F", "#C1121F"),
    "gold": ("#C98A00", "#F2C230"), "blue": ("#1F6FEB", "#4F8EF7"),
    # intestazione e barre delle schede (blu notte, come i tabelloni)
    "header": ("#0F1B2D", "#0b0e13"), "header2": ("#1C2B42", "#1a2030"), "header_muted": ("#A9B6C8", "#8a90a0"),
    # colori degli esiti: casa, pareggio, ospite
    "home": ("#1F6FEB", "#4F8EF7"), "draw": ("#7D8A9C", "#9AA5B4"), "away": ("#F0812B", "#F59E4B"),
    "track": ("#E3E8EE", "#2a2f3a"), "under": ("#3B82F6", "#4F8EF7"), "over": ("#0A9F55", "#16B364"),
    "tile_hl": ("#E8F6EE", "#16301f"),
}
FONT = "Segoe UI" if os.name == "nt" else "Helvetica"
OUT_COL = {"1": C["home"], "X": C["draw"], "2": C["away"]}

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
        self.models = {}
        self.current_league = DEFAULT_LEAGUE
        self.updating = False
        self.q = queue.Queue()
        self.fixture_odds = None

        self._build_header()
        self._build_tabs()
        self._build_statusbar()
        self.refresh_all()

        self.after(200, self._poll_queue)
        self.after(1500, self._auto_update_check)
        self.after(4000, lambda: self.check_app_update(silent=True))

    # ---------------- costruzione interfaccia ----------------
    def _build_header(self):
        top = ctk.CTkFrame(self, fg_color=C["header"], corner_radius=0)
        top.pack(fill="x")
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left", padx=22, pady=14)
        ctk.CTkLabel(left, text="FOOTBALL PREDICTOR PRO", font=(FONT, 24, "bold"), text_color="#FFFFFF").pack(anchor="w")
        ctk.CTkLabel(left, text="Pronostici statistici · modello Dixon-Coles con forma recente e fattore campo",
                     font=(FONT, 12), text_color=C["header_muted"]).pack(anchor="w")

        right = ctk.CTkFrame(top, fg_color="transparent")
        right.pack(side="right", padx=22, pady=14)
        ctk.CTkLabel(right, text="Campionato", font=(FONT, 13, "bold"), text_color="#FFFFFF").grid(row=0, column=0, padx=(0, 8))
        self.combo_league = ctk.CTkComboBox(right, values=self._league_options(), command=self.change_league,
                                            width=230, height=34, state="readonly", font=(FONT, 13))
        self.combo_league.grid(row=0, column=1, padx=(0, 12))
        self.btn_update = ctk.CTkButton(right, text="AGGIORNA DATI", command=self.start_update,
                                        fg_color=C["accent"], hover_color=C["accent_h"], width=170, height=36,
                                        corner_radius=18, font=(FONT, 13, "bold"))
        self.btn_update.grid(row=0, column=2)
        ctk.CTkButton(right, text="Impostazioni", command=self.open_settings, width=120, height=36, corner_radius=18,
                      fg_color="transparent", border_width=1, border_color=C["header_muted"],
                      hover_color=C["header2"], text_color="#FFFFFF", font=(FONT, 13)).grid(row=0, column=3, padx=(10, 0))
        self.label_round = ctk.CTkLabel(right, text="", font=(FONT, 12), text_color=C["header_muted"])
        self.label_round.grid(row=1, column=0, columnspan=4, sticky="e", pady=(6, 0))
        # striscia verde "campo"
        ctk.CTkFrame(self, fg_color=C["accent"], corner_radius=0, height=4).pack(fill="x")

    def _build_tabs(self):
        self.tabs = ctk.CTkTabview(self, fg_color=C["panel"], corner_radius=14, border_width=1, border_color=C["line"],
                                   **self._tab_style())
        self.tabs.pack(fill="both", expand=True, padx=14, pady=(10, 4))
        for name in ("Prossime partite", "Analisi partita", "Classifica", "Storico pronostici", "Affidabilità"):
            self.tabs.add(name)
        self.tabs._segmented_button.configure(font=(FONT, 13, "bold"), height=34)
        self._build_fixtures_tab(self.tabs.tab("Prossime partite"))
        self._build_analysis_tab(self.tabs.tab("Analisi partita"))
        self._build_table_tab(self.tabs.tab("Classifica"))
        self._build_history_tab(self.tabs.tab("Storico pronostici"))
        self._build_reliability_tab(self.tabs.tab("Affidabilità"))

    @staticmethod
    def _tab_style(selected=None):
        return dict(segmented_button_fg_color=C["header"], segmented_button_unselected_color=C["header"],
                    segmented_button_unselected_hover_color=C["header2"],
                    segmented_button_selected_color=selected or C["accent"],
                    segmented_button_selected_hover_color=selected or C["accent_h"],
                    text_color="#FFFFFF")

    def _build_fixtures_tab(self, tab):
        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.pack(fill="x", padx=4, pady=(0, 4))
        ctk.CTkLabel(bar, text="Esporta i pronostici dei prossimi 7 giorni:", text_color=C["muted"],
                     font=(FONT, 12)).pack(side="left", padx=(4, 8))
        self.var_export_all = ctk.BooleanVar(value=True)
        ctk.CTkButton(bar, text="Excel", width=80, height=28, command=lambda: self.export("xlsx"),
                      fg_color=C["accent"], hover_color=C["accent_h"]).pack(side="left", padx=3)
        ctk.CTkButton(bar, text="PDF", width=80, height=28, command=lambda: self.export("pdf"),
                      fg_color=C["accent"], hover_color=C["accent_h"]).pack(side="left", padx=3)
        ctk.CTkCheckBox(bar, text="tutti i campionati", variable=self.var_export_all,
                        font=(FONT, 12)).pack(side="left", padx=10)
        head = ctk.CTkFrame(tab, fg_color=C["header"], corner_radius=8)
        head.pack(fill="x", padx=4, pady=(4, 0))
        self._fixture_cols(head, ["Data", "Partita", "1", "X", "2", "Over 2.5", "Goal", "Ris. più prob.",
                                  "Quote 1-X-2", "Valore", ""], header=True)
        self.fixtures_frame = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self.fixtures_frame.pack(fill="both", expand=True, padx=0, pady=4)
        self.fixtures_note = ctk.CTkLabel(tab, text="", text_color=C["muted"], font=(FONT, 11), justify="left")
        self.fixtures_note.pack(anchor="w", padx=8, pady=(0, 4))

    WIDTHS = [88, 280, 58, 58, 58, 72, 62, 92, 118, 100, 90]

    def _fixture_cols(self, parent, values, header=False, colors=None):
        colors = colors or {}
        for i, (v, w) in enumerate(zip(values, self.WIDTHS)):
            if isinstance(v, ctk.CTkBaseClass):
                v.grid(row=0, column=i, padx=3, pady=4)
                continue
            lbl = ctk.CTkLabel(parent, text=v, width=w, anchor="w" if i in (0, 1) else "center",
                               font=(FONT, 12, "bold" if header or i in colors else "normal"),
                               text_color=colors.get(i, "#FFFFFF" if header else C["text"]))
            lbl.grid(row=0, column=i, padx=3, pady=6)

    def _build_analysis_tab(self, tab):
        sel = ctk.CTkFrame(tab, fg_color=C["panel2"], corner_radius=12)
        sel.pack(fill="x", padx=4, pady=4)
        ctk.CTkLabel(sel, text="Casa", font=(FONT, 13, "bold")).grid(row=0, column=0, padx=(16, 6), pady=12)
        self.combo_home = ctk.CTkComboBox(sel, values=[], width=260, command=lambda _: self._clear_fixture_odds())
        self.combo_home.grid(row=0, column=1, padx=6)
        ctk.CTkLabel(sel, text="Ospite", font=(FONT, 13, "bold")).grid(row=0, column=2, padx=(18, 6))
        self.combo_away = ctk.CTkComboBox(sel, values=[], width=260, command=lambda _: self._clear_fixture_odds())
        self.combo_away.grid(row=0, column=3, padx=6)
        self.btn_predict = ctk.CTkButton(sel, text="ANALIZZA PARTITA", command=self.run_prediction,
                                         font=(FONT, 15, "bold"), height=40, width=210, corner_radius=20,
                                         fg_color=C["accent"], hover_color=C["accent_h"])
        self.btn_predict.grid(row=0, column=4, padx=(24, 16))

        summ = ctk.CTkFrame(tab, fg_color="transparent")
        summ.pack(fill="x", padx=8, pady=(2, 0))
        self.label_match = ctk.CTkLabel(summ, text="Scegli due squadre e premi ANALIZZA PARTITA",
                                        font=(FONT, 20, "bold"), text_color=C["text"])
        self.label_match.pack(anchor="w")
        self.label_xg = ctk.CTkLabel(summ, text="", text_color=C["muted"], font=(FONT, 12))
        self.label_xg.pack(anchor="w")

        self.subtabs = ctk.CTkTabview(tab, fg_color="transparent", height=460, **self._tab_style(C["gold"]))
        self.subtabs.pack(fill="both", expand=True, padx=0, pady=(0, 2))
        self.sub = {}
        for name in ("Esito e gol", "Primo tempo", "Angoli · Cartellini · Tiri", "Forma e precedenti"):
            self.subtabs.add(name)
            sf = ctk.CTkScrollableFrame(self.subtabs.tab(name), fg_color="transparent")
            sf.pack(fill="both", expand=True)
            for i in range(3):
                sf.grid_columnconfigure(i, weight=1, uniform="col")
            self.sub[name] = sf
        self.subtabs._segmented_button.configure(font=(FONT, 12, "bold"))

    def _textbox(self, parent):
        return ctk.CTkTextbox(parent, font=("Consolas" if os.name == "nt" else "Courier New", 13),
                              fg_color=C["panel2"], wrap="none")

    def _build_table_tab(self, tab):
        self.table_box = self._textbox(tab)
        self.table_box.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_history_tab(self, tab):
        self.hist_summary = ctk.CTkTextbox(tab, font=(FONT, 13), fg_color=C["panel2"], wrap="word", height=250)
        self.hist_summary.pack(fill="x", padx=4, pady=4)
        self.hist_box = self._textbox(tab)
        self.hist_box.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_reliability_tab(self, tab):
        self.rel_box = ctk.CTkTextbox(tab, font=(FONT, 14), fg_color=C["panel2"], wrap="word")
        self.rel_box.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=0, height=34)
        bar.pack(fill="x", side="bottom")
        self.status = ctk.CTkLabel(bar, text="", text_color=C["muted"], font=(FONT, 11))
        self.status.pack(side="left", padx=14, pady=6)
        self.progress = ctk.CTkProgressBar(bar, width=160, height=8)
        self.progress.set(0)
        self.var_task = ctk.BooleanVar(value=updater.task_exists())
        self.switch_task = ctk.CTkSwitch(bar, text="Aggiorna anche ad app chiusa (ogni 6 ore)",
                                         variable=self.var_task, command=self.toggle_task, font=(FONT, 11))
        if updater.scheduler_available():
            self.switch_task.pack(side="right", padx=14)

    # ---------------- dati e modello ----------------
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
        self.label_round.configure(text=f"Stagione {ld['season']} · {ld['played']} partite giocate · ultimo risultato "
                                        f"{last_s} · fonte {ld['source']}")

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
        nxt = ("a breve" if age is None or age >= updater.AUTO_MAX_AGE_H
               else f"tra circa {max(0.1, updater.AUTO_MAX_AGE_H - age):.1f} ore")
        self.status.configure(text=f"Dati aggiornati il {fmt_dt(self.data.get('updated_at'))} · prossimo aggiornamento "
                                   f"automatico {nxt} · cartella dati: {updater.data_dir()}")

    # ---------------- prossime partite ----------------
    def render_fixtures(self):
        for w in self.fixtures_frame.winfo_children():
            w.destroy()
        ld, m = self.league_data(), self.model()
        if not ld or not m:
            self.fixtures_note.configure(text="Nessun dato. Premi AGGIORNA DATI.")
            return
        today = date.today().isoformat()
        fx = [f for f in ld.get("fixtures", []) if f["date"] >= today]  # niente partite già giocate
        if not fx:
            self.fixtures_note.configure(text="Nessuna partita in calendario nei prossimi 21 giorni (pausa per le "
                                              "nazionali o calendario non ancora pubblicato). Puoi comunque "
                                              "analizzare qualsiasi partita nella scheda Analisi partita.")
            return
        n_val = 0
        for i, f in enumerate(fx):
            r = m.predict_detailed(f["home"], f["away"], f.get("odds"))
            if not r:
                continue
            row = ctk.CTkFrame(self.fixtures_frame, fg_color=C["panel2"] if i % 2 == 0 else C["panel"], corner_radius=8)
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
            btn = ctk.CTkButton(row, text="Analizza", width=80, height=28, corner_radius=14,
                                fg_color=C["accent"], hover_color=C["accent_h"], font=(FONT, 12, "bold"),
                                command=lambda f=f: self.open_fixture(f))
            colors = {2 + "1X2".index(best): C["accent"]}
            if val:
                colors[9] = C["gold"]
            self._fixture_cols(row, [fmt_date(f["date"], f.get("time", "")), f"{f['home']}  –  {f['away']}",
                                     pct(p["1"]).strip(), pct(p["X"]).strip(), pct(p["2"]).strip(),
                                     pct(over).strip(), pct(r["GoalNoGoal"]["Goal"]).strip(),
                                     f"{sh}-{sa}", q, val, btn], colors=colors)
        note = ("In verde l'esito più probabile, in oro le segnalazioni di valore. Dove ci sono le quote, 1X2 e Over sono la stima combinata modello + "
                f"bookmaker (peso del modello {self.blend_w() * 100:.0f}%, ricalibrato a ogni aggiornamento).")
        if n_val:
            note += ("\n«Valore» = la quota paga più di quanto la stima combinata ritiene giusto (vantaggio atteso ≥ 3%). "
                     "È una stima statistica, non una certezza.")
        self.fixtures_note.configure(text=note)

    def open_fixture(self, f):
        self.combo_home.set(f["home"])
        self.combo_away.set(f["away"])
        self.fixture_odds = f.get("odds")
        self.tabs.set("Analisi partita")
        self.run_prediction(keep_odds=True)

    def _clear_fixture_odds(self):
        self.fixture_odds = None

    def _write(self, tb, text):
        tb.configure(state="normal")
        tb.delete("1.0", "end")
        tb.insert("end", text)
        tb.configure(state="disabled")

    # ---------------- analisi partita ----------------
    def run_prediction(self, keep_odds=False):
        home, away = self.combo_home.get(), self.combo_away.get()
        if not keep_odds:
            self.fixture_odds = self._find_fixture_odds(home, away)
        m = self.model()
        if not m:
            self._analysis_message("Nessun dato: premi AGGIORNA DATI.")
            return
        if home == away:
            self._analysis_message("Errore: hai scelto la stessa squadra.")
            return
        try:
            r = m.predict_detailed(home, away, self.fixture_odds)
        except Exception as e:
            self._analysis_message(f"Errore: {e}")
            return
        if not r:
            self._analysis_message("Squadra non trovata nei dati.")
            return

        lh, la = r["xg"]
        self.label_match.configure(text=f"{home}  –  {away}")
        extra = ""
        if home in getattr(m, "promoted", []) or away in getattr(m, "promoted", []):
            extra = "  ·  attenzione: neopromossa, stima meno affidabile a inizio stagione"
        self.label_xg.configure(text=f"Gol attesi: {home} {lh:.2f}  –  {la:.2f} {away}{extra}")
        pb = m.best_1x2(r)
        self._render_main(r, pb, home, away)
        self._render_halftime(r, home, away)
        self._render_stats(r, home, away)
        self._render_form(home, away)

    # ---------------- mattoncini grafici ----------------
    @staticmethod
    def _clear(frame):
        for w in frame.winfo_children():
            w.destroy()

    def _card(self, parent, title, row, col, colspan=1, subtitle=None):
        card = ctk.CTkFrame(parent, fg_color=C["panel"], corner_radius=14, border_width=1, border_color=C["line"])
        card.grid(row=row, column=col, columnspan=colspan, padx=6, pady=6, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text=title.upper(), font=(FONT, 12, "bold"), text_color=C["accent"],
                     anchor="w").grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 0))
        card._r = 1
        if subtitle:
            ctk.CTkLabel(card, text=subtitle, font=(FONT, 11), text_color=C["muted"], anchor="w",
                         justify="left").grid(row=1, column=0, sticky="ew", padx=14)
            card._r = 2
        return card

    @staticmethod
    def _next(card):
        r = card._r
        card._r += 1
        return r

    def _heading(self, card, text):
        ctk.CTkLabel(card, text=text, font=(FONT, 12, "bold"), text_color=C["text"], anchor="w").grid(
            row=self._next(card), column=0, sticky="ew", padx=14, pady=(8, 2))

    def _note(self, card, text):
        ctk.CTkLabel(card, text=text, font=(FONT, 11), text_color=C["muted"], anchor="w", justify="left",
                     wraplength=340).grid(row=self._next(card), column=0, sticky="ew", padx=14, pady=(4, 10))

    def _bar(self, card, label, p, scale=1.0, color=None, right=None, bold=False):
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=self._next(card), column=0, sticky="ew", padx=14, pady=2)
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text=label, width=92, anchor="w", font=(FONT, 12, "bold" if bold else "normal"),
                     text_color=C["text"]).grid(row=0, column=0, sticky="w")
        b = ctk.CTkProgressBar(row, height=10, corner_radius=5, fg_color=C["track"], progress_color=color or C["accent"])
        b.set(max(0.0, min(1.0, p / scale if scale else 0.0)))
        b.grid(row=0, column=1, sticky="ew", padx=8)
        ctk.CTkLabel(row, text=f"{p * 100:.1f}%", width=50, anchor="e", font=(FONT, 12, "bold"),
                     text_color=C["text"]).grid(row=0, column=2)
        ctk.CTkLabel(row, text=fair(p).strip() if right is None else right, width=42, anchor="e", font=(FONT, 11),
                     text_color=C["muted"]).grid(row=0, column=3)

    def _uo(self, card, label, under, over):
        """Barra a due colori: a sinistra Under (blu), a destra Over (verde)."""
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=self._next(card), column=0, sticky="ew", padx=14, pady=2)
        row.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(row, text=str(label), width=36, anchor="w", font=(FONT, 12, "bold"),
                     text_color=C["text"]).grid(row=0, column=0)
        ctk.CTkLabel(row, text=f"U {under * 100:.0f}%", width=54, anchor="w", font=(FONT, 12, "bold" if under >= over else "normal"),
                     text_color=C["under"]).grid(row=0, column=1)
        b = ctk.CTkProgressBar(row, height=12, corner_radius=6, fg_color=C["over"], progress_color=C["under"])
        b.set(max(0.0, min(1.0, under)))
        b.grid(row=0, column=2, sticky="ew", padx=6)
        ctk.CTkLabel(row, text=f"{over * 100:.0f}% O", width=54, anchor="e", font=(FONT, 12, "bold" if over > under else "normal"),
                     text_color=C["over"]).grid(row=0, column=3)

    def _tiles(self, card, items, big=24):
        """Riquadri affiancati con numero grande. items: dict(title, value, sub, color, hl)."""
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=self._next(card), column=0, sticky="ew", padx=10, pady=(6, 8))
        for i, it in enumerate(items):
            row.grid_columnconfigure(i, weight=1, uniform="t")
            hl = it.get("hl")
            t = ctk.CTkFrame(row, fg_color=C["tile_hl"] if hl else C["panel2"], corner_radius=12,
                             border_width=2 if hl else 0, border_color=C["accent"])
            t.grid(row=0, column=i, padx=4, sticky="nsew")
            ctk.CTkLabel(t, text=it["title"], font=(FONT, 11, "bold"), text_color=C["muted"]).pack(padx=6, pady=(8, 0))
            ctk.CTkLabel(t, text=it["value"], font=(FONT, big, "bold"),
                         text_color=it.get("color") or C["text"]).pack(padx=6)
            ctk.CTkLabel(t, text=it.get("sub") or " ", font=(FONT, 11), text_color=C["muted"]).pack(padx=6, pady=(0, 8))

    def _badge(self, parent, text, color, width=28):
        return ctk.CTkLabel(parent, text=text, width=width, height=22, corner_radius=6, fg_color=color,
                            text_color="#FFFFFF", font=(FONT, 11, "bold"))

    def _analysis_message(self, text):
        for f in self.sub.values():
            self._clear(f)
        ctk.CTkLabel(self.sub["Esito e gol"], text=text, font=(FONT, 15), text_color=C["muted"]).grid(
            row=0, column=0, columnspan=3, pady=40)
        self.subtabs.set("Esito e gol")

    # ---------------- schede dell'analisi ----------------
    def _render_main(self, r, pb, home, away):
        f = self.sub["Esito e gol"]
        self._clear(f)
        best = max(pb, key=pb.get)

        c = self._card(f, "Esito finale", 0, 0, colspan=2,
                       subtitle="Stima combinata modello + quote dei bookmaker" if r.get("blend")
                       else "Probabilità del modello · sotto ogni valore la quota equa")
        self._tiles(c, [dict(title=f"1 · {home[:20]}", value=pct(pb["1"]).strip(), color=C["home"],
                             sub=f"quota equa {fair(pb['1']).strip()}", hl=best == "1"),
                        dict(title="X · Pareggio", value=pct(pb["X"]).strip(), color=C["draw"],
                             sub=f"quota equa {fair(pb['X']).strip()}", hl=best == "X"),
                        dict(title=f"2 · {away[:20]}", value=pct(pb["2"]).strip(), color=C["away"],
                             sub=f"quota equa {fair(pb['2']).strip()}", hl=best == "2")], big=28)
        dc = r["DoppiaChance"]
        bdc = max(dc, key=dc.get)
        self._tiles(c, [dict(title=f"Doppia chance {k}", value=pct(v).strip(), sub=f"quota equa {fair(v).strip()}",
                             hl=k == bdc) for k, v in dc.items()], big=18)
        if r.get("blend"):
            self._note(c, "Solo modello:  " + "   ".join(f"{k} {pct(r['1X2'][k]).strip()}" for k in "1X2"))

        c = self._card(f, "Gol", 0, 2)
        lh, la = r["xg"]
        self._tiles(c, [dict(title="Gol attesi casa", value=f"{lh:.2f}", color=C["home"]),
                        dict(title="Gol attesi ospite", value=f"{la:.2f}", color=C["away"])], big=22)
        gg = r["GoalNoGoal"]
        self._tiles(c, [dict(title="Goal", value=pct(gg["Goal"]).strip(), sub=f"quota equa {fair(gg['Goal']).strip()}",
                             hl=gg["Goal"] >= gg["NoGoal"]),
                        dict(title="No Goal", value=pct(gg["NoGoal"]).strip(), sub=f"quota equa {fair(gg['NoGoal']).strip()}",
                             hl=gg["NoGoal"] > gg["Goal"])], big=18)

        c = self._card(f, "Risultati esatti più probabili", 1, 0)
        mx = r["exact_scores"][0][1]
        for (h, a), q in r["exact_scores"]:
            self._bar(c, f"{h} - {a}", q, scale=mx, color=OUT_COL["1" if h > a else "X" if h == a else "2"])
        self._note(c, "Colore della barra: blu vittoria casa, grigio pareggio, arancio vittoria ospite.")

        c = self._card(f, "Under / Over", 1, 1, subtitle="blu = Under · verde = Over")
        for l, v in r["UnderOver"].items():
            self._uo(c, l, v["Under"], v["Over"])
        self._heading(c, "Somma gol esatta")
        ms = max(r["SommaGol"].values())
        for k, v in r["SommaGol"].items():
            self._bar(c, f"{k} gol", v, scale=ms)

        c = self._card(f, "Multigol", 1, 2)
        for k, v in r["Multigol"].items():
            self._bar(c, f"MG {k}", v)
        self._heading(c, f"Casa · {home[:22]}")
        for k, v in r["MultigolHome"].items():
            self._bar(c, f"MG {k}", v, color=C["home"])
        self._heading(c, f"Ospite · {away[:22]}")
        for k, v in r["MultigolAway"].items():
            self._bar(c, f"MG {k}", v, color=C["away"])

        c = self._card(f, "Combo", 2, 0)
        for k, v in r["Combo"].items():
            self._bar(c, k, v, right="")

        c = self._card(f, "Confronto con le quote", 2, 1, colspan=2)
        mk = r.get("market")
        if mk and mk.get("rows"):
            tab = ctk.CTkFrame(c, fg_color="transparent")
            tab.grid(row=self._next(c), column=0, sticky="ew", padx=14, pady=4)
            heads = ("Esito", "Quota", "Bookmaker", "Modello", "Combinata", "")
            for j, hd in enumerate(heads):
                tab.grid_columnconfigure(j, weight=1)
                ctk.CTkLabel(tab, text=hd, font=(FONT, 11, "bold"), text_color=C["muted"]).grid(row=0, column=j, pady=(0, 4))
            for i, x in enumerate(mk["rows"], 1):
                vals = (x["market"], f"{x['odds']:.2f}", f"{x['p_market'] * 100:.0f}%", f"{x['p_model'] * 100:.0f}%",
                        f"{x['p_blend'] * 100:.0f}%")
                for j, v in enumerate(vals):
                    ctk.CTkLabel(tab, text=v, font=(FONT, 13, "bold" if j in (0, 4) else "normal"),
                                 text_color=C["text"]).grid(row=i, column=j, pady=2)
                if x["value"]:
                    self._badge(tab, f"VALORE +{x['ev'] * 100:.0f}%", C["gold"], width=100).grid(row=i, column=5, pady=2)
            txt = (f"Combinata = {mk['w'] * 100:.0f}% modello + {(1 - mk['w']) * 100:.0f}% quote. "
                   "«Valore» = la quota paga più di quanto la stima ritiene giusto (vantaggio atteso ≥ 3%).")
            if mk.get("margin") is not None:
                txt += f" Margine del bookmaker: {mk['margin'] * 100:.1f}%."
            self._note(c, txt)
        else:
            self._note(c, "Quote dei bookmaker non disponibili per questa partita: di solito compaiono pochi giorni "
                          "prima del calcio d'inizio.")

    def _render_halftime(self, r, home, away):
        f = self.sub["Primo tempo"]
        self._clear(f)
        pt = r.get("PrimoTempo")
        if not pt:
            ctk.CTkLabel(f, text="Dati del primo tempo non disponibili per questo campionato.", font=(FONT, 14),
                         text_color=C["muted"]).grid(row=0, column=0, columnspan=3, pady=40)
            return

        def tiles_1x2(card, d):
            b = max(d, key=d.get)
            self._tiles(card, [dict(title=t, value=pct(d[k]).strip(), color=OUT_COL[k], hl=k == b,
                                    sub=f"quota equa {fair(d[k]).strip()}")
                               for k, t in (("1", "1"), ("X", "X"), ("2", "2"))], big=22)

        c = self._card(f, "Esito 1° tempo", 0, 0)
        tiles_1x2(c, pt["1X2"])
        c = self._card(f, "Esito 2° tempo", 0, 1, subtitle="conta solo i gol segnati nel secondo tempo")
        tiles_1x2(c, pt["1X2_ST"])
        c = self._card(f, "Gol nel 1° tempo", 0, 2)
        lh1, la1 = pt["xg_pt"]
        self._tiles(c, [dict(title=home[:16], value=f"{lh1:.2f}", color=C["home"], sub="gol attesi"),
                        dict(title=away[:16], value=f"{la1:.2f}", color=C["away"], sub="gol attesi")], big=20)

        c = self._card(f, "Parziale / finale", 1, 0, subtitle="esito al 45' / esito finale")
        items = sorted(pt["PTFT"].items(), key=lambda kv: -kv[1])
        mx = items[0][1]
        for k, v in items:
            self._bar(c, k, v, scale=mx, color=OUT_COL[k[-1]])
        c = self._card(f, "Under / Over per tempo", 1, 1, subtitle="blu = Under · verde = Over")
        self._heading(c, "1° tempo")
        for l, v in pt["UO_PT"].items():
            self._uo(c, l, v["Under"], v["Over"])
        self._heading(c, "2° tempo")
        for l, v in pt["UO_ST"].items():
            self._uo(c, l, v["Under"], v["Over"])
        c = self._card(f, "Tempo con più gol", 1, 2)
        for k, v in pt["TempoPiuGol"].items():
            self._bar(c, k, v)
        self._heading(c, "Almeno un gol")
        self._bar(c, "nel 1° tempo", pt["Gol1T"])
        self._bar(c, "nel 2° tempo", pt["Gol2T"])

    def _render_stats(self, r, home, away):
        f = self.sub["Angoli · Cartellini · Tiri"]
        self._clear(f)
        st = r.get("Stats") or {}
        titles = {"Angoli": "Calci d'angolo", "Cartellini": "Cartellini (gialli + rossi)", "TiriPorta": "Tiri in porta"}
        for i, name in enumerate(("Angoli", "Cartellini", "TiriPorta")):
            s = st.get(name)
            c = self._card(f, titles[name], 0, i)
            if not s:
                self._note(c, "Dati non disponibili per questo campionato o per questa fonte: servono le statistiche "
                              "partita di football-data.co.uk.")
                continue
            mh, ma = s["exp"]
            self._tiles(c, [dict(title="Casa", value=f"{mh:.1f}", color=C["home"]),
                            dict(title="Ospite", value=f"{ma:.1f}", color=C["away"]),
                            dict(title="Totale", value=f"{mh + ma:.1f}")], big=20)
            self._heading(c, "Totale partita")
            for l, v in s["tot"].items():
                self._uo(c, l, v["Under"], v["Over"])
            self._heading(c, home[:26])
            for l, v in s["home"].items():
                self._uo(c, l, v["Under"], v["Over"])
            self._heading(c, away[:26])
            for l, v in s["away"].items():
                self._uo(c, l, v["Under"], v["Over"])
            self._heading(c, "Chi ne fa di più")
            d = s["1X2"]
            b = max(d, key=d.get)
            self._tiles(c, [dict(title=k, value=pct(d[k]).strip(), color=OUT_COL[k], hl=k == b) for k in "1X2"], big=15)
            self._note(c, f"Stima su {s['n']} partite" + ("; i cartellini dipendono molto dall'arbitro." if name == "Cartellini" else "."))

    def _render_form(self, home, away):
        f = self.sub["Forma e precedenti"]
        self._clear(f)
        ld = self.league_data() or {}
        res_col = {"V": C["accent"], "N": C["draw"], "P": C["red"]}
        for i, team in enumerate((home, away)):
            fm = team_form(ld, team, 6)
            c = self._card(f, f"Forma · {team[:24]}", 0, i, subtitle="ultime partite, la più recente in alto")
            if not fm["rows"]:
                self._note(c, "Nessuna partita nei dati.")
                continue
            for x in fm["rows"]:
                row = ctk.CTkFrame(c, fg_color="transparent")
                row.grid(row=self._next(c), column=0, sticky="ew", padx=14, pady=2)
                row.grid_columnconfigure(2, weight=1)
                ctk.CTkLabel(row, text=fmt_date(x["date"]), width=78, anchor="w", font=(FONT, 11),
                             text_color=C["muted"]).grid(row=0, column=0)
                self._badge(row, x["side"], C["home"] if x["side"] == "C" else C["header2"], width=22).grid(row=0, column=1, padx=4)
                ctk.CTkLabel(row, text=x["opp"][:22], anchor="w", font=(FONT, 12),
                             text_color=C["text"]).grid(row=0, column=2, sticky="ew", padx=4)
                ctk.CTkLabel(row, text=f"{x['gf']}-{x['gs']}", width=36, font=(FONT, 12, "bold"),
                             text_color=C["text"]).grid(row=0, column=3)
                self._badge(row, x["res"], res_col[x["res"]]).grid(row=0, column=4, padx=(4, 0))
            k = len(fm["rows"])
            self._tiles(c, [dict(title="Punti", value=f"{fm['pts']}/{3 * k}"),
                            dict(title="Gol", value=f"{fm['gf']}-{fm['gs']}"),
                            dict(title="Over 2.5", value=f"{fm['over']}/{k}"),
                            dict(title="Goal", value=f"{fm['gg']}/{k}")], big=15)
            self._note(c, "C = in casa, T = in trasferta · V vinta, N pari, P persa")

        h2h = head_to_head(ld, home, away, 8)
        c = self._card(f, "Precedenti", 0, 2, subtitle="stagione in corso e precedente")
        if not h2h:
            self._note(c, "Nessun precedente nei dati.")
        for m in h2h:
            row = ctk.CTkFrame(c, fg_color="transparent")
            row.grid(row=self._next(c), column=0, sticky="ew", padx=14, pady=2)
            row.grid_columnconfigure(1, weight=1)
            row.grid_columnconfigure(3, weight=1)
            ctk.CTkLabel(row, text=f"{date.fromisoformat(m['date']):%d/%m/%y}", width=64, anchor="w", font=(FONT, 11),
                         text_color=C["muted"]).grid(row=0, column=0)
            ctk.CTkLabel(row, text=m["home"][:16], anchor="e", font=(FONT, 12, "bold" if m["hg"] > m["ag"] else "normal"),
                         text_color=C["text"]).grid(row=0, column=1, sticky="ew")
            win = "1" if m["hg"] > m["ag"] else ("X" if m["hg"] == m["ag"] else "2")
            self._badge(row, f"{m['hg']}-{m['ag']}", OUT_COL[win], width=44).grid(row=0, column=2, padx=6)
            ctk.CTkLabel(row, text=m["away"][:16], anchor="w", font=(FONT, 12, "bold" if m["ag"] > m["hg"] else "normal"),
                         text_color=C["text"]).grid(row=0, column=3, sticky="ew")
        if h2h:
            n = len(h2h)
            wh = sum((m["home"] == home and m["hg"] > m["ag"]) or (m["away"] == home and m["ag"] > m["hg"]) for m in h2h)
            wa = sum((m["home"] == away and m["hg"] > m["ag"]) or (m["away"] == away and m["ag"] > m["hg"]) for m in h2h)
            self._tiles(c, [dict(title=home[:12], value=str(wh), color=C["home"], sub="vittorie"),
                            dict(title="Pari", value=str(n - wh - wa), color=C["draw"]),
                            dict(title=away[:12], value=str(wa), color=C["away"], sub="vittorie")], big=18)
            self._note(c, f"Over 2.5 in {sum(m['hg'] + m['ag'] > 2.5 for m in h2h)} su {n} · "
                          f"Goal in {sum(m['hg'] > 0 and m['ag'] > 0 for m in h2h)} su {n}")
        self._note(c, "Forma e precedenti sono indicativi: il modello li considera già, dando più peso alle "
                      "partite recenti.")

    def _find_fixture_odds(self, home, away):
        ld = self.league_data() or {}
        for f in ld.get("fixtures", []):
            if f["home"] == home and f["away"] == away:
                return f.get("odds")
        return None

    # ---------------- classifica ----------------
    def render_table(self):
        ld, m = self.league_data(), self.model()
        if not ld:
            self._write(self.table_box, "Nessun dato.")
            return
        rows = standings(ld)
        rat = {x["team"]: x for x in (m.team_ratings() if m else [])}
        t = (f"CLASSIFICA {self.current_league.upper()} {ld['season']}   (calcolata dai risultati; eventuali "
             "penalizzazioni non incluse)\n\n")
        t += (f"{'#':>3}  {'Squadra':<26}{'PG':>4}{'V':>4}{'N':>4}{'P':>4}{'GF':>5}{'GS':>5}{'DR':>5}{'Pt':>5}   "
              f"{'Forma':<7}{'Att.':>7}{'Dif.':>7}\n")
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

    # ---------------- storico ----------------
    def render_history(self):
        h = history.load(updater.data_dir())
        lg = self.current_league
        s_l, s_all = history.summary(h, lg), history.summary(h)

        def block(title, s):
            if not s.get("n"):
                return (f"{title}: nessuna partita ancora conclusa"
                        f"{' (' + str(s['pending']) + ' pronostici in attesa del risultato)' if s.get('pending') else ''}.\n")
            t = (f"{title}: {s['n']} partite concluse, {s['pending']} in attesa\n   1X2 indovinato "
                 f"{s['acc_1x2'] * 100:.0f}%  ·  Over/Under 2.5 {s['acc_over'] * 100:.0f}%  ·  Goal/No Goal "
                 f"{s['acc_gg'] * 100:.0f}%  ·  risultato esatto {s['acc_score'] * 100:.0f}%  ·  RPS {s['rps']:.3f}\n")
            if s.get("n_sure"):
                t += f"   Pronostici con almeno il 60%: {s['n_sure']}, indovinati {s['acc_sure'] * 100:.0f}%\n"
            v = s.get("value")
            if v:
                t += (f"   Segnalazioni «valore»: {v['n']}, vinte {v['won']} (quota media {v['avg_odds']:.2f}); "
                      f"puntando 1 € su ciascuna il saldo sarebbe {v['profit']:+.2f} € (rendimento {v['roi'] * 100:+.0f}%)\n")
            return t

        t = block(lg, s_l) + "\n" + block("Tutti i campionati", s_all)
        t += ("\nI pronostici vengono salvati prima della partita a ogni aggiornamento (anche quelli automatici) e "
              "confrontati con il risultato appena disponibile. È la misura più onesta della qualità del modello; "
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
                out = "1" if rr["hg"] > rr["ag"] else ("X" if rr["hg"] == rr["ag"] else "2")
                esito = "✓" if out == k else "✗"
                if v:
                    esito += "  valore " + ("✓" if history.value_won(v, rr) else "✗")
            else:
                res, esito = "—", "in attesa"
            match = f"{e['home']} – {e['away']}"[:38]
            t += f"{fmt_date(e['date']):<12}{match:<40}{k:>6}{pct(p[k]):>7}{e['score']:>11}{vs:>14}{res:>11}  {esito}\n"
        self._write(self.hist_box, t)

    # ---------------- affidabilità ----------------
    def render_reliability(self):
        rel = (self.data or {}).get("reliability", {}).get(self.current_league)
        t = f"Quanto è affidabile il modello in {self.current_league}?\n\n"
        if not rel or not rel.get("n"):
            t += ("Non ci sono ancora abbastanza partite recenti per misurarlo. Il calcolo viene rifatto a ogni "
                  "aggiornamento dei dati.\n")
        else:
            t += ("Ho simulato il modello sulle ultime 10 settimane: ogni settimana l'ho ri-stimato usando solo i dati "
                  "disponibili fino a quel giorno e ho confrontato le previsioni con i "
                  f"{rel['n']} risultati reali.\n\n")
            t += f"•  Esito 1X2 più probabile indovinato: {rel['acc_1x2'] * 100:.0f}% delle partite\n"
            if rel.get("acc_conf") is not None:
                t += (f"•  Quando il modello dà almeno il 55% a un esito ({rel['n_conf']} partite): indovinato il "
                      f"{rel['acc_conf'] * 100:.0f}%\n")
            t += f"•  Over/Under 2.5 indovinato: {rel['acc_over25'] * 100:.0f}%\n"
            t += f"•  Goal/No Goal indovinato: {rel['acc_gg'] * 100:.0f}%\n"
            t += f"•  Punteggio di errore RPS: {rel['rps']:.3f} (più basso è meglio; tirare a caso darebbe circa 0.24)\n"
            mk = rel.get("market")
            if mk:
                better = "meglio" if mk["rps_model_same"] < mk["rps"] else "peggio"
                t += (f"\nConfronto con i bookmaker sulle stesse {mk['n']} partite: le quote medie avrebbero indovinato il "
                      f"{mk['acc_1x2'] * 100:.0f}% (RPS {mk['rps']:.3f}); il modello ha fatto {better} (RPS "
                      f"{mk['rps_model_same']:.3f}).\n")
                if mk.get("rps_blend") is not None:
                    t += (f"La stima combinata ({mk['w'] * 100:.0f}% modello + {(1 - mk['w']) * 100:.0f}% quote) avrebbe "
                          f"indovinato il {mk['acc_blend'] * 100:.0f}% (RPS {mk['rps_blend']:.3f}).\n")
        bl = (self.data or {}).get("blend") or {}
        if bl:
            if bl.get("n", 0) >= 150 and "rps_blend" in bl:
                t += (f"\nPeso del modello nella stima combinata: {bl['w'] * 100:.0f}%, scelto automaticamente su "
                      f"{bl['n']} partite recenti di tutti i campionati con quote (RPS: solo modello {bl['rps_model']:.3f}, "
                      f"solo quote {bl['rps_market']:.3f}, combinata {bl['rps_blend']:.3f}).\n")
            else:
                t += (f"\nPeso del modello nella stima combinata: {bl.get('w', DEFAULT_BLEND) * 100:.0f}% (valore di "
                      "partenza; verrà calibrato automaticamente appena ci saranno abbastanza partite con quote).\n")
        xg = (self.data or {}).get("xg") or {}
        if xg.get("leagues"):
            rps = xg.get("rps") or {}
            t += (f"\nxG stimati dai tiri: peso attuale {float(xg.get('beta', 0)) * 100:.0f}% (0% = solo gol). Ricalcolato "
                  "a ogni aggiornamento confrontando sulle ultime 10 settimane: "
                  + ", ".join(f"{float(k) * 100:.0f}% → RPS {v:.4f}" for k, v in rps.items()) + ".\n")
            if self.current_league not in xg["leagues"]:
                t += "In questo campionato i tiri non sono disponibili: si usano solo i gol.\n"
        t += ("\nCome leggere questi numeri\nNel calcio anche i modelli migliori indovinano l'esito 1X2 circa una volta su "
              "due: pareggi e sorprese sono frequenti. Le percentuali dell'app sono probabilità, non certezze; servono a "
              "capire quanto un esito è plausibile e a confrontarlo con le quote. Le quote dei bookmaker includono un "
              "margine e di solito sono molto precise: battere il mercato in modo costante è raro.\n\nCosa il modello non "
              "vede: infortuni, squalifiche, turnover, cambi di allenatore, motivazioni di classifica, meteo. Tienine conto "
              "prima di decidere.\n\nIl gioco è vietato ai minori e può causare dipendenza.")
        self._write(self.rel_box, t)

    # ---------------- esportazione ----------------
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

    # ---------------- impostazioni ----------------
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
            ctk.CTkLabel(w, text=title, font=(FONT, 15, "bold")).grid(row=row, column=0, pady=(14, 0), **pad)
            ctk.CTkLabel(w, text=text, text_color=C["muted"], font=(FONT, 12), justify="left",
                         wraplength=600).grid(row=row + 1, column=0, **pad)

        section(0, "Risultati in giornata (football-data.org)",
                "Chiave gratuita: registrati su football-data.org/client/register e incolla qui la chiave ricevuta per "
                "e-mail. Lascia vuoto per non usarla.")
        e_key = ctk.CTkEntry(w, width=420, placeholder_text="chiave API (32 caratteri)")
        e_key.insert(0, cfg.get("fdorg_key", ""))
        e_key.grid(row=2, column=0, pady=6, **pad)

        section(3, "GitHub: aggiornamento del programma e versione per telefono",
                "Nome del repository, nella forma utente/nome (vedi GUIDA_GITHUB.txt). Il programma controllerà da solo "
                "le nuove versioni pubblicate lì.")
        e_repo = ctk.CTkEntry(w, width=420, placeholder_text="es. mariorossi/football-predictor")
        e_repo.insert(0, cfg.get("github_repo", ""))
        e_repo.grid(row=5, column=0, pady=6, **pad)
        url_lbl = ctk.CTkLabel(w, text="", text_color=C["blue"], font=(FONT, 12, "underline"), cursor="hand2")
        url_lbl.grid(row=6, column=0, **pad)

        def refresh_url(*_):
            u = self.phone_url(e_repo.get())
            url_lbl.configure(text=f"Versione per telefono: {u}" if u else "")

        refresh_url()
        e_repo.bind("<KeyRelease>", refresh_url)
        url_lbl.bind("<Button-1>", lambda _: self.phone_url(e_repo.get())
                     and __import__("webbrowser").open(self.phone_url(e_repo.get())))

        ctk.CTkLabel(w, text="Notifiche e aggiornamenti", font=(FONT, 15, "bold")).grid(row=7, column=0,
                                                                                              pady=(16, 4), **pad)
        v_val = ctk.BooleanVar(value=cfg.get("notify_value", True))
        v_upd = ctk.BooleanVar(value=cfg.get("notify_update", False))
        v_app = ctk.BooleanVar(value=cfg.get("auto_app_update", True))
        ctk.CTkSwitch(w, text="Notifica di Windows per nuove segnalazioni di valore", variable=v_val).grid(
            row=8, column=0, pady=3, **pad)
        ctk.CTkSwitch(w, text="Notifica di Windows a ogni aggiornamento dei dati", variable=v_upd).grid(
            row=9, column=0, pady=3, **pad)
        ctk.CTkSwitch(w, text="Controlla da solo le nuove versioni del programma", variable=v_app).grid(
            row=10, column=0, pady=3, **pad)

        trow = ctk.CTkFrame(w, fg_color="transparent")
        trow.grid(row=14, column=0, pady=(6, 0), **pad)
        ctk.CTkLabel(trow, text="Aspetto:", font=(FONT, 13, "bold")).pack(side="left", padx=(0, 10))
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
            self.status.configure(text="Impostazioni salvate. La chiave football-data.org verrà usata dal prossimo "
                                       "aggiornamento.")
            w.destroy()

        ctk.CTkButton(w, text="SALVA", width=160, height=36, fg_color=C["accent"], hover_color=C["accent_h"],
                      command=save_close).grid(row=15, column=0, pady=16, padx=18, sticky="e")

    def _save_settings(self, cfg, e_key, e_repo, v_val, v_upd, v_app):
        cfg.update(fdorg_key=e_key.get().strip(), github_repo=e_repo.get().strip(),
                   notify_value=bool(v_val.get()), notify_update=bool(v_upd.get()), auto_app_update=bool(v_app.get()))
        settings.save(updater.data_dir(), cfg)

    # ---------------- aggiornamento del programma ----------------
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
                if (datetime.now() - last).total_seconds() < 43200:
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

    # ---------------- aggiornamento dati ----------------
    def _auto_update_check(self):
        if not self.updating and updater.needs_update(self.data):
            self.start_update(auto=True)
        elif not self.updating:
            self.update_status()
        self.after(1_200_000, self._auto_update_check)

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
                                           "(restano i dati precedenti).")
                return
            self.btn_update.configure(state="normal", text="AGGIORNA DATI", fg_color=C["accent"])
            return
        self.btn_update.configure(state="normal", text="ERRORE · RIPROVA", fg_color=C["red"])
        self.status.configure(text="Aggiornamento non riuscito: controlla la connessione internet. Restano i dati "
                                   "precedenti. Riproverò automaticamente.")

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
