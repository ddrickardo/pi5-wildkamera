"""
Azure IoT Hub — Direct-Method-Handler ("capture", "set_preferences") und
IoTHubConnectionManager: kapselt Verbindungsaufbau, exponentielles Backoff
bei Wiederverbindung und den Verbindungsstatus in einer einzigen Klasse.

Der Pi-Betrieb (PIR, Foto/Video, SMS, Upload-Queue) wird durch die IoT-Hub-
Verbindung niemals blockiert: läuft vollständig in einem eigenen Daemon-Thread.
"""

import json
import threading
import time
from typing import Callable

from azure.iot.device import IoTHubDeviceClient, MethodRequest, MethodResponse

import camera_service
import config
import preferences_service
from logging_service import journal


def direct_method_handler_erstellen(model) -> Callable[[MethodRequest], MethodResponse]:
    """
    Gibt den Direct-Method-Handler mit Zugriff auf das geladene YOLO-Modell zurück.
    """

    def method_handler(method_request: MethodRequest) -> MethodResponse:
        print(f"\n[DirectMethod] Methode empfangen: '{method_request.name}'")

        # ── Methode "capture" ─────────────────────────────────────────────
        if method_request.name == "capture":
            # Prüfen ob die Kamera bereits belegt ist
            if not camera_service.kamera_sperre.acquire(blocking=False):
                payload = {"error": "Kamera belegt, bitte später erneut versuchen"}
                return MethodResponse.create_from_method_request(
                    method_request, status=503, payload=payload
                )

            try:
                # Eingehenden JSON-Payload parsen
                try:
                    data = (
                        json.loads(method_request.payload)
                        if isinstance(method_request.payload, str)
                        else method_request.payload
                    )
                except (json.JSONDecodeError, TypeError) as e:
                    payload = {"error": f"Ungültiger JSON-Payload: {e}"}
                    return MethodResponse.create_from_method_request(
                        method_request, status=400, payload=payload
                    )

                modus = data.get("mode", "photo")
                dauer = int(data.get("duree", 15))
                klassen = data.get("classes", config.ALLE_KLASSEN)

                # Modus validieren
                if modus not in ("photo", "video"):
                    modus = "photo"

                # Videodauer validieren
                if dauer not in (15, 30, 60):
                    dauer = 15

                # Ungültige Klassen herausfiltern
                klassen = [c for c in klassen if c in config.ALLE_KLASSEN]
                if not klassen:
                    payload = {"error": "Keine gültige Klasse in der Anfrage"}
                    return MethodResponse.create_from_method_request(
                        method_request, status=400, payload=payload
                    )

                print(f"  modus={modus}  dauer={dauer}s  klassen={klassen}")

                # Kameraaufgabe ausführen
                if modus == "photo":
                    ergebnis, datei, inferenz_ms = camera_service.foto_aufnahme(model, klassen)
                else:
                    ergebnis, datei, inferenz_ms = camera_service.video_aufnahme(
                        model, klassen, dauer
                    )

                return MethodResponse.create_from_method_request(
                    method_request, status=200, payload=ergebnis
                )

            except Exception as e:
                print(f"  FEHLER während der Aufnahme: {e}")
                payload = {"error": str(e)}
                return MethodResponse.create_from_method_request(
                    method_request, status=500, payload=payload
                )

            finally:
                camera_service.kamera_sperre.release()

        # ── Methode "set_preferences" ─────────────────────────────────────
        elif method_request.name == "set_preferences":
            try:
                data = (
                    json.loads(method_request.payload)
                    if isinstance(method_request.payload, str)
                    else method_request.payload
                )
            except (json.JSONDecodeError, TypeError) as e:
                return MethodResponse.create_from_method_request(
                    method_request, status=400, payload={"error": str(e)}
                )

            # Validierung und Aktualisierung
            neue_werte = {}
            if "mode" in data and data["mode"] in ("photo", "video"):
                neue_werte["mode"] = data["mode"]
            if "duree" in data and data["duree"] in (15, 30, 60):
                neue_werte["duree"] = int(data["duree"])
            if "classes" in data:
                gueltig = [c for c in data["classes"] if c in config.ALLE_KLASSEN]
                if gueltig:
                    neue_werte["classes"] = gueltig
            if "cooldown" in data:
                cooldown = int(data["cooldown"])
                if 5 <= cooldown <= 3600:
                    neue_werte["cooldown"] = cooldown

            praeferenzen_kopie = preferences_service.praeferenzen_aktualisieren(neue_werte)

            try:
                preferences_service.praeferenzen_speichern(praeferenzen_kopie)
            except Exception as e:
                print(f"  WARNUNG: Einstellungen konnten nicht gespeichert werden: {e}")

            print(f"  Einstellungen aktualisiert: {praeferenzen_kopie}")
            return MethodResponse.create_from_method_request(
                method_request,
                status=200,
                payload={"ok": True, "preferences": praeferenzen_kopie},
            )

        # ── Unbekannte Methode ──────────────────────────────────────────────
        else:
            print(f"  Unbekannte Methode: {method_request.name}")
            payload = {"error": f"Unbekannte Methode: {method_request.name}"}
            return MethodResponse.create_from_method_request(
                method_request, status=404, payload=payload
            )

    return method_handler


class IoTHubConnectionManager:
    """
    Verwaltet die Verbindung zum Azure IoT Hub in einem eigenen Daemon-Thread:
    Verbindungsaufbau mit exponentiellem Backoff, Erkennung von
    Verbindungsabbrüchen und sauberes, sofort unterbrechbares Herunterfahren.

    Nutzung:
        manager = IoTHubConnectionManager(config.IOT_HUB_CONN_STR, handler)
        manager.start()   # nicht blockierend
        ...
        manager.stop()    # sauberes Beenden
    """

    def __init__(
        self,
        connection_string: str,
        method_handler: Callable[[MethodRequest], MethodResponse],
        *,
        websockets: bool = True,
        keep_alive: int = 30,
        verzoegerungen: list | None = None,
    ) -> None:
        self._connection_string = connection_string
        self._method_handler = method_handler
        self._websockets = websockets
        self._keep_alive = keep_alive
        self._verzoegerungen = verzoegerungen or config.IOT_HUB_WIEDERVERBINDUNGS_VERZOEGERUNGEN

        self._client: IoTHubDeviceClient | None = None
        self._verbunden = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """
        Erstellt den IoTHubDeviceClient, registriert die Callbacks und startet
        den Verbindungs-Thread als Daemon. Nicht blockierend. Falls der Client
        nicht erstellt werden kann, wird ein FEHLER geloggt und die Methode
        kehrt zurück, ohne einen Thread zu starten — der Pi läuft dann einfach
        weiter offline (nur lokal + SMS), wie im ursprünglichen Verhalten.
        """
        try:
            self._client = IoTHubDeviceClient.create_from_connection_string(
                self._connection_string,
                websockets=self._websockets,  # Port 443 — passiert alle NATs/Firewalls
                keep_alive=self._keep_alive,
                connection_retry=False,  # Reconnect wird von dieser Klasse selbst verwaltet
            )
            self._client.on_connection_state_change = self._on_verbindungsaenderung
            self._client.on_method_request_received = self._method_handler
        except Exception as e:
            journal(
                "FEHLER",
                f"IoT Hub Client konnte nicht erstellt werden: {e} "
                "— Pi läuft trotzdem (nur lokal + SMS)",
            )
            self._client = None
            return

        self._thread = threading.Thread(
            target=self._verbindungs_schleife, name="IoTHub", daemon=True
        )
        self._thread.start()
        journal("INFO", "IoT Hub-Thread gestartet (Verbindung läuft im Hintergrund)")

    def stop(self, timeout: float | None = 5.0) -> None:
        """Signalisiert dem Verbindungs-Thread zu stoppen und trennt den Client."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        if self._client is not None:
            journal("INFO", "IoT Hub trennen…")
            try:
                self._client.shutdown()
            except Exception:
                pass

    def is_connected(self) -> bool:
        """Thread-sicherer Verbindungsstatus."""
        return self._verbunden.is_set()

    def _on_verbindungsaenderung(self) -> None:
        if self._client.connected:
            self._verbunden.set()
        else:
            self._verbunden.clear()

    def _verbindungs_schleife(self) -> None:
        while not self._stop.is_set():
            if self._verbindung_mit_backoff():
                self._warten_waehrend_verbunden()
            # Nicht (mehr) verbunden: Schleifenbedingung neu auswerten
            # (stop() beendet sofort, sonst wird ein neuer Zyklus versucht)

    def _verbindung_mit_backoff(self) -> bool:
        """
        Versucht die Verbindung mit exponentiellem Backoff aufzubauen.
        Gibt True zurück sobald verbunden, False falls stop() aufgerufen wurde
        oder alle Versuche erschöpft sind (nach 5 Minuten Pause wird die
        äußere Schleife den nächsten Zyklus starten).
        """
        for i, delay in enumerate(self._verzoegerungen, start=1):
            if self._stop.is_set():
                return False
            try:
                journal("INFO", f"IoT Hub: Verbindungsversuch {i}…")
                self._client.connect()
                self._verbunden.set()
                journal("INFO", "IoT Hub: verbunden ✓ — bereit für Direct Methods")
                return True
            except Exception as e:
                journal(
                    "INFO",
                    f"IoT Hub: Versuch {i} fehlgeschlagen ({e}) "
                    f"— nächster Versuch in {delay}s",
                )
                if self._stop.wait(timeout=delay):
                    return False

        # Alle Versuche erschöpft — 5 min warten, dann neuer Zyklus
        journal(
            "INFO",
            "IoT Hub: alle Versuche fehlgeschlagen — "
            "Pi läuft weiter offline, nächster Anlauf in 5 min",
        )
        self._stop.wait(timeout=300)
        return False

    def _warten_waehrend_verbunden(self) -> None:
        """Bleibt hier solange die Verbindung steht; kehrt zurück sobald sie abbricht oder stop() gesetzt ist."""
        while not self._stop.is_set():
            if self._verbunden.wait(timeout=15):
                time.sleep(2)
            else:
                # SDK hat Verbindung verloren → _verbunden wurde per Callback gecleared
                journal(
                    "INFO", "IoT Hub: Verbindung verloren — starte Wiederverbindung…"
                )
                return
