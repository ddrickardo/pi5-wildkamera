"""
Kamera-Dienst — Picamera2-Initialisierung sowie die vollständigen Foto- und
Video-Aufnahmepipelines (Aufnahme → Inferenz → Annotation → Speicherung).

Exportiert KAMERA_SPERRE: das einzige Lock-Objekt, das den gleichzeitigen
Kamerazugriff zwischen dem PIR-Thread (pir_service.py) und den Azure IoT Hub
Direct Methods (azure_iot_service.py) verhindert. Beide Module importieren
exakt dieses Objekt.
"""

import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2

from libcamera import controls as libcam_controls
from picamera2 import Picamera2
from ultralytics import YOLO

import config
import detection_service
from logging_service import journal

# Globale Sperre: nur eine Kameraoperation gleichzeitig
kamera_sperre = threading.Lock()


def kamera_initialisieren(modus: str) -> Picamera2:
    """
    modus='photo' → Still-Konfiguration 3280×2464 (volle IMX219-Auflösung)
    modus='video' → Video-Konfiguration 1280×720 @ 10 FPS
    """
    cam = Picamera2()

    if modus == "photo":
        konfiguration = cam.create_still_configuration(
            main={"size": (config.KAMERA_BREITE, config.KAMERA_HOEHE), "format": "RGB888"},
            controls={
                "AwbMode": libcam_controls.AwbModeEnum.Auto,
                "AeEnable": True,
            },
        )
    else:
        konfiguration = cam.create_video_configuration(
            main={"size": (config.VIDEO_BREITE, config.VIDEO_HOEHE), "format": "RGB888"},
            controls={
                "FrameDurationLimits": (
                    1_000_000 // config.VIDEO_FPS,
                    1_000_000 // config.VIDEO_FPS,
                ),
                "AwbMode": libcam_controls.AwbModeEnum.Auto,
                "AeEnable": True,
            },
            buffer_count=2,
        )

    cam.configure(konfiguration)
    cam.start()
    time.sleep(1.5)  # AE/AWB stabilisieren lassen
    return cam


def foto_aufnahme(model: YOLO, aktive_klassen: list) -> tuple:
    """
    1. Nimmt ein hochauflösendes Foto auf (IMX219 nativ 3280×2464).
    2. Startet die NCNN-Inferenz (Zeit wird gemessen).
    3. Filtert Erkennungen nach aktive_klassen.
    4. Speichert in DCIM/ und kopiert in TRANSMIT/ (Upload-Warteschlange).
    5. Gibt (ergebnis_dict, datei_path_oder_None, inferenz_ms) zurück.
    """
    zeitstempel = datetime.now().strftime("%Y%m%d_%H%M%S")
    datum_zeit = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    cam = kamera_initialisieren("photo")
    try:
        print("  Fotoaufnahme…", end=" ", flush=True)
        t0 = time.perf_counter()
        frame = cam.capture_array()
        aufnahme_ms = (time.perf_counter() - t0) * 1000
        print(f"{aufnahme_ms:.0f} ms", end="   ", flush=True)
    finally:
        cam.stop()
        cam.close()

    print("Inferenz…", end=" ", flush=True)
    t1 = time.perf_counter()
    results = model.predict(
        source=frame, conf=config.KONFIDENZ_SCHWELLE, imgsz=config.BILDGROESSE, verbose=False
    )
    inferenz_ms = (time.perf_counter() - t1) * 1000
    print(f"{inferenz_ms:.0f} ms")

    boxen = detection_service.boxen_filtern(results, aktive_klassen)

    if not boxen:
        journal(
            "FOTO", f"Kein Tier erkannt | Inferenz: {inferenz_ms:.0f}ms | {datum_zeit}"
        )
        return (
            {
                "saved": False,
                "message": "Kein Tier erkannt",
                "inferenz_ms": inferenz_ms,
            },
            None,
            inferenz_ms,
        )

    # Beste Erkennung → Dateiname
    beste = max(boxen, key=lambda b: b[4])
    dominante_klasse = beste[5]
    dominante_konfidenz_int = int(beste[4] * 100)
    blob_name = f"{zeitstempel}_{dominante_klasse}_{dominante_konfidenz_int}pct.jpg"

    # ── 1. In DCIM/ speichern ─────────────────────────────────────────────
    dcim_pfad = config.DCIM_VERZEICHNIS / blob_name
    img_bgr = detection_service.foto_annotieren(frame, boxen)
    cv2.imwrite(str(dcim_pfad), img_bgr, [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITAET])
    groesse_kb = dcim_pfad.stat().st_size // 1024
    print(f"  DCIM gespeichert → {blob_name}  ({groesse_kb} KB)")

    # ── 2. In TRANSMIT/ kopieren (Upload-Warteschlange) ─────────────────────
    transmit_pfad = config.TRANSMIT_VERZEICHNIS / blob_name
    shutil.copy2(str(dcim_pfad), str(transmit_pfad))
    print(f"  TRANSMIT Kopie → {blob_name}")

    detections = [
        {
            "classe": b[5],
            "confiance": round(b[4] * 100, 1),
            "bbox": [b[0], b[1], b[2], b[3]],
        }
        for b in boxen
    ]

    # Arten-Zusammenfassung für Log
    arten_str = ", ".join(f"{d['classe']} {d['confiance']:.0f}%" for d in detections)
    journal(
        "FOTO",
        f"Tier erkannt | {arten_str} | Anzahl: {len(detections)} | "
        f"Inferenz: {inferenz_ms:.0f}ms | Datei: {blob_name} | {datum_zeit}",
    )

    return (
        {
            "saved": True,
            "blob_name": blob_name,
            "detections": detections,
            "inferenz_ms": inferenz_ms,
        },
        dcim_pfad,
        inferenz_ms,
    )


def video_aufnahme(model: YOLO, aktive_klassen: list, dauer: int) -> tuple:
    """
    1. Nimmt ein annotiertes Video von `dauer` Sekunden auf.
    2. Startet NCNN-Inferenz auf jedem Frame (Zeit wird gemessen).
    3. Bounding-Boxes bleiben BBOX_PERSISTENZ_SEK Sekunden nach der letzten
       Erkennung sichtbar (abnehmende Deckkraft).
    4. Speichert in DCIM/ und kopiert in TRANSMIT/ (Upload-Warteschlange).
    5. Gibt (ergebnis_dict, datei_path_oder_None, inferenz_ms_gesamt) zurück.
    """
    zeitstempel = datetime.now().strftime("%Y%m%d_%H%M%S")
    datum_zeit = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    temp_datei = config.DCIM_VERZEICHNIS / f"_tmp_{zeitstempel}.mp4"

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    video_schreiber = cv2.VideoWriter(
        str(temp_datei), fourcc, config.VIDEO_FPS, (config.VIDEO_BREITE, config.VIDEO_HOEHE)
    )

    erkennung_gefunden = False
    letzte_boxen = []
    t_letzte_erkennung = -999.0
    dominante_klasse = "animal"
    dominante_konfidenz = 0.0
    start_zeit = time.time()
    inferenz_summe_ms = 0.0
    frame_anzahl = 0

    print(f"  Aufnahme {dauer}s (Box-Persistenz: {config.BBOX_PERSISTENZ_SEK:.0f}s)…")

    cam = kamera_initialisieren("video")
    try:
        while time.time() - start_zeit < dauer:
            frame = cam.capture_array()

            t_inf = time.perf_counter()
            results = model.predict(
                source=frame, conf=config.KONFIDENZ_SCHWELLE, imgsz=config.BILDGROESSE, verbose=False
            )
            inferenz_summe_ms += (time.perf_counter() - t_inf) * 1000
            frame_anzahl += 1

            boxen = detection_service.boxen_filtern(results, aktive_klassen)
            jetzt = time.time()

            if boxen:
                erkennung_gefunden = True
                t_letzte_erkennung = jetzt
                letzte_boxen = boxen
                beste = max(boxen, key=lambda b: b[4])
                dominante_klasse = beste[5]
                dominante_konfidenz = beste[4]
                alpha = 1.0
                print(
                    f"\n    [{jetzt - start_zeit:5.1f}s]  {dominante_klasse}  "
                    f"{dominante_konfidenz*100:.0f}%",
                    flush=True,
                )
            else:
                alter = jetzt - t_letzte_erkennung
                alpha = max(0.0, 1.0 - alter / config.BBOX_PERSISTENZ_SEK)

            img_bgr = detection_service.frame_annotieren(frame, letzte_boxen, alpha)

            # Sicherheitscheck: bei Bedarf skalieren
            if img_bgr.shape[1] != config.VIDEO_BREITE or img_bgr.shape[0] != config.VIDEO_HOEHE:
                img_bgr = cv2.resize(img_bgr, (config.VIDEO_BREITE, config.VIDEO_HOEHE))

            video_schreiber.write(img_bgr)

            verstrichen = jetzt - start_zeit
            print(f"\r  {verstrichen:4.1f}/{dauer}s", end="", flush=True)

    finally:
        cam.stop()
        cam.close()
        video_schreiber.release()

    print()  # Zeilenumbruch nach Fortschrittsanzeige

    # Mittlere Inferenzzeit pro Frame
    inferenz_ms = inferenz_summe_ms / max(frame_anzahl, 1)

    if not erkennung_gefunden:
        temp_datei.unlink(missing_ok=True)
        journal(
            "VIDEO",
            f"Kein Tier erkannt | Frames: {frame_anzahl} | "
            f"Ø Inferenz: {inferenz_ms:.0f}ms/Frame | {datum_zeit}",
        )
        return (
            {
                "saved": False,
                "message": "Kein Tier erkannt",
                "inferenz_ms": inferenz_ms,
            },
            None,
            inferenz_ms,
        )

    # ── 1. In DCIM/ umbenennen ────────────────────────────────────────────
    dominante_konfidenz_int = int(dominante_konfidenz * 100)
    blob_name = f"{zeitstempel}_{dominante_klasse}_{dominante_konfidenz_int}pct_video_{dauer}s.mp4"
    dcim_pfad = config.DCIM_VERZEICHNIS / blob_name
    temp_datei.rename(dcim_pfad)
    datei_groesse_mb = dcim_pfad.stat().st_size / (1024 * 1024)
    print(f"  DCIM gespeichert → {dcim_pfad.name}  ({datei_groesse_mb:.1f} MB)")

    # ── 2. In TRANSMIT/ kopieren (Upload-Warteschlange) ─────────────────────
    transmit_pfad = config.TRANSMIT_VERZEICHNIS / blob_name
    shutil.copy2(str(dcim_pfad), str(transmit_pfad))
    print(f"  TRANSMIT Kopie → {blob_name}")

    # Bei Videos ist die Erkennungsliste kein Einzelbild-Referenz
    detections = [
        {
            "classe": dominante_klasse,
            "confiance": round(dominante_konfidenz * 100, 1),
            "bbox": [],  # keine feste Bbox für Videos
        }
    ]

    journal(
        "VIDEO",
        f"Tier erkannt | {dominante_klasse} {dominante_konfidenz_int}% | "
        f"Frames: {frame_anzahl} | Ø Inferenz: {inferenz_ms:.0f}ms/Frame | "
        f"Datei: {blob_name} | {datum_zeit}",
    )

    return (
        {
            "saved": True,
            "blob_name": blob_name,
            "detections": detections,
            "inferenz_ms": inferenz_ms,
        },
        dcim_pfad,
        inferenz_ms,
    )
