"""Ereignis-Log — schreibt in die Log-Datei und auf die Konsole."""

from datetime import datetime

import config


def journal(niveau: str, nachricht: str) -> None:
    """
    Schreibt ein Ereignis in wildkamera.log und auf die Konsole.
    niveau : "PIR" | "FOTO" | "VIDEO" | "UPLOAD" | "SMS" | "FEHLER" | "INFO"
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    zeile = f"{ts} | {niveau:<8} | {nachricht}\n"
    try:
        with open(config.LOG_DATEI, "a", encoding="utf-8") as f:
            f.write(zeile)
    except Exception as e:
        print(f"  [LOG] Schreibfehler: {e}")
    print(zeile.rstrip())
