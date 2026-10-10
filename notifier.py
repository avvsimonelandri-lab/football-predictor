"""Notifiche di Windows (quelle che compaiono in basso a destra), senza librerie aggiuntive."""
from __future__ import annotations

import os
import subprocess

_PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [System.Security.SecurityElement]::Escape($env:FPP_TITLE)
$m = [System.Security.SecurityElement]::Escape($env:FPP_MSG)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$m</text></binding></visual></toast>")
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def available() -> bool:
    return os.name == "nt"


def notify(title: str, message: str) -> bool:
    if not available():
        print(f"[notifica] {title}: {message}")
        return False
    try:
        env = dict(os.environ, FPP_TITLE=title[:120], FPP_MSG=message[:400])
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                          "-ExecutionPolicy", "Bypass", "-Command", _PS],
                         env=env, creationflags=0x08000000)  # CREATE_NO_WINDOW
        return True
    except Exception as e:
        print(f"[notifica] errore: {e}")
        return False


def notify_new_values(h: dict, enabled: bool) -> int:
    """Notifica le segnalazioni di valore non ancora notificate (una sola notifica riassuntiva)."""
    new = [e for e in h.get("entries", {}).values() if e.get("value") and not e.get("result") and not e.get("notified")]
    if not new:
        return 0
    for e in new:
        e["notified"] = True
    if enabled:
        new.sort(key=lambda e: (e["date"], e.get("time") or ""))
        lines = [f"{e['home']}-{e['away']}: {e['value']['market']} @{e['value']['odds']:.2f}" for e in new[:4]]
        more = f" (+{len(new) - 4} altre)" if len(new) > 4 else ""
        notify(f"Football Predictor: {len(new)} nuove segnalazioni di valore", "; ".join(lines) + more)
    return len(new)
