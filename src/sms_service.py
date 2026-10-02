"""
SMS-Versand über das SIM7600 4G HAT (AT-Kommandos über seriellen Port).

Alle Verbindungsparameter (Port, Baudrate, PIN, Zielnummer) kommen
ausschließlich aus config.py — keine fest codierten Werte in den Funktionen.
"""

import time
from datetime import datetime

import serial

import config
from logging_service import journal


def an_kommando_senden(ser: serial.Serial, kommando: str, wartezeit: float = 1) -> str:
    ser.write((kommando + "\r\n").encode())
    time.sleep(wartezeit)
    antwort = ser.read(ser.inWaiting()).decode(errors="ignore")
    print(f"  [SMS] >> {kommando}")
    print(f"  [SMS] << {antwort.strip()}")
    return antwort


def sms_senden(port: str, nummer: str, nachricht: str) -> bool:
    try:
        with serial.Serial(port, config.SMS_BAUDRATE, timeout=5) as ser:
            time.sleep(1)
            an_kommando_senden(ser, "AT")
            pin_antwort = an_kommando_senden(ser, "AT+CPIN?")
            if "SIM PIN" in pin_antwort:
                journal("SMS", "PIN erforderlich — Entsperrung läuft…")
                an_kommando_senden(ser, f'AT+CPIN="{config.SMS_PIN}"', wartezeit=3)
                journal("SMS", "PIN eingegeben — warte SIM-Initialisierung…")
                time.sleep(3)
            an_kommando_senden(ser, "AT+CSQ")
            an_kommando_senden(ser, "AT+CREG?")
            an_kommando_senden(ser, "AT+CMGF=1")
            an_kommando_senden(ser, f'AT+CMGS="{nummer}"', wartezeit=2)
            ser.write((nachricht + chr(26)).encode())
            time.sleep(5)
            antwort = ser.read(ser.inWaiting()).decode(errors="ignore")
            print(f"  [SMS] << {antwort.strip()}")
            if "+CMGS:" in antwort:
                journal("SMS", "SMS erfolgreich gesendet!")
                return True
            else:
                journal("FEHLER", "SMS Versand fehlgeschlagen")
                return False
    except Exception as e:
        journal("FEHLER", f"SMS: {e}")
        return False


def _sms_erstellen(ergebnis: dict, modus: str, inferenz_ms: float = 0.0) -> str:
    """
    Formatiert das Aufnahmeergebnis als kompakte SMS (max. 160 Zeichen, GSM 7-Bit).

    Beispiele:
      Kein Tier:
        WK Pi5 | 01.05 18:23 | Foto
        Kein Tier erkannt
        Inferenz: 312ms

      Tier erkannt:
        WK Pi5 | 01.05 18:23 | Foto
        Hirsch 87%, Rehwild 74%
        2 Tier(e) | Inferenz: 312ms
    """
    jetzt = datetime.now().strftime("%d.%m %H:%M")
    modus_text = "Foto" if modus == "photo" else "Video"
    kopfzeile = f"WK Pi5 | {jetzt} | {modus_text}"

    if not ergebnis.get("saved"):
        return f"{kopfzeile}\nKein Tier erkannt\nInferenz: {inferenz_ms:.0f}ms"[:160]

    detections = ergebnis.get("detections", [])
    anzahl = len(detections)

    # Arten sortiert nach Konfidenz (höchste zuerst)
    detections_sortiert = sorted(
        detections, key=lambda d: d.get("confiance", 0), reverse=True
    )

    # Kompakte Artenliste — kürzen falls zu lang
    arten_teile = [
        f"{d.get('classe','?')} {d.get('confiance', 0):.0f}%" for d in detections_sortiert
    ]
    arten_str = ", ".join(arten_teile)

    # Sicherheitskürzung falls viele Arten (max 60 Zeichen für Artenliste)
    if len(arten_str) > 60:
        arten_str = arten_str[:57] + "..."

    zeile2 = arten_str
    zeile3 = f"{anzahl} Tier(e) | Inferenz: {inferenz_ms:.0f}ms"

    nachricht = f"{kopfzeile}\n{zeile2}\n{zeile3}"
    return nachricht[:160]


def sms_im_hintergrund(ergebnis: dict, modus: str, inferenz_ms: float = 0.0) -> None:
    """Formatiert und sendet die SMS (wird von pir_service.py in einem Daemon-Thread aufgerufen)."""
    try:
        nachricht = _sms_erstellen(ergebnis, modus, inferenz_ms)
        journal("SMS", f"Versand → {config.SMS_NUMMER}\n{nachricht}")
        sms_senden(config.SMS_PORT, config.SMS_NUMMER, nachricht)
    except Exception as e:
        journal("FEHLER", f"SMS-Thread: {e}")
