"""
Azure Blob Storage Upload — Verbindungsprüfung (4G), Upload-Warteschlange
(TRANSMIT/ → Azure) und Blob-Upload mit wiederverwendetem BlobServiceClient.

Der BlobServiceClient/ContainerClient wird nur beim ersten Upload erstellt
(Singleton) statt bei jedem Aufruf neu — spart Verbindungsaufbau und
create_container()-Aufrufe bei jedem einzelnen Upload.
"""

import subprocess
import threading
import time
from pathlib import Path

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContainerClient

import config
import preferences_service
from logging_service import journal

# SIM7600 4G HAT via USB — typisches Linux-Interface ist "usb0"
# Falls abweichend: "ip link show" auf dem Pi ausführen und anpassen
_IFACES_4G = ["usb0", "usb1", "wwan0", "ppp0"]

_client_sperre = threading.Lock()
_blob_service_client: BlobServiceClient | None = None
_container_client: ContainerClient | None = None
_container_bereit = False  # True sobald der Container nachweislich existiert


def verbindung_verfuegbar() -> bool:
    """
    Prüft ob die 4G-Verbindung des SIM7600 verfügbar ist.

    Strategie (Reihenfolge):
      1. Bekannte 4G-Interfaces direkt anpingen (usb0, wwan0, ppp0)
         → stellt sicher, dass WIRKLICH die 4G-Verbindung geprüft wird,
           nicht WiFi oder Ethernet.
      2. Fallback: Ping ohne Interface-Bindung (falls kein Interface bekannt)
    """
    for iface in _IFACES_4G:
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "3", "-I", iface, "8.8.8.8"],
                capture_output=True,
                timeout=8,
            )
            if result.returncode == 0:
                return True
        except Exception:
            continue

    # Fallback: kein Interface angegeben (nimmt Standard-Route)
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "3", "8.8.8.8"],
            capture_output=True,
            timeout=8,
        )
        return result.returncode == 0
    except Exception:
        return False


def _container_client_holen() -> ContainerClient:
    """
    Erstellt BlobServiceClient/ContainerClient nur beim ersten Aufruf
    (Singleton) und versucht create_container() nur solange der Container
    noch nicht bestätigt vorhanden ist.
    """
    global _blob_service_client, _container_client, _container_bereit

    with _client_sperre:
        if _blob_service_client is None:
            _blob_service_client = BlobServiceClient.from_connection_string(
                config.STORAGE_CONN_STR
            )
            _container_client = _blob_service_client.get_container_client(
                config.CONTAINER_NAME
            )

        if not _container_bereit:
            try:
                _container_client.create_container()
                journal("UPLOAD", f"Container '{config.CONTAINER_NAME}' erstellt.")
                _container_bereit = True
            except ResourceExistsError:
                _container_bereit = True  # bereits vorhanden — auch ein Erfolg
            except Exception as e:
                journal("FEHLER", f"Container-Erstellung fehlgeschlagen: {e}")
                # _container_bereit bleibt False → nächster Aufruf versucht es erneut

        return _container_client


def blob_hochladen(lokaler_pfad: Path, blob_name: str) -> None:
    """Lädt eine lokale Datei in den Azure Blob Storage Container hoch."""
    container = _container_client_holen()
    with open(lokaler_pfad, "rb") as data:
        container.upload_blob(name=blob_name, data=data, overwrite=True)
    journal("UPLOAD", f"Blob hochgeladen → {blob_name}")


def upload_warteschlangen_thread() -> None:
    """
    Daemon-Thread: überwacht TRANSMIT/ und sendet alle Dateien nach Azure,
    sobald die 4G-Verbindung verfügbar ist.

    Ablauf pro Zyklus (alle UPLOAD_INTERVALL_SEK Sekunden):
      1. 4G-Verbindung prüfen
      2. Einstellungen von Azure herunterladen (Sync)
      3. Dateien aus TRANSMIT/ hochladen (älteste zuerst)
      4. Nur nach erfolgreichem Upload aus TRANSMIT/ löschen
    """
    journal(
        "INFO",
        f"Upload-Queue-Thread gestartet — prüft alle {config.UPLOAD_INTERVALL_SEK}s",
    )

    while True:
        try:
            if not verbindung_verfuegbar():
                ausstehend = list(config.TRANSMIT_VERZEICHNIS.iterdir())
                anzahl = sum(1 for f in ausstehend if f.is_file())
                if anzahl:
                    journal(
                        "UPLOAD",
                        f"Keine Verbindung — {anzahl} Datei(en) warten in TRANSMIT/",
                    )
                time.sleep(config.UPLOAD_INTERVALL_SEK)
                continue

            # ── Schritt 1: Einstellungen synchronisieren ──────────────────
            preferences_service.praeferenzen_von_azure_synchronisieren()

            # ── Schritt 2: Dateien hochladen ───────────────────────────────
            ausstehend = sorted(
                [f for f in config.TRANSMIT_VERZEICHNIS.iterdir() if f.is_file()],
                key=lambda f: f.stat().st_mtime,  # älteste zuerst
            )

            if ausstehend:
                journal(
                    "UPLOAD",
                    f"Verbindung OK — {len(ausstehend)} Datei(en) werden hochgeladen",
                )
                for datei in ausstehend:
                    try:
                        blob_hochladen(datei, datei.name)
                        datei.unlink()  # Erst nach erfolgreichem Upload löschen
                        journal(
                            "UPLOAD",
                            f"OK → {datei.name} hochgeladen und aus TRANSMIT entfernt",
                        )
                    except Exception as e:
                        journal("FEHLER", f"Upload fehlgeschlagen ({datei.name}): {e}")
                        break  # Abbrechen — nächste Iteration in UPLOAD_INTERVALL_SEK

        except Exception as e:
            journal("FEHLER", f"Upload-Queue-Thread: {e}")

        time.sleep(config.UPLOAD_INTERVALL_SEK)
