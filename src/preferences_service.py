"""
Einstellungsverwaltung — thread-sicherer Zugriff auf die Aufnahme-Präferenzen
(Modus, Dauer, aktive Klassen, Cooldown), lokale Persistenz in
preferences.json und Synchronisierung von Azure.

Kein anderes Modul darf auf den internen Zustand zugreifen — ausschließlich
über praeferenzen_abrufen() / praeferenzen_aktualisieren().
"""

import json
import threading

import config
from logging_service import journal

_praeferenzen: dict = {
    "mode": "photo",
    "duree": 15,
    "classes": config.ALLE_KLASSEN,
    "cooldown": 30,  # Mindestsekunden zwischen zwei PIR-Aufnahmen
}
_praeferenzen_sperre = threading.Lock()


def praeferenzen_abrufen() -> dict:
    """Gibt eine thread-sichere Kopie der aktuellen Einstellungen zurück."""
    with _praeferenzen_sperre:
        return dict(_praeferenzen)


def praeferenzen_aktualisieren(neue_werte: dict) -> dict:
    """Merged neue_werte thread-sicher in die Einstellungen und gibt die aktualisierte Kopie zurück."""
    with _praeferenzen_sperre:
        _praeferenzen.update(neue_werte)
        return dict(_praeferenzen)


def praeferenzen_laden() -> None:
    """Lädt preferences.json beim Start (falls vorhanden)."""
    if config.PRAEFERENZEN_DATEI.exists():
        try:
            with open(config.PRAEFERENZEN_DATEI) as f:
                geladen = json.load(f)
            with _praeferenzen_sperre:
                _praeferenzen.update(geladen)
            print(f"  Einstellungen geladen: {_praeferenzen}")
        except Exception as e:
            print(f"  WARNUNG: Einstellungen konnten nicht geladen werden: {e}")
    else:
        print("  Keine Einstellungsdatei — Standardwerte werden verwendet.")


def praeferenzen_speichern(prefs: dict) -> None:
    """Speichert die Einstellungen in preferences.json."""
    with open(config.PRAEFERENZEN_DATEI, "w") as f:
        json.dump(prefs, f, indent=2, ensure_ascii=False)


def praeferenzen_von_azure_synchronisieren() -> bool:
    """
    Lädt die aktuellen Einstellungen vom Azure Blob Storage (via Azure Function).
    Wird am Anfang jedes Upload-Zyklus aufgerufen, wenn 4G verfügbar ist.
    Bei Fehler: lokale Einstellungen bleiben unverändert.
    Gibt True zurück wenn Einstellungen erfolgreich synchronisiert wurden.
    """
    import urllib.request

    if not config.AZURE_FUNC_URL:
        return False  # URL nicht konfiguriert

    url = f"{config.AZURE_FUNC_URL}/preferences"
    if config.AZURE_FUNC_KEY:
        url += f"?code={config.AZURE_FUNC_KEY}"

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            if resp.status == 204:
                # Noch keine Einstellungen in Azure gespeichert
                return False
            if resp.status != 200:
                journal(
                    "INFO", f"Einstellungen-Download: HTTP {resp.status} — ignoriert"
                )
                return False

            data = resp.read().decode("utf-8")
            prefs = json.loads(data)

        # Einstellungen übernehmen (nur gültige Felder)
        neu: dict = {}
        if "mode" in prefs and prefs["mode"] in ("photo", "video"):
            neu["mode"] = prefs["mode"]
        if "duree" in prefs and prefs["duree"] in (15, 30, 60):
            neu["duree"] = int(prefs["duree"])
        if "classes" in prefs:
            gueltig = [c for c in prefs["classes"] if c in config.ALLE_KLASSEN]
            if gueltig:
                neu["classes"] = gueltig
        if "cooldown" in prefs:
            try:
                cd = int(prefs["cooldown"])
                if 5 <= cd <= 3600:
                    neu["cooldown"] = cd
            except (ValueError, TypeError):
                pass

        if neu:
            praeferenzen_aktualisieren(neu)
            praeferenzen_speichern(praeferenzen_abrufen())
            journal("INFO", f"Einstellungen von Azure synchronisiert: {neu}")
            return True

    except Exception as e:
        journal("INFO", f"Einstellungen-Download fehlgeschlagen (kein Problem): {e}")
    return False
