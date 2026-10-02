"""
PIR-Bewegungsmelder-Schleife — löst bei Bewegung eine Aufnahme aus, unter
Berücksichtigung von Cooldown und dem gemeinsamen Kamera-Lock
(camera_service.kamera_sperre), das auch von den Azure IoT Hub Direct
Methods verwendet wird.
"""

import threading
import time

from gpiozero import MotionSensor
from ultralytics import YOLO

import camera_service
import config
import preferences_service
import sms_service
from logging_service import journal


def pir_schleife(model: YOLO) -> None:
    """
    Läuft in einem Daemon-Thread.

    Ablauf bei jeder Bewegung:
      1. Cooldown prüfen — falls aktiv, ignorieren und Ende der Bewegung abwarten.
      2. Kamera-Sperre BLOCKIEREND anfordern: wartet bis jede laufende
         Aufnahme (Foto oder Video) vollständig abgeschlossen ist.
      3. Aufnahme mit aktuellen Einstellungen durchführen.
      4. Sperre freigeben, Cooldown-Zeitstempel aktualisieren.
      5. Ende der physischen Bewegung abwarten, bevor neu gelauscht wird.

    Der PIR kann daher niemals eine Aufnahmeserie auslösen: nur eine Aufnahme
    gleichzeitig, und der Cooldown läuft nach dem echten Ende der Aufnahme ab.
    """
    try:
        pir = MotionSensor(config.PIR_GPIO)
    except Exception as e:
        print(f"  [PIR] FEHLER bei GPIO-Initialisierung {config.PIR_GPIO}: {e}")
        return

    print(f"  [PIR] Sensor aktiv auf GPIO {config.PIR_GPIO} — lauscht…")
    t_letzte_aufnahme = 0.0

    while True:
        pir.wait_for_motion()  # blockiert bis zur nächsten Bewegung

        jetzt = time.time()
        praeferenzen = preferences_service.praeferenzen_abrufen()

        cooldown = praeferenzen.get("cooldown", 30)
        verstrichen = jetzt - t_letzte_aufnahme

        if verstrichen < cooldown:
            rest = cooldown - verstrichen
            journal(
                "PIR",
                f"Bewegung ignoriert — Cooldown aktiv ({rest:.0f}s verbleibend)",
            )
            pir.wait_for_no_motion()  # Ende der Bewegung abwarten, dann neu starten
            continue

        # ── BLOCKIERENDE Sperre: warten bis Kamera vollständig frei ist ────
        # Falls eine manuelle Aufnahme (Direct Method) läuft, wird hier gewartet
        # ohne unnötige CPU-Last.
        journal("PIR", "Bewegung erkannt — warte auf Kameraverfügbarkeit…")
        camera_service.kamera_sperre.acquire(blocking=True)

        try:
            t_letzte_aufnahme = time.time()  # Zeitstempel NACH Sperrerwerb
            modus = praeferenzen.get("mode", "photo")
            klassen = praeferenzen.get("classes", config.ALLE_KLASSEN)
            dauer = praeferenzen.get("duree", 15)

            journal(
                "PIR",
                f"Aufnahme gestartet | Modus={modus}  Dauer={dauer}s  "
                f"Klassen={klassen}",
            )

            if modus == "photo":
                ergebnis, datei, inferenz_ms = camera_service.foto_aufnahme(model, klassen)
            else:
                ergebnis, datei, inferenz_ms = camera_service.video_aufnahme(
                    model, klassen, dauer
                )

            journal(
                "PIR", f"Aufnahme abgeschlossen | gespeichert={ergebnis.get('saved')}"
            )

            # ── SMS-Benachrichtigung ────────────────────────────────────────
            threading.Thread(
                target=sms_service.sms_im_hintergrund,
                args=(ergebnis, modus, inferenz_ms),
                daemon=True,
            ).start()

        except Exception as e:
            journal("FEHLER", f"PIR-Aufnahme: {e}")
        finally:
            camera_service.kamera_sperre.release()  # Kamera freigeben

        pir.wait_for_no_motion()  # Ende der Bewegung abwarten bevor neu gelauscht wird
