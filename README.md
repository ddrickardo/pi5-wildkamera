# 🦌 Wildkamera Pi 5 – Edge-KI-Wildtiererkennung

Wildkamera auf Basis eines **Raspberry Pi 5**, die Wildtiere **direkt auf dem Gerät** erkennt.
Ein feinabgestimmtes **YOLO26l-Modell** (NCNN, CPU-Inferenz) unterscheidet vier Wildarten;
nur Bilder mit Tier werden gespeichert und – sobald Netz verfügbar ist – nach **Azure** übertragen.

---

## ✨ Funktionen

- **Artbestimmung** – Hirsch, Rehbock, Rehwild und Wildschwein statt nur „Bewegung erkannt“
- **Datenschutz an der Quelle** – Bilder ohne Tier verlassen das Gerät nie
- **Offline-First** – Erkennung, Speicherung und SMS funktionieren ohne Datenverbindung
- **Fernsteuerung** – Aufnahmen und Einstellungen per Azure IoT Hub (Direct Methods)

## 📊 Ergebnisse

| Kennzahl | Wert |
|---|---|
| mAP@0.5 (4 Wildarten) | **0,875** |
| Inferenz auf der Pi-5-CPU (NCNN, 640 × 640) | ca. 600–700 ms |
| Gesamtlatenz PIR → SMS | ca. 5,3 s |
| Offline-/Reconnect-Tests | keine Verluste, keine Duplikate |

---

## 🔭 Systemüberblick

```mermaid
%%{init: {"flowchart": {"curve": "stepBefore"}}}%%
flowchart LR
    PIR(["PIR-Sensor"]) --> CAM["Kamera<br/>IMX219"]
    CAM --> INFER["Lokale Inferenz<br/>YOLO26l · NCNN"]
    INFER --> DEC{"Tier erkannt?"}

    DEC -- "Nein" --> DROP["Bild verwerfen"]
    DEC -- "Ja" --> SAVE[("Lokal speichern<br/>DCIM/ + TRANSMIT/")]
    SAVE --> QUEUE["Upload-Warteschlange"]
    QUEUE --> NET{"4G verfügbar?"}
    NET -- "Ja" --> AZURE[("Azure Blob Storage")]

    DEC -- "Ergebnis" --> SMS["SMS senden<br/>SIM7600"]

    classDef sensor fill:#e8f2ff,stroke:#3973ac,color:#17365d,stroke-width:1.5px;
    classDef compute fill:#eee8ff,stroke:#7654a5,color:#38245b,stroke-width:1.5px;
    classDef decision fill:#fff4d6,stroke:#b88716,color:#654600,stroke-width:1.5px;
    classDef storage fill:#e5f5e9,stroke:#43845a,color:#194d2b,stroke-width:1.5px;
    classDef cloud fill:#e2f5f7,stroke:#2f8791,color:#16474d,stroke-width:1.5px;
    classDef notify fill:#ffe9e7,stroke:#b85c50,color:#702d26,stroke-width:1.5px;
    classDef discard fill:#f1f1f1,stroke:#777,color:#333,stroke-dasharray: 4 3;

    class PIR,CAM sensor;
    class INFER compute;
    class DEC,NET decision;
    class SAVE,QUEUE storage;
    class AZURE cloud;
    class SMS notify;
    class DROP discard;
```

Alles links vom Upload läuft vollständig lokal auf dem Pi; die Cloud ist eine optionale Ergänzung.

## 🧩 Software-Architektur

```mermaid
%%{init: {"flowchart": {"curve": "stepAfter"}}}%%
flowchart TB
    MAIN(["main.py<br/>Initialisierung &<br/>Thread-Start"])

    PIR["pir_service.py<br/>Bewegungsschleife"]
    IOT["azure_iot_service.py<br/>Direct Methods"]
    UPL["azure_blob_uploader.py<br/>Upload-Queue"]

    SMS["sms_service.py<br/>SMS-Versand"]
    CAM["camera_service.py<br/>Foto- / Videoaufnahme"]
    DET["detection_service.py<br/>YOLO-Inferenz"]

    CFG["config.py<br/>Konfiguration"]
    PREF["preferences_service.py<br/>Aufnahme-Einstellungen"]
    LOG["logging_service.py<br/>Protokollierung"]

    MAIN --> PIR
    MAIN --> IOT
    MAIN --> UPL
    PIR --> SMS
    PIR --> CAM
    IOT --> CAM
    CAM --> DET
    DET ~~~ CFG
    DET ~~~ PREF
    DET ~~~ LOG

    classDef entry fill:#17365d,stroke:#10243a,color:#fff,stroke-width:2px;
    classDef control fill:#e8f2ff,stroke:#3973ac,color:#17365d,stroke-width:1.5px;
    classDef domain fill:#e5f5e9,stroke:#43845a,color:#194d2b,stroke-width:1.5px;
    classDef core fill:#ffffff,stroke:#b88716,color:#654600,stroke-width:1.5px;

    class MAIN entry;
    class PIR,IOT,UPL control;
    class SMS,CAM,DET domain;
    class CFG,PREF,LOG core;
```

`pir_service` und `azure_iot_service` teilen sich ein einziges Objekt: die **Kamera-Sperre**
`camera_service.kamera_sperre`. So greifen PIR-Aufnahmen und Fernaufnahmen nie gleichzeitig auf die Kamera zu.
Die Basisdienste in der unteren Reihe (Konfiguration, Einstellungen, Protokollierung) werden von allen Modulen genutzt.

### Module

| Datei | Aufgabe |
|---|---|
| `main.py` | Startet und verbindet alle Module |
| `pir_service.py` | Bewegungsschleife mit Cooldown |
| `azure_iot_service.py` | Direct Methods `capture` / `set_preferences`, Wiederverbindung |
| `azure_blob_uploader.py` | Upload-Warteschlange nach Azure Blob Storage |
| `camera_service.py` | Picamera2-Steuerung, Foto- und Video-Pipeline, Kamera-Sperre |
| `detection_service.py` | Modell laden, Erkennungen filtern, Bilder annotieren |
| `sms_service.py` | SMS-Versand über SIM7600 (AT-Kommandos) |
| `preferences_service.py` | Thread-sichere Aufnahme-Einstellungen |
| `config.py` | Zentrale Konstanten, lädt `.env` |
| `logging_service.py` | Ereignisprotokoll (Datei + Konsole) |

---

## 🔄 Abläufe im Detail

<details>
<summary><b>PIR-Aufnahme</b></summary>

```mermaid
%%{init: {"flowchart": {"curve": "stepAfter"}}}%%
flowchart TD
    START(["Bewegung erkannt"]) --> COOL{"Cooldown aktiv?"}
    COOL -- "Ja" --> IGN(["Ignorieren"])
    COOL -- "Nein" --> LOCK["Kamera-Sperre anfordern"]

    LOCK --> MODE{"Aufnahmemodus"}
    MODE -- "Foto" --> PHOTO["Foto aufnehmen"]
    MODE -- "Video" --> VIDEO["Video aufnehmen"]
    PHOTO --> DETECT["YOLO-Inferenz ausführen"]
    VIDEO --> DETECT

    DETECT --> ANIMAL{"Tier erkannt?"}
    ANIMAL -- "Ja" --> SAVE[("In DCIM/ speichern<br/>nach TRANSMIT/ kopieren")]
    ANIMAL -- "Nein" --> DISCARD["Aufnahme verwerfen"]

    SAVE --> RELEASE["Kamera-Sperre freigeben"]
    DISCARD --> RELEASE
    RELEASE --> NOTIFY["SMS asynchron senden"]
    NOTIFY --> WAIT(["Auf Bewegungsende warten"])

    classDef event fill:#17365d,stroke:#10243a,color:#fff,stroke-width:2px;
    classDef action fill:#e8f2ff,stroke:#3973ac,color:#17365d,stroke-width:1.5px;
    classDef decision fill:#fff4d6,stroke:#b88716,color:#654600,stroke-width:1.5px;
    classDef storage fill:#e5f5e9,stroke:#43845a,color:#194d2b,stroke-width:1.5px;
    classDef discard fill:#f1f1f1,stroke:#777,color:#333,stroke-dasharray: 4 3;

    class START,IGN,WAIT event;
    class LOCK,PHOTO,VIDEO,DETECT,RELEASE,NOTIFY action;
    class COOL,MODE,ANIMAL decision;
    class SAVE storage;
    class DISCARD discard;
```

</details>

<details>
<summary><b>Fernaufnahme über Direct Method <code>capture</code></b></summary>

```mermaid
%%{init: {"flowchart": {"curve": "stepAfter"}}}%%
flowchart TD
    REQ(["Direct Method<br/>capture empfangen"]) --> BUSY{"Kamera frei?"}

    BUSY -- "Nein" --> E503["HTTP 503<br/>Kamera belegt"]
    BUSY -- "Ja" --> VALID{"Payload gültig?"}

    VALID -- "Nein" --> E400["HTTP 400<br/>Ungültige Anfrage"]
    VALID -- "Ja" --> CAPTURE["Foto / Video aufnehmen"]
    CAPTURE --> RESULT["Ergebnis zusammenstellen"]
    RESULT --> E200["HTTP 200<br/>Ergebnis zurückgeben"]

    classDef request fill:#17365d,stroke:#10243a,color:#fff,stroke-width:2px;
    classDef decision fill:#fff4d6,stroke:#b88716,color:#654600,stroke-width:1.5px;
    classDef action fill:#e8f2ff,stroke:#3973ac,color:#17365d,stroke-width:1.5px;
    classDef success fill:#e5f5e9,stroke:#43845a,color:#194d2b,stroke-width:1.5px;
    classDef error fill:#ffe9e7,stroke:#b85c50,color:#702d26,stroke-width:1.5px;

    class REQ request;
    class BUSY,VALID decision;
    class CAPTURE,RESULT action;
    class E200 success;
    class E400,E503 error;
```

Die Sperre wird hier **nicht blockierend** angefordert: Ist die Kamera belegt, antwortet das Gerät sofort.

</details>

<details>
<summary><b>Upload-Warteschlange</b></summary>

```mermaid
%%{init: {"flowchart": {"curve": "stepAfter"}}}%%
flowchart TD
    TIMER(["Start: alle 60 Sekunden"]) --> NET{"4G verfügbar?"}

    NET -- "Ja" --> PREF["Einstellungen von Azure abrufen"]
    PREF --> NEXT["Älteste Datei aus TRANSMIT/ auswählen"]
    NEXT --> UPLOAD["Datei nach Azure Blob Storage hochladen"]
    UPLOAD --> OK{"Upload erfolgreich?"}

    OK -- "Ja" --> DELETE["Datei aus TRANSMIT/ entfernen"]
    OK -- "Nein" --> KEEP["Datei bleibt in TRANSMIT/"]

    NET -- "Nein" --> DONE(["Warten auf nächsten Zyklus"])
    DELETE --> DONE
    KEEP --> DONE

    classDef trigger fill:#17365d,stroke:#10243a,color:#fff,stroke-width:2px;
    classDef action fill:#e8f2ff,stroke:#3973ac,color:#17365d,stroke-width:1.5px;
    classDef decision fill:#fff4d6,stroke:#b88716,color:#654600,stroke-width:1.5px;
    classDef storage fill:#e5f5e9,stroke:#43845a,color:#194d2b,stroke-width:1.5px;

    class TIMER,DONE trigger;
    class PREF,NEXT,UPLOAD,KEEP action;
    class NET,OK decision;
    class DELETE storage;
```

Der Zyklus wiederholt sich alle 60 Sekunden. Die Warteschlange liegt im Dateisystem und übersteht daher auch einen Neustart.

</details>

<details>
<summary><b>IoT-Hub-Wiederverbindung</b></summary>

```mermaid
%%{init: {"flowchart": {"curve": "stepAfter"}}}%%
flowchart TD
    START(["Start / Verbindung verloren"]) --> TRY["Verbindungsversuch"]
    TRY --> OK{"Erfolgreich?"}
    OK -- "Ja" --> CONN(["Verbunden<br/>Direct Methods aktiv"])
    OK -- "Nein" --> MORE{"Weitere<br/>Versuche?"}
    MORE -- "Ja" --> BACK["Backoff<br/>5–300 s"]
    MORE -- "Nein" --> PAUSE["Wartepause<br/>5 Minuten"]
    BACK --> AGAIN(["Erneuter Versuch"])
    PAUSE --> AGAIN

    classDef event fill:#17365d,stroke:#10243a,color:#fff,stroke-width:2px;
    classDef action fill:#e8f2ff,stroke:#3973ac,color:#17365d,stroke-width:1.5px;
    classDef decision fill:#fff4d6,stroke:#b88716,color:#654600,stroke-width:1.5px;
    classDef success fill:#e5f5e9,stroke:#43845a,color:#194d2b,stroke-width:1.5px;

    class START,AGAIN event;
    class TRY,BACK,PAUSE action;
    class OK,MORE decision;
    class CONN success;
```

„Erneuter Versuch“ startet wieder beim Verbindungsversuch, ebenso ein Verbindungsverlust. Alle Wartezeiten nutzen `threading.Event.wait()`, sodass `stop()` auch einen laufenden Backoff sofort beendet.

</details>

---

## ⚙️ Installation (auf dem Raspberry Pi 5)

```bash
sudo apt install -y python3-picamera2
git clone https://github.com/ddrickardo/pi5-wildkamera.git
cd pi5-wildkamera
pip install -r requirements.txt --break-system-packages
cp .env.example .env    # echte Werte eintragen
python3 main.py
```

Für den Dauerbetrieb als systemd-Dienst einrichten, mit `WorkingDirectory` auf dem Projektverzeichnis.

### Konfiguration

Geheimnisse stehen ausschließlich in `.env` (per `.gitignore` ausgeschlossen); `.env.example` dient als Vorlage.

| Variable | Inhalt |
|---|---|
| `IOT_HUB_CONN_STR` | Verbindungszeichenfolge des IoT-Hub-Geräts |
| `STORAGE_CONN_STR` | Verbindungszeichenfolge des Blob Storage |
| `AZURE_FUNC_KEY` | Azure Function Key (leer bei anonymem Zugriff) |
| `SMS_NUMMER` | Zielrufnummer für SMS |
| `SMS_PIN` | PIN der SIM-Karte |

Alle übrigen Einstellungen (Auflösung, Konfidenzschwelle, Verzeichnisse, GPIO-Pin, SMS-Port …) stehen in `config.py`.
Fehlt eine Pflichtvariable, bricht `config.py` mit einer klaren Fehlermeldung ab.

---

## ⚠️ Hinweise

- **Nur für den Raspberry Pi 5:** Picamera2, libcamera, gpiozero und der serielle SIM7600-Port sind Hardware-Abhängigkeiten. Eine PC-Kompatibilitätsschicht ist bewusst nicht vorgesehen.
- **API-Vertrag:** Die JSON-Schlüssel im Austausch mit App und Azure Function (`mode`, `duree`, `classes`, `saved`, `confiance`, `bbox` …) bleiben absichtlich unverändert.

## 🛠️ Technologien

Python 3 · Ultralytics YOLO26 · NCNN · OpenCV · Picamera2 · gpiozero · Azure IoT Hub · Azure Blob Storage · SIM7600 (4G/GSM)

## 📄 Lizenz

[AGPL-3.0](LICENSE)

## 👤 Autor

**Rickardo Dimbison Doré**
