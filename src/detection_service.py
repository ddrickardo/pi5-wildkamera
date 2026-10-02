"""
Erkennung & Annotation — Laden des YOLO/NCNN-Modells, Filtern der Erkennungen
nach aktiven Klassen und Zeichnen der Bounding-Boxes auf Fotos/Video-Frames.

Keine Kamera- oder GPIO-Abhängigkeit — reine Bildverarbeitung.
"""

from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

import config


def modell_laden(pfad: Path = config.MODELL_PFAD) -> YOLO:
    """Lädt das NCNN-Modell. Wirft FileNotFoundError falls der Pfad nicht existiert."""
    if not pfad.exists():
        raise FileNotFoundError(f"Modell nicht gefunden → {pfad}")
    return YOLO(str(pfad), task="detect")


def modell_aufwaermen(model: YOLO) -> None:
    """Führt eine Dummy-Inferenz aus, um Latenz beim ersten echten Aufruf zu vermeiden."""
    dummy = np.zeros((config.VIDEO_HOEHE, config.VIDEO_BREITE, 3), dtype=np.uint8)
    model.predict(
        source=dummy, conf=config.KONFIDENZ_SCHWELLE, imgsz=config.BILDGROESSE, verbose=False
    )


def boxen_filtern(results, aktive_klassen: list) -> list:
    """
    Gibt nur die Boxen zurück, deren Klasse in aktive_klassen enthalten ist.
    Format: Liste von (x1, y1, x2, y2, conf_float, name_str)
    """
    out = []
    for box in results[0].boxes:
        name = results[0].names[int(box.cls[0])]
        if name in aktive_klassen:
            out.append(
                (
                    *map(int, box.xyxy[0]),
                    float(box.conf[0]),
                    name,
                )
            )
    return out


def _boxen_zeichnen(img: np.ndarray, boxen_daten: list, alpha: float = 1.0) -> None:
    """
    Zeichnet eine Liste von Boxen auf img (in-place, BGR).
    boxen_daten : Liste von (x1, y1, x2, y2, conf, name)
    alpha       : Deckkraft 0.0 → 1.0 (Ausblenden bei Persistenz)
    """
    h, w = img.shape[:2]
    overlay = img.copy()

    for x1, y1, x2, y2, conf, name in boxen_daten:
        farbe_basis = config.FARBEN.get(name, (255, 255, 255))
        farbe = tuple(int(c * alpha) for c in farbe_basis)

        ep = max(2, w // 800)
        cv2.rectangle(overlay, (x1, y1), (x2, y2), farbe, ep)

        label = f"{name}  {conf * 100:.0f}%"
        font = cv2.FONT_HERSHEY_SIMPLEX
        skala = max(0.6, w / 2000)
        ep_txt = max(1, w // 1200)

        (tw, th), bl = cv2.getTextSize(label, font, skala, ep_txt)
        pad = 5
        # Label-Hintergrund
        cv2.rectangle(
            overlay, (x1, y1 - th - bl - pad * 2), (x1 + tw + pad, y1), farbe, -1
        )
        txt_farbe = (0, 0, 0) if alpha > 0.4 else (180, 180, 180)
        cv2.putText(
            overlay,
            label,
            (x1 + pad // 2, y1 - bl - pad),
            font,
            skala,
            txt_farbe,
            ep_txt,
            cv2.LINE_AA,
        )

    # Überblendung mit Alpha für den Ausblendeffekt
    cv2.addWeighted(overlay, alpha, img, 1.0 - alpha, 0, img)


def foto_annotieren(img_rgb: np.ndarray, boxen_daten: list) -> np.ndarray:
    """Annotation für Fotos: volle Boxen + Zeitstempel. Gibt BGR zurück."""
    img = cv2.cvtColor(img_rgb.copy(), cv2.COLOR_RGB2BGR)
    h, w = img.shape[:2]
    _boxen_zeichnen(img, boxen_daten, alpha=1.0)
    ts = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    cv2.putText(
        img,
        ts,
        (10, h - 10),
        cv2.FONT_HERSHEY_SIMPLEX,
        max(0.5, w / 3000),
        (210, 210, 210),
        max(1, w // 1800),
        cv2.LINE_AA,
    )
    return img


def frame_annotieren(img_rgb: np.ndarray, boxen_daten: list, alpha: float) -> np.ndarray:
    """
    Annotation für ein Video-Frame mit Box-Persistenz.
    alpha: 1.0 = frische Erkennung, 0.0 = Boxen abgelaufen
    Gibt BGR zurück.
    """
    img = cv2.cvtColor(img_rgb.copy(), cv2.COLOR_RGB2BGR)
    h, w = img.shape[:2]
    if boxen_daten and alpha > 0.0:
        _boxen_zeichnen(img, boxen_daten, alpha=alpha)
    ts = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    cv2.putText(
        img,
        ts,
        (10, h - 10),
        cv2.FONT_HERSHEY_SIMPLEX,
        max(0.5, w / 3000),
        (210, 210, 210),
        max(1, w // 1800),
        cv2.LINE_AA,
    )
    return img
