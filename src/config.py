"""
Zentrale Konfiguration — Wildkamera Raspberry Pi 5
====================================================
Alle Konstanten des Projekts an einem Ort. Geheimnisse (Azure-Verbindungs-
zeichenfolgen, Schlüssel, SMS-Nummer, SIM-PIN) werden NICHT hier fest codiert,
sondern aus der Datei ".env" (im selben Verzeichnis) geladen — siehe
".env.example" für die erwartete Struktur.

Läuft ausschließlich auf dem Raspberry Pi 5.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ── .env laden ─────────────────────────────────────────────────────────────
# Expliziter Pfad relativ zu dieser Datei — unabhängig vom Arbeitsverzeichnis
# des aufrufenden Prozesses (wichtig bei Start über systemd/cron @reboot).
_ENV_DATEI = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_DATEI)


def _env_pflicht(name: str) -> str:
    """Liest eine Pflicht-Umgebungsvariable; bricht mit klarer Fehlermeldung ab, falls sie fehlt."""
    wert = os.environ.get(name)
    if not wert:
        raise RuntimeError(
            f"Pflicht-Umgebungsvariable '{name}' fehlt — bitte in '{_ENV_DATEI}' setzen "
            f"(siehe .env.example)."
        )
    return wert


def _env_optional(name: str, standard: str = "") -> str:
    """Liest eine optionale Umgebungsvariable mit Standardwert."""
    return os.environ.get(name, standard)


# ── Azure-Geheimnisse (aus .env) ──────────────────────────────────────────
IOT_HUB_CONN_STR = _env_pflicht("IOT_HUB_CONN_STR")
STORAGE_CONN_STR = _env_pflicht("STORAGE_CONN_STR")
AZURE_FUNC_KEY = _env_optional("AZURE_FUNC_KEY")
SMS_NUMMER = _env_pflicht("SMS_NUMMER")
SMS_PIN = _env_optional("SMS_PIN")

# ── Azure IoT Hub / Blob Storage — feste Konfiguration ────────────────────
CONTAINER_NAME = "captures"
GERAETE_ID = "raspberrypi5"

# ── Azure Function URL (für Einstellungs-Sync vom Pi) ─────────────────────
# Format: "https://<deine-app>.azurewebsites.net/api"
AZURE_FUNC_URL = (
    "https://camerapi5func-cshwc9e7fyaxhcgc.westeurope-01.azurewebsites.net/api"
)

# ── Kamera / Modell / PIR Konfiguration ────────────────────────────────────
PIR_GPIO = 18  # BCM GPIO 18 — physischer Pin 12
PRAEFERENZEN_DATEI = Path("/home/rick351/script/preferences.json")
MODELL_PFAD = Path("/home/rick351/script/best5_ncnn_model")

# ── SMS via SIM7600 4G HAT ──────────────────────────────────────────────────
SMS_PORT = "/dev/ttyUSB3"  # AT-Port des SIM7600
SMS_BAUDRATE = 115200

# ── Verzeichnisse (Offline-First Architektur) ──────────────────────────────
DCIM_VERZEICHNIS = Path("/home/rick351/DCIM")  # Alle aufgenommenen Fotos/Videos
TRANSMIT_VERZEICHNIS = Path("/home/rick351/TRANSMIT")  # Warteschlange für Azure-Upload
LOG_DATEI = Path("/home/rick351/script/wildkamera.log")  # Ereignis-Log

# Verzeichnisse beim Start erstellen
DCIM_VERZEICHNIS.mkdir(parents=True, exist_ok=True)
TRANSMIT_VERZEICHNIS.mkdir(parents=True, exist_ok=True)
LOG_DATEI.parent.mkdir(parents=True, exist_ok=True)

# Foto
KAMERA_BREITE = 3280  # native IMX219-Auflösung
KAMERA_HOEHE = 2464
JPEG_QUALITAET = 95

# Video
VIDEO_BREITE = 1280  # guter Kompromiss Geschwindigkeit/Qualität auf Pi 5
VIDEO_HOEHE = 720
VIDEO_FPS = 10  # realistisches FPS mit Inferenz (~8-12 FPS auf Pi 5)

# Inferenz
KONFIDENZ_SCHWELLE = 0.50
BILDGROESSE = 640

# Persistenz der Bounding-Boxes im Video:
# Boxen bleiben N Sekunden nach der letzten Erkennung sichtbar,
# mit progressiv abnehmender Deckkraft bis zum Verschwinden.
BBOX_PERSISTENZ_SEK = 3.0

# Farben pro Klasse (BGR)
FARBEN = {
    "Hirsch": (0, 200, 50),
    "Rebock": (0, 165, 255),
    "Rehwild": (255, 100, 0),
    "Wildschwein": (0, 50, 200),
}

ALLE_KLASSEN = list(FARBEN.keys())

# ── Upload-Warteschlange ────────────────────────────────────────────────────
UPLOAD_INTERVALL_SEK = 60  # Prüfintervall des Upload-Queue-Threads

# ── IoT Hub Wiederverbindung (exponentielles Backoff) ──────────────────────
IOT_HUB_WIEDERVERBINDUNGS_VERZOEGERUNGEN = [5, 10, 20, 30, 60, 60, 120, 300]
