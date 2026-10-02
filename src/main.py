#!/usr/bin/env python3
"""
Raspberry Pi 5 – Azure IoT Hub Wildkamera-Client
=================================================
Der Pi lauscht auf IoT Hub Direct Methods.
Unterstützte Methoden: "capture", "set_preferences"

Eingehender Payload:
    {"classes": ["Hirsch","Rehwild"], "mode": "photo", "duree": 15}

Antwort:
    {"saved": true,  "blob_name": "20260421_143012_Hirsch_87pct.jpg",
     "detections": [{"classe": "Hirsch", "confiance": 87.1, "bbox": [x1,y1,x2,y2]}]}
  oder
    {"saved": false, "message": "Kein Tier erkannt"}

Kamera    : IMX219 (Pi Camera v2) – picamera2
Inferenz  : YOLOv26 NCNN (best(5)_ncnn_model)
Speicher  : Azure Blob Storage, Container "captures"

Läuft ausschließlich auf dem Raspberry Pi 5.

Start (aus diesem Verzeichnis heraus, wichtig für die relativen Imports und
für das automatische Auffinden der ".env"-Datei):
    cd pi5_wildkamera
    python3 main.py

Abhängigkeiten:
    sudo apt install -y python3-picamera2
    pip install -r requirements.txt --break-system-packages
"""

import time
import threading

import config
import detection_service
import preferences_service
from azure_blob_uploader import upload_warteschlangen_thread
from azure_iot_service import IoTHubConnectionManager, direct_method_handler_erstellen
from logging_service import journal
from pir_service import pir_schleife


def main() -> None:
    print("=" * 60)
    print("  Wildkamera – Raspberry Pi 5 + Azure IoT Hub")
    print("=" * 60)
    print(f"  Geräte-ID  : {config.GERAETE_ID}")
    print(f"  Modell     : {config.MODELL_PFAD.name}")
    print(f"  Klassen    : {' | '.join(config.ALLE_KLASSEN)}")
    print(f"  Konfid.    : {config.KONFIDENZ_SCHWELLE * 100:.0f}%")
    print(f"  Foto       : {config.KAMERA_BREITE}×{config.KAMERA_HOEHE} px  JPEG {config.JPEG_QUALITAET}%")
    print(f"  Video      : {config.VIDEO_BREITE}×{config.VIDEO_HOEHE} px  {config.VIDEO_FPS} FPS")
    print(f"  DCIM       : {config.DCIM_VERZEICHNIS}/")
    print(f"  TRANSMIT   : {config.TRANSMIT_VERZEICHNIS}/")
    print(f"  Log        : {config.LOG_DATEI}")
    print(f"  Azure Func : {config.AZURE_FUNC_URL or '(nicht konfiguriert)'}")
    print("=" * 60)
    print()

    journal("INFO", "Wildkamera gestartet")

    # ── Lokale Einstellungen laden ────────────────────────────────────────
    preferences_service.praeferenzen_laden()

    # ── NCNN-Modell laden ─────────────────────────────────────────────────
    journal("INFO", f"Lade NCNN-Modell: {config.MODELL_PFAD.name}…")
    try:
        model = detection_service.modell_laden(config.MODELL_PFAD)
    except FileNotFoundError as e:
        journal("FEHLER", str(e))
        raise SystemExit(1)

    # Warmup: vermeidet Latenz beim ersten echten Aufruf
    detection_service.modell_aufwaermen(model)
    journal("INFO", "Modell geladen und aufgewärmt ✓")

    # ── Daemon-Threads starten ────────────────────────────────────────────
    # Reihenfolge: Upload-Queue → PIR → IoT Hub (optional)
    # Alle Threads sind Daemon → enden automatisch wenn main() endet.

    threading.Thread(
        target=upload_warteschlangen_thread,
        name="UploadQueue",
        daemon=True,
    ).start()
    journal("INFO", "Upload-Queue-Thread gestartet")

    threading.Thread(
        target=pir_schleife,
        args=(model,),
        name="PIR",
        daemon=True,
    ).start()
    journal("INFO", "PIR-Thread gestartet")

    # ── IoT Hub Verbindung (kapselt ihre eigene Fehlertoleranz) ────────────
    iot_manager = IoTHubConnectionManager(
        connection_string=config.IOT_HUB_CONN_STR,
        method_handler=direct_method_handler_erstellen(model),
    )
    iot_manager.start()

    print()
    print("  Pi läuft — PIR aktiv, Upload-Queue aktiv.")
    print("  IoT Hub: Verbindung im Hintergrund (Pi bleibt bei Ausfall aktiv).")
    print("  Ctrl+C zum sauberen Beenden.")
    print("=" * 60)

    # ── Hauptthread: nur auf Ctrl+C warten ─────────────────────────────────
    # Alle echten Arbeiten laufen in Daemon-Threads.
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        journal("INFO", "Beenden durch Benutzer (Ctrl+C)")
    finally:
        iot_manager.stop()
        journal("INFO", "Wildkamera beendet.")


if __name__ == "__main__":
    main()
