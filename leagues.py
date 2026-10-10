"""Configurazione dei campionati supportati.

fd  = codice divisione su football-data.co.uk (fonte principale: risultati, calendario e quote)
of  = codice openfootball (fonte di riserva, su GitHub). None = nessuna riserva disponibile.
"""

LEAGUES = {
    "Serie A":            {"fd": "I1",  "of": "it.1", "country": "Italia"},
    "Serie B":            {"fd": "I2",  "of": None,   "country": "Italia"},
    "Premier League":     {"fd": "E0",  "of": "en.1", "country": "Inghilterra"},
    "Championship":       {"fd": "E1",  "of": "en.2", "country": "Inghilterra"},
    "LaLiga":             {"fd": "SP1", "of": "es.1", "country": "Spagna"},
    "LaLiga 2":           {"fd": "SP2", "of": None,   "country": "Spagna"},
    "Bundesliga":         {"fd": "D1",  "of": "de.1", "country": "Germania"},
    "2. Bundesliga":      {"fd": "D2",  "of": "de.2", "country": "Germania"},
    "Ligue 1":            {"fd": "F1",  "of": "fr.1", "country": "Francia"},
    "Ligue 2":            {"fd": "F2",  "of": None,   "country": "Francia"},
    "Eredivisie":         {"fd": "N1",  "of": "nl.1", "country": "Olanda"},
    "Primeira Liga":      {"fd": "P1",  "of": "pt.1", "country": "Portogallo"},
    "Jupiler Pro League": {"fd": "B1",  "of": None,   "country": "Belgio"},
    "Süper Lig":          {"fd": "T1",  "of": None,   "country": "Turchia"},
    "Scottish Premiership": {"fd": "SC0", "of": None, "country": "Scozia"},
}

DEFAULT_LEAGUE = "Serie A"
