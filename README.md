English version: [README.en.md](README.en.md)

# POCKET KNIFE

Aktuelle Version: **pocketknife-2.28.1** – siehe `CHANGELOG.md`.

Webseite: [helfi.space](https://helfi.space)

Webanwendung zur **Inventarisierung** und **Softwareverteilung** für Windows-Computer in einer Active-Directory-Domäne (Microsoft AD oder Samba AD).

- Läuft als Docker-Container, Daten (SQLite, Logs, Zertifikat, winget-Index) liegen unter `/data`
- Anmeldung mit Domänenkonten über **LDAPS**; nur Mitglieder einer festgelegten AD-Gruppe haben Zugriff
- Das LDAPS-Zertifikat der DCs wird **automatisch vertraut**; der SHA-256-Fingerabdruck wird gespeichert und eine Änderung protokolliert
- Die Oberfläche lädt **nichts aus dem Internet** (keine Fonts, CDNs oder Bibliotheken; strenge Content-Security-Policy)

## Hilfe, Handbuch und Sprache

- **Hilfe** im Programm (Navigation → Hilfe, oder das **?** oben rechts auf jeder Seite und in den wichtigsten Dialogen) mit Inhaltsverzeichnis, Suche und Beispielen.
- **Handbuch als PDF** in Deutsch und Englisch: `app/static/help/POCKET-KNIFE-Handbuch.pdf` und `POCKET-KNIFE-Manual.pdf` – auch über die Hilfe herunterladbar.
- **Deutsch und Englisch**: Umschalter **DE / EN** unten links und auf der Anmeldeseite (gilt je Browser).

Für Entwickler: Die Texte des Handbuchs stehen in `app/static/help/de.md` und `en.md`; `python3 tools/build_manual.py` erzeugt daraus die PDFs (benötigt pandoc und Playwright). Die englische Oberfläche kommt aus dem Wörterbuch `app/i18n/en.json`; `python3 tools/i18n_extract.py` listet neue, noch nicht übersetzte Texte, `python3 tools/build_i18n.py` baut daraus `app/static/i18n.js`.

## Funktionen

| Bereich | Inhalt |
|---|---|
| Computer | Automatischer Abgleich aus dem AD, Heartbeat (online/offline – nur wenn unter der Adresse wirklich dieser Computer antwortet; veraltete DNS-Adressen werden erkannt, DNS-Cache einstellbar), Scans über WinRM. Hardware, BIOS, OS mit Version/Build, Datenträger, Netzwerk, installierte Software (Computer + Benutzer), Hotfixes, Dienste, lokale Administratoren, Defender, BitLocker, TPM, ausstehender Neustart, App-Installer-Version |
| Computeransicht | Übersichtlicher Aufbau mit 28 Kategorien: Computer, Active-Directory-Gruppen, Anwendungen, Sammlungen, CPU, Benutzerdefinierte Felder, Bereitstellungen, Laufwerke, Bildschirme, Umgebungsvariablen, Dateien & Ordner, Hardware, Hotfixes, Lokale Gruppen, Lokale Benutzer, Speichermodule, Netzwerkkarten, PowerShell, Drucker (lokal), Prozesse, Produktschlüssel, Registry, Scans, Dienste, Freigaben, Windows-Features, Geplante Aufgaben, WMI |
| Scanprofile | 20 Standard-Scanner und eigene Scanner (Registry, Dateien, WMI, PowerShell) frei zu Profilen kombinieren, auf Sammlungen oder einzelne Computer anwenden und mit Auslösern steuern (Einmalig, Intervall, Täglich, Wöchentlich, Monatlich, Scan-Alter mit Zeitfenster, Heartbeat). Benutzerdefinierte Felder (z. B. Inventarnummer) unter Einstellungen |
| Sammlungen | statische und dynamische Sammlungen mit verschachtelten Filtergruppen (Alle / Eine / Nicht alle / Keine) über alle Scan-Daten, Versionsvergleich, Muster, RegEx, Datumsfilter, Live-Vorschau, Vorlagen, Ordnerstruktur; per Rechtsklick direkt aus Inventardaten erstellen (z. B. „Computer mit Drucker XY“, „Software XY älter als Version …“) |
| Software | Übersicht aller Programme, automatischer Abgleich mit dem offiziellen **winget-Repository** (Index `source2.msix`), Kennzeichnung veralteter Versionen, manuelle Zuordnung oder Ignorieren; optional über einen **Proxyserver** (auch für winget auf den Clients) |
| Pakete | Aus **Schritten** (Vorbereitung, Schritte, Abschluss): Installieren (winget oder Datei aus dem Repository: MSI, MSP, MSU, EXE, CMD/BAT, PS1, REG – mit MSI-Autoerkennung), Befehl, PowerShell, verschachteltes Paket, Dateikopie, Registry (Werte/Schlüssel setzen und löschen, .reg-Import), Scannen, Neustart, Warten, Nachricht, Abmelden. Bedingungen (Betriebssystem, Architektur, PowerShell, Anmeldestatus, Datei, Registry, Sammlung), Ausführen als Dienstkonto/System/angemeldeter Benutzer, Fehlerverhalten, Vorschau des tatsächlichen Aufrufs; nach erfolgreicher Installation/Aktualisierung optional eine **Windows-Benachrichtigung** für den angemeldeten Benutzer („7-Zip wurde aktualisiert. Installierte Version: 26.00“); Repository-Suche beim Paketbau (sofort, über ein Verzeichnis im Arbeitsspeicher); **Export/Import** von Paketen als Datei (z. B. in eine zweite Installation – ohne IDs, Repository-Pfade relativ); **Bruchprüfung**: Größe, Datum und Fingerabdruck der benutzten Repository-Dateien werden festgehalten – fehlt eine Datei oder ändert sich die Installationsdatei, werden Bereitstellungen und Zeitpläne unterbrochen, bis jemand den neuen Stand übernimmt (Schnellprüfung alle 5 Minuten, wöchentliche Vollprüfung nachts) |
| Bereitstellungen | Sofort oder geplant, für Auswahl oder Sammlung, Liste mit Ziel (Sammlung/Computer), Live-Fortschritt, Ergebnis je Schritt und Computer, „Fehlgeschlagene erneut“ sowie **einzelne fehlgeschlagene Schritte wiederholen** („Nur diesen Schritt“ / „Ab diesem Schritt“, für alle betroffenen oder einen einzelnen Computer) |
| Zeitpläne | **Alles Automatische an einer Stelle** – Art „Pakete verteilen“ (mehrere Pakete nacheinander) oder „winget-Updates“ (alle oder bestimmte IDs, Ausnahmen); Auslöser inkl. **Heartbeat**, optionales Zeitfenster (Wochentage, auch über Mitternacht), Ziele, offline Computer vormerken, Wartezeit nach Fehlschlag, erneut versorgen nach X Stunden, Vorschau, Zielverlauf |
| Neuigkeiten | dezent in der Seitenleiste: was ist neu (Sicherheit, Neu, Verbessert, Behoben) und – für Hauptadmins – ob eine neue Version verfügbar ist (der Server fragt einmal täglich bei GitHub) |
| Repository | Installationsdateien direkt in der Oberfläche verwalten: **hochladen** (Dateien und ganze Ordner, auch per Ziehen vom Desktop, in Stücken mit Fortsetzen), Ordner anlegen, umbenennen, **verschieben mit automatischer Anpassung der Pfade in allen Paketen** (Vorschau, Sicherung vorher, Zurückdrehen bei Fehler), löschen nur, was kein Paket benutzt; Assistent „Repository umstrukturieren“; **Duplikatsperre** (gleiche winget-ID, MSI-Produktcode oder Datei per SHA-256 nur einmal – zentral oder bei einem Mandanten); **Datei suchen** per Fingerabdruck, wenn außerhalb verschoben wurde |
| Mandanten | Pakete und Zeitpläne je Kunde/Standort, optional auf eine Sammlung beschränkt; **OU-Admins**: Mitglieder der AD-Gruppe eines Mandanten melden sich an und betreuen nur dessen Computer (alle Pakete verteilen, ändern nur eigene, eigene nur mit Freigabe; Repository-Ordner `Mandanten\<Name>` wird automatisch angelegt, Upload mit Kontingent) – serverseitig an jeder Schnittstelle geprüft |
| Berichte | 19 Berichte (u. a. Software, veraltete Software, Betriebssysteme, Speicherplatz, Windows-Updates, Sicherheitsstatus, lokale Admins, Softwareänderungen, Bereitstellungsverlauf) – Tabelle, CSV-Export (Excel-kompatibel), Druckansicht |
| Eigene Berichte | Berichte selbst zusammenstellen: Datenquelle (Computer, Anwendungen, Laufwerke, Drucker, Dienste, eigene Scanner …), Spalten, Zeilen- und Computerfilter wie bei Sammlungen, Zusammenfassen/Zählen, Sortierung, Vorlagen, Vorschau – nutzbar im Berichtsversand und als Benachrichtigung („Mail, sobald der Bericht Zeilen liefert“) |
| Software-Kiosk | Nutzer installieren freigegebene Software selbst unter `/kiosk` – nur auf dem Rechner, an dem sie angemeldet sind (live geprüft); Zugang über AD-Gruppen und/oder OUs, Freigabe je Paket (mit Mail), je Paket nur für bestimmte AD-Gruppen, Deinstallieren, Status, Neustart planen; optional **Anmeldung mit Windows (Kerberos-SSO)**, Standard aus; kein Zugriff auf die Verwaltung |
| E-Mail | **Berichte per E-Mail** zu festen Zeiten (mehrere Berichte in einer Mail, Tabelle + CSV-Anhang, optional nur wenn nicht leer) und optionale **Benachrichtigungen** bei Ereignissen (wenig Speicherplatz, lange offline, Scan/Bereitstellung/Sicherung fehlgeschlagen, Dienstkonto-Schutz) – SMTP mit STARTTLS/SSL, Kennwort verschlüsselt |
| Fernsitzungen | RDP und VNC direkt im Browser (Apache Guacamole), wahlweise mit der eigenen POCKET KNIFE-Anmeldung bzw. einem zentralen VNC-Kennwort, Zwischenablage, Strg+Alt+Entf, Vollbild |
| Protokoll | Alle Ereignisse in der Datenbank (filterbar, CSV) und als rotierende Datei in `/data/logs/` |

## So funktioniert der Zugriff auf die Clients

```
 POCKET KNIFE (Docker)
   │  LDAPS 636 ───────────────► Domänencontroller (Anmeldung, Gruppe, Computerliste)
   │  WinRM 5985/5986 ─────────► Client: Inventar-Skript, Schritte starten/abfragen
   │  SMB 445 (ADMIN$) ────────► Client: Paketdateien nach C:\Windows\Temp\PocketKnife\…
   │  SMB 445 ─────────────────► Repository-Freigabe (eigene Pakete lesen)
   └  HTTPS ───────────────────► cdn.winget.microsoft.com (nur winget-Index, optional)
```

Installationen laufen standardmäßig **direkt als Dienstkonto**: POCKET KNIFE startet den Schritt über WinRM als eigenen Prozess (Win32_Process), der auch einen Verbindungsabbruch übersteht – **ohne geplante Aufgabe und ohne Kennwort auf dem Client** (ab 2.14). Nur „Lokales System“ und „Angemeldeter Benutzer“ nutzen eine temporäre geplante Aufgabe (`\POCKETKNIFE\…`), ebenfalls ohne gespeichertes Kennwort. Es gibt kein Double-Hop-Problem, weil die Paketdateien vorher vom Container per SMB kopiert werden. Nach der Ausführung werden Prozess, Aufgabe und Dateien wieder entfernt. Wird POCKET KNIFE währenddessen neu gestartet (z. B. Update), räumt es danach auf dem Computer auf, sobald er erreichbar ist – ein noch laufender Installer wird dabei abgewartet, nicht abgebrochen (ab 2.18).

### Woher kommen die Installationsdateien?

| Pakettyp | Quelle der Installationsdatei |
|---|---|
| Eigenes Paket | Ihr Repository. Der Container kopiert den Paketordner per SMB auf den Client (`ADMIN$\Temp\PocketKnife`), danach wird er wieder gelöscht. Der Client braucht keinen Internetzugang. |
| winget | Das Internet. Jeder Client lädt den Installer selbst von der Herstelleradresse aus dem winget-Manifest. Im Repository wird nichts gespeichert. |

Der Container lädt nur den winget-**Katalog** (Index für den Versionsvergleich), keine Installer.

## Voraussetzungen in der Domäne

1. **Dienstkonto** anlegen, z. B. `svc-pocketknife` (normales Benutzerkonto, Passwort läuft nicht ab).
2. Das Dienstkonto per GPO in die lokale Gruppe **Administratoren** aller Clients aufnehmen
   (*Computerkonfiguration → Einstellungen → Systemsteuerungseinstellungen → Lokale Benutzer und Gruppen*).
3. **WinRM** per GPO aktivieren (*Windows-Remoteverwaltung → Remoteserververwaltung über WinRM zulassen*, Dienst „WinRM“ automatisch starten).
4. Firewall auf den Clients für die IP des Docker-Hosts öffnen: **TCP 5985** (oder 5986) und **TCP 445**.
5. Für Remote-Zugriff mit lokalem Admin-Token braucht es bei Domänenkonten nichts weiter. Nur falls Sie ein *lokales* Konto verwenden, wäre `LocalAccountTokenFilterPolicy=1` nötig (nicht empfohlen).
6. AD-Gruppe **POCKETKNIFE-Admins** anlegen und die Bediener aufnehmen (verschachtelte Gruppen werden berücksichtigt).
7. **LDAPS** muss auf den DCs aktiv sein. Samba AD stellt es standardmäßig bereit (auch mit selbstsigniertem Zertifikat). Bei Microsoft AD braucht der DC ein Serverzertifikat (z. B. aus der AD-CS); da POCKET KNIFE dem Zertifikat automatisch vertraut, reicht auch ein selbstsigniertes.
8. Für winget: Der **App-Installer** (Microsoft.DesktopAppInstaller) muss auf den Clients vorhanden sein (Windows 10 21H2+ / Windows 11 / Server 2025). In der Computeransicht sehen Sie, ob er gefunden wurde.
9. Für eigene Pakete: Freigabe anlegen, z. B. `\\fileserver\software$`, Leserecht für das Dienstkonto (oder ein eigenes Repository-Konto). Für Hochladen, Umbenennen, Verschieben und Löschen über die Oberfläche (ab 2.27) zusätzlich **Schreibrecht**; andere Admins brauchen dann keinen direkten Zugriff mehr auf die Freigabe.

## Systemanforderungen und Größenplanung

**Docker-Host**

- Linux (z. B. Debian 12/13 oder Ubuntu 22.04/24.04), 64 Bit (x86_64), als VM oder physisch
- Docker Engine 24 oder neuer
- **SSD-Speicher** für das Datenverzeichnis (`PK_DATA_PATH`) – die SQLite-Datenbank schreibt bei Scans viele kleine Änderungen
- DNS der Domäne (`PK_DNS_1`), Netzwerkzugriff auf die Clients (WinRM 5985/5986, SMB 445, für Fernsitzungen 3389/5900), die Domänencontroller (LDAPS 636) und die Repository-Freigabe
- Internet ist optional: nur für den winget-Index (`cdn.winget.microsoft.com`) – oder der Index wird offline hochgeladen; die Clients laden winget-Pakete selbst

**Clients:** Windows 10/11 bzw. Windows Server 2016–2025 mit PowerShell 5.1, WinRM aktiv, Dienstkonto als lokaler Administrator. winget ab Windows 10 1809 / Server 2019.

**Richtwerte nach Größe der Umgebung**

> Die Werte sind Schätzungen aus dem Aufbau des Programms (ein Prozess, SQLite, parallele WinRM-Verbindungen), nicht in großen Umgebungen gemessen. Erst klein beginnen, unter **Einstellungen → Wartung** und mit `docker stats` beobachten und bei Bedarf erhöhen.

| Computer | vCPU | RAM (gesamt, inkl. guacd) | Speicher (SSD) | Parallele Scans / Installationen | Heartbeat |
|---|---|---|---|---|---|
| bis 250 | 2 | 2 GB | 10 GB | 6 / 8 (Standard) | 5 Min. |
| 250 – 1 000 | 2 – 4 | 4 GB | 20 GB | 10 / 12 | 5 Min. |
| 1 000 – 3 000 | 4 | 8 GB | 40 GB | 15 – 20 / 15 – 25 | 10 Min. |
| 3 000 – 5 000 | 8 | 16 GB | 80 GB | 20 – 30 / 25 – 40 | 15 Min. |
| über 5 000 | – | – | – | mehrere Instanzen (z. B. je Standort/Mandant) | – |

Die Zahl der parallelen Scans und Installationen sowie das Heartbeat-Intervall stehen unter **Einstellungen → Zeitpläne & Leistung** (Änderungen an der Parallelität wirken nach einem Neustart des Containers).

**Wofür die Ressourcen gebraucht werden**

| Bereich | Bedarf |
|---|---|
| Datenbank | je Computer grob 0,5 – 2 MB (Software, Dienste, Hotfixes, Prozesse, Hardware, Aufgaben, Verlauf). 1 000 Computer ≈ 1 – 2 GB. Kürzere Aufbewahrung (Protokoll, Scan-Protokoll, Bereitstellungen) hält sie klein. |
| winget-Index | ca. 0,5 – 1 GB im Datenverzeichnis, beim Einlesen kurzzeitig zusätzlich RAM |
| Scans | Die Arbeit läuft auf dem Client; der Server wartet überwiegend auf WinRM. Ein vollständiger Scan dauert je Computer etwa 20 – 60 s – mit 6 parallelen Scans also rund 400 – 1 000 Computer pro Stunde. Mehr Parallelität = schneller, aber mehr RAM und mehr Last auf DCs und Netz. |
| Heartbeat | prüft mit 48 parallelen Verbindungen; ein ausgeschalteter Computer kostet bis ca. 4,5 s. Bei vielen Computern ein längeres Intervall wählen, damit ein Durchlauf fertig ist, bevor der nächste beginnt. |
| Bereitstellungen | Dateien aus dem Repository laufen **durch den Docker-Host** zum Client (SMB lesen → ADMIN$ schreiben). 100 Computer × 500 MB = 50 GB Netzlast über den Host – für große Pakete 1 Gbit/s oder mehr einplanen, die Parallelität begrenzen oder winget nutzen (die Clients laden dann selbst). |
| Fernsitzungen (guacd) | je gleichzeitiger RDP-/VNC-Sitzung etwa 50 – 150 MB RAM und spürbar CPU bei Bewegung im Bild; 10 gleichzeitige Sitzungen ≈ 1 – 1,5 GB und 1 – 2 vCPU zusätzlich |

**Tipps für große Umgebungen**

- Scans mit **Scanprofilen** staffeln: das vollständige Profil z. B. wöchentlich, „Anwendungen“ täglich; „Scan-Alter“ statt fester Uhrzeit verteilt die Last.
- Zeitpläne mit **Zeitfenster** und Heartbeat statt alle Computer gleichzeitig.
- Aufbewahrung unter **Einstellungen → Zeitpläne & Leistung** anpassen (Protokoll, Scan-Protokoll, Bereitstellungen).
- Optional Ressourcen im Compose begrenzen, z. B. `mem_limit: 8g` und `cpus: 4` beim Dienst `pocketknife`.
- **Sicherung:** die automatische Sicherung (Einstellungen → Sicherung) auf ein anderes Laufwerk legen (`PK_BACKUP_PATH`) und je Sicherung mit etwa der Datenbankgröße (komprimiert meist deutlich weniger) × Anzahl Versionen rechnen.

### Automatische Sicherung

POCKET KNIFE sichert sich selbst – unter **Einstellungen → Sicherung**:

- **täglich** zur eingestellten Stunde (Standard 02:00) und **vor jedem Update** (beim ersten Start einer neuen Version, noch vor den Datenbank-Änderungen)
- **Versionen aufheben:** Standard 10; ältere Sicherungen werden automatisch gelöscht (auch manuelle und „vor Update“)
- **Jetzt sichern**, Liste mit Herunterladen und Löschen
- Inhalt je ZIP: die Datenbank (konsistente Kopie im laufenden Betrieb), `pocketknife.key` und das HTTPS-Zertifikat. Damit lassen sich auch gespeicherte Kennwörter wiederherstellen – den Sicherungsordner deshalb schützen.
- Ablage: `PK_BACKUP_PATH` aus der `.env` (Standard `<PK_DATA_PATH>-backup`, z. B. `/opt/pocketknife/data-backup`). Besser ein anderes Laufwerk oder eine eingebundene Netzwerkfreigabe; liegt der Ordner auf demselben Datenträger wie die Datenbank, weist die Seite darauf hin.

**Wiederherstellen:** `docker compose down` → im Datenverzeichnis `pocketknife.db-wal` und `pocketknife.db-shm` löschen → Inhalt der ZIP-Datei dorthin entpacken → `docker compose up -d`.


**Aufräumen der Datenbank:** Stündlich werden alte Einträge gelöscht – Protokoll (Standard 90 Tage), Scan-Protokoll (14 Tage), Bereitstellungen samt Ausgabe (180 Tage), Softwareänderungen (365 Tage), abgelaufene Sitzungen und Reste ohne Bezug. Die Fristen stehen unter **Einstellungen → Zeitpläne & Leistung**. Einmal täglich (Standard 03:00 Uhr) folgt die **Datenbankwartung**: Statistiken aktualisieren, WAL-Datei zurückschreiben und – wenn mehr als 20 % der Datei frei sind – `VACUUM`, das die Datei verkleinert. Größe, freier Anteil und letzte Wartung zeigt **Einstellungen → Wartung**; dort startet **Jetzt aufräumen** die Wartung sofort. Gelöschte Computer verlieren ihre Inventardaten sofort. Die Protokolldateien unter `/data/logs` werden bei 10 MB rotiert (höchstens 10 Dateien).

## Installation

Empfohlene Ordnerstruktur auf dem Docker-Host – die Daten liegen außerhalb der Versionsordner:

```
/opt/pocketknife/
├── data/                 Datenbank, Logs, Zertifikat (PK_DATA_PATH)
├── pocketknife-1.0/
└── pocketknife-1.1/      aktuelle Version, enthält die .env
```

```bash
cd /opt/pocketknife/pocketknife-1.1
cp .env.example .env
nano .env                 # Domäne, DCs, DNS-Server, Dienstkonto, Gruppe, Datenpfad
docker compose up -d --build
```

**Fertiges Image statt Bauen (ab 2.27.1):** Für amd64 und arm64 gibt es das Image in der GitHub Container Registry. Statt `docker compose up -d --build`:

```bash
docker login ghcr.io -u <GitHub-Benutzer>          # nur bei privater Registry; Kennwort = Token mit read:packages
echo "PK_IMAGE=ghcr.io/soleil0815/pocketknife:latest" >> .env
docker compose pull pocketknife
docker compose up -d
```

Danach aktualisiert `sh update.sh --pull` (siehe unten).

**Reproduzierbarer Build:** Basis-Image (Python-Version und Digest), Debian-Pakete (über einen festen Stand von snapshot.debian.org) und alle Python-Pakete samt Abhängigkeiten werden exakt festgeschrieben (`Dockerfile`, `requirements.lock`, `build-requirements.txt`, `docker/apt-*.txt`). Festschreiben bzw. bewusst aktualisieren mit `./tools/pin-versions.sh` (aktueller Stand) oder `./tools/pin-versions.sh --update` (neueste Versionen) – das Werkzeug baut danach zur Kontrolle neu und führt den Selbsttest aus. Ist `requirements.lock` leer, baut das Image nach `requirements.txt` und weist darauf hin.

**Ohne root (ab 2.14):** Der Dienst läuft als Benutzer `pocketknife` (UID/GID 10001, änderbar mit `PK_UID`/`PK_GID`), mit schreibgeschütztem Programmdateisystem, ohne Capabilities und mit `no-new-privileges`. Beim Start übergibt er `/data` und `/backup` einmalig an diesen Benutzer und gibt die root-Rechte dann ab. Ein eingebundener Repository-Ordner (`PK_REPO_MOUNT`) muss für UID 10001 lesbar sein.

Alle anpassbaren Werte – auch DNS-Server (`PK_DNS_1`, `PK_DNS_2`, `PK_DNS_SEARCH`), Port, Zeitzone, Datenpfad (`PK_DATA_PATH`) und Repository-Ordner (`PK_REPO_MOUNT`) – stehen in der `.env`. Die `docker-compose.yml` wird nicht bearbeitet. Ohne `PK_DNS_1` bricht `docker compose` mit einer klaren Meldung ab.

## Update auf eine neue Version

Versionen heißen `pocketknife-<Version>` (z. B. `pocketknife-2.23.3`); die Nummer steht in der Datei `VERSION`, wird in der Seitenleiste angezeigt, und die Änderungen stehen in `CHANGELOG.md`.

**Einfach (ab 2.24.1) – immer im selben Ordner:** ZIP in den Programmordner legen (z. B. `/opt/pocketknife`) und dort aufrufen:

```bash
cd /opt/pocketknife
sh update.sh pocketknife-2.24.1.zip
```

Das Skript zeigt bisherige und neue Version, fragt **„Update to POCKET KNIFE … now? [y/N]“** (die Meldungen von `update.sh` sind englisch; `j` wird als „ja“ ebenfalls angenommen) und dann:

1. ersetzt die **Programmdateien im selben Ordner** (`app/`, `tools/`, `docker/`, `Dockerfile`, `docker-compose.yml` …). `.env`, Datenbank (`data/`), Sicherungen (`data-backup/`), Repository (`repo/`) und alles andere bleiben unangetastet. Es entsteht **kein neuer Versionsordner**
2. legt die bisherigen Programmdateien nach `.pk-vorher/` – genau ein Stand, wenige MB, beim nächsten Update ersetzt. Bricht das Update mitten im Tausch ab (Strg+C, volle Platte …), legt es die bisherigen Dateien **automatisch** zurück. Bei derselben Version noch einmal oder nach einem nicht abgeschlossenen Update bleibt `.pk-vorher/` unverändert (der echte Vorgänger geht nicht verloren). Übernommen werden nur bekannte Programmdateien
3. baut das Image – schlägt das fehl, legt es die bisherigen Dateien zurück, und die bisherige Version läuft weiter
4. startet neu, wartet auf die Gesundheitsprüfung und führt den **Selbsttest** aus; startet die neue Version nicht sauber, bietet es die Rückkehr zum bisherigen Stand an
5. räumt auf (jeweils mit Nachfrage): Versionsordner früherer Updates, das ZIP, nicht mehr benutzte Docker-Images – nie einen Ordner mit Datenbank, Sicherungen oder Repository (auch nicht, wenn dort noch Dateien in `data/`, `data-backup/`, `repo/` oder eine Datenbankdatei liegen)

**Mit fertigem Image aus der Registry (ab 2.27.1):** Statt die ZIP-Datei zu holen und das Image auf dem Docker-Host zu bauen, lädt `update.sh` das fertige Image aus der GitHub Container Registry – für **amd64 und arm64**, Docker wählt das passende:

```bash
cd /opt/pocketknife
sh update.sh --pull            # neueste Version
sh update.sh --pull 2.27.1     # bestimmte Version
```

Das Skript zieht das Image, nimmt die Programm-ZIP **aus dem Image** (geprüft per SHA-256, Version von Image und ZIP müssen übereinstimmen), tauscht die Programmdateien wie beim ZIP-Weg und startet **genau dieses Image** (per Digest, ohne Bauen) – mit Gesundheitsprüfung, Selbsttest und Rückweg wie oben. Der Container läuft danach – wie andere Container – unter dem Namen aus der Registry, z. B. `ghcr.io/soleil0815/pocketknife:latest` (eingetragen als `PK_IMAGE` in der `.env`; der ZIP-Weg baut wieder lokal als `pocketknife:local`). Für den Rückweg behält es das bisherige Image als `pocketknife:previous`; zurück geht es dann ohne Bauen und ohne Internet. Läuft schon genau dieses Image, meldet es „already up to date“.

- **Registry:** Standard `ghcr.io/soleil0815/pocketknife`; eine eigene (z. B. ein Fork oder eine Spiegel-Registry) in der `.env` mit `PK_IMAGE_REPO=ghcr.io/<besitzer>/pocketknife`.
- **Private Registry:** einmal auf dem Docker-Host als der Benutzer, der das Update ausführt (meist root), anmelden: `docker login ghcr.io -u <GitHub-Benutzer>`, Kennwort = **persönliches Zugriffstoken** mit dem Recht `read:packages` (bei einem fein granularen Token: Zugriff auf das Repository, „Packages: Read“). Docker merkt sich die Anmeldung; das Token steht nicht in der `.env`.
- **Ohne Internet** oder bei einer eigenen Änderung am Code bleibt der ZIP-Weg wie bisher (baut lokal).
- Der erste Wechsel auf 2.27.1 geht mit der ZIP-Datei (das alte `update.sh` kennt `--pull` noch nicht); danach genügt `--pull`.

**Woher das Image kommt:** Bei jeder Version baut GitHub (Workflow `.github/workflows/release.yml`) das Image für amd64 und arm64, lässt den Selbsttest in beiden laufen und lädt es nur dann hoch; dazu ein Release mit der ZIP-Datei und ihrer Prüfsumme. Ausgelöst wird es mit einem Tag `v<Version>`, der zur Datei `VERSION` passen muss. Jeder Push und Pull Request baut und testet zusätzlich (`test.yml`), ohne etwas zu veröffentlichen.

**Umstieg von Versionsordnern:** Wer mit `update.sh` aus 2.23.3/2.24 aktualisiert hat, hat Unterordner wie `pocketknife-2.24/`. Läuft POCKET KNIFE noch aus so einem Ordner, übernimmt das Skript dessen `.env` in den Programmordner, startet von dort und bietet an, die Versionsordner zu löschen. Liegt das neue Skript noch nicht im Programmordner: `unzip -oj pocketknife-2.24.1.zip pocketknife/update.sh -d /opt/pocketknife`. `-y` beantwortet alle Fragen mit „ja“.

Wie bisher geht es auch von Hand:

```bash
cd /opt/pocketknife
unzip pocketknife-2.24.1.zip && mv pocketknife pocketknife-2.24.1
cd pocketknife-2.24.1
./update.sh /opt/pocketknife   # übernimmt die .env des alten Ordners, baut und startet neu
```

Da der Compose-Projektname fest `pocketknife` ist, ersetzt die neue Version den alten Container automatisch. Die Datenbank wird beim Start bei Bedarf erweitert. Vorher legt POCKET KNIFE automatisch eine Sicherung „vor Update“ an (Einstellungen → Sicherung) – damit kommt man auch nach einer Datenbank-Änderung sicher auf den alten Stand zurück. Zurück zum vorigen Stand von Hand: die Dateien aus `.pk-vorher/` in den Programmordner zurückkopieren (`.env` dort nur bei Bedarf) und `docker compose up -d --build`.

Danach `https://<docker-host>:8443` öffnen und mit einem Konto aus der Zugriffsgruppe anmelden. Beim ersten Start wird ein selbstsigniertes Zertifikat in `/data/tls/` erzeugt.

**Eigenes Zertifikat (ohne Reverse Proxy):** unter **Einstellungen → Zertifikat (HTTPS)**
- als **PFX/PKCS#12** (mit Kennwort) oder **PEM/CER + Schlüsseldatei** (auch verschlüsselt, Zwischenzertifikate optional) hochladen, oder
- eine **Zertifikatsanforderung (CSR)** erstellen, bei der eigenen CA einreichen (z. B. `certreq -submit -attrib "CertificateTemplate:WebServer" pocketknife.csr`) und das ausgestellte Zertifikat hochladen – der Schlüssel bleibt in POCKET KNIFE.

Vor dem Aktivieren wird geprüft (Schlüssel passt, Gültigkeit, Serverauthentifizierung, mind. RSA 2048, Hostname, Kette). Das neue Zertifikat ist **sofort ohne Neustart aktiv**, das bisherige wird gesichert (letzte fünf, wiederherstellbar). Alternativ weiterhin `server.crt` (mit Kette) und `server.key` in `data/tls/` ablegen und den Container neu starten.

Erste Schritte nach der Anmeldung:

1. **Einstellungen → Verbindung & Konten**: Dienstkonto eintragen („Prüfen & speichern“), dann „LDAPS testen“
2. **Einstellungen → Active Directory**: Container wählen und „Jetzt abgleichen“
3. **Einstellungen → Wartung → Verbindung testen** mit einem Client-Namen
4. **Einstellungen → Repository & winget**: Repository-Pfad eintragen und „Index jetzt laden“
5. Computer werden nun automatisch gescannt, sobald sie online sind – wann und womit, legen die **Scanprofile** fest

> **Wichtig für Passwörter in der .env:** `docker compose` ersetzt `$` in Werten durch Variablen. Enthält ein Passwort ein `$`, schreiben Sie `$$` oder setzen den Wert in einfache Anführungszeichen: `PK_SERVICE_PASSWORD='Ab$c#12'`. Ein falsch übernommenes Passwort führt sonst zu Fehlanmeldungen und im schlimmsten Fall zur Kontosperre.

## Neue Version veröffentlichen (GitHub)

Der Quellcode liegt auf GitHub; GitHub baut die Images. Ablauf für eine neue Version:

1. `VERSION` erhöhen, in `CHANGELOG.md` einen Abschnitt `## pocketknife-<Version> (Datum)` anlegen und in `app/news.json` die Neuigkeiten der Version (kurz, Deutsch und Englisch; Art `security`/`new`/`improved`/`fixed`) – fehlt eins davon, bricht der Bau ab.
2. Committen und pushen – `Test` baut das Image (amd64) und führt den Selbsttest darin aus.
3. Tag setzen: `git tag v<Version> && git push origin v<Version>`. `Release` baut dann für **amd64 und arm64**, führt den Selbsttest in beiden aus (arm64 emuliert, dauert länger), lädt das Image als `ghcr.io/<besitzer>/pocketknife:<Version>` und `:latest` hoch und legt ein Release mit `pocketknife-<Version>.zip`, Prüfsumme und Image-Digest an. Schlägt ein Schritt fehl, wird nichts veröffentlicht.

Bei einem privaten Repository ist auch das Image privat; die Docker-Hosts melden sich einmal mit `docker login ghcr.io` an (Token mit `read:packages`).

## Umgebungsvariablen

| Variable | Bedeutung | Standard |
|---|---|---|
| `PK_DOMAIN` | DNS-Name der Domäne | – |
| `PK_NETBIOS` | NetBIOS-Name (für `DOMÄNE\konto` bei WinRM/SMB) | – |
| `PK_LDAP_SERVERS` | DCs, kommagetrennt | `PK_DOMAIN` |
| `PK_LDAP_PORT` | LDAPS-Port | `636` |
| `PK_BASE_DN` | Such-Basis | aus Domäne |
| `PK_COMPUTER_BASE_DN` | Startwert für die Such-Basen (ab 2.2 unter Einstellungen → Active Directory pflegbar) | `PK_BASE_DN` |
| `PK_ACCESS_GROUP` | Zugriffsgruppe (Name oder DN) | `POCKETKNIFE-Admins` |
| `PK_SERVICE_USER` / `PK_SERVICE_PASSWORD` | Dienstkonto (`*_FILE` für Docker-Secrets). **Ab 2.2 optional** – besser unter Einstellungen → „Dienstkonto & Repository-Konto“ hinterlegen | – |
| `PK_WINRM_AUTH` | Anmeldeverfahren WinRM/SMB: `ntlm`, `negotiate`, `kerberos`, `credssp` (in der Oberfläche überschreibbar) | `ntlm` |
| `PK_LDAP_CA_FILE` | CA der Domäne (PEM) für eine vollständige Prüfung des LDAPS-Zertifikats, z. B. `/data/ca.pem` | leer = Zertifikat beim ersten Kontakt merken |
| `PK_WINRM_CA_FILE` | CA (PEM) zur Prüfung der Client-Zertifikate bei WinRM über HTTPS, z. B. `/data/winrm-ca.pem` | leer = nicht prüfen |
| `PK_KDC` | Domänencontroller für Kerberos (kommagetrennt), falls DNS-SRV nicht genügt | DNS |
| `PK_KERBEROS_REALM` | Kerberos-Realm, falls abweichend von `PK_DOMAIN` | `PK_DOMAIN` in Großbuchstaben |
| `PK_IMAGE_REPO` | Registry für `update.sh --pull` | `ghcr.io/soleil0815/pocketknife` |
| `PK_WINRM_HTTPS` / `PK_WINRM_PORT` | WinRM über HTTPS / Port | `false` / `5985` |
| `PK_REPO_USER` / `PK_REPO_PASSWORD` | Konto für die Repository-Freigabe | Dienstkonto |
| `PK_HTTPS` / `PK_PORT` | Weboberfläche per HTTPS / Port | `true` / `8443` |
| `PK_PUBLIC_HOSTNAME` | Name im selbstsignierten Zertifikat und Vorgabe für die Zertifikatsanforderung | `pocketknife` |
| `PK_SESSION_HOURS` | Höchstdauer einer Sitzung (zusätzlich Abmeldung bei Inaktivität, in der Oberfläche einstellbar) | `10` |
| `PK_UID` / `PK_GID` | Benutzer-/Gruppen-ID, unter der der Dienst im Container läuft (Besitzer von `/data` und `/backup`) | `10001` |
| `PK_DNS_1` / `PK_DNS_2` / `PK_DNS_SEARCH` | DNS-Server und Suchdomäne für den Container | – / `PK_DNS_1` / `PK_DOMAIN` |
| `PK_DATA_PATH` | Datenverzeichnis auf dem Host | `./data` |
| `PK_REPO_MOUNT` | Host-Ordner, der als `/repo` eingebunden wird | `./repo` |
| `PK_BACKUP_PATH` | Host-Ordner für die automatischen Sicherungen (`/backup` im Container) | `<PK_DATA_PATH>-backup` |
| `PK_TIMEZONE` | Zeitzone | `Europe/Berlin` |
| `PK_TRUSTED_PROXIES` | IPs eines Reverse-Proxys (X-Forwarded-For) | `127.0.0.1` |
| `PK_GUACD_HOST` / `PK_GUACD_PORT` | guacd für Fernsitzungen (Image-Version ab 2.14.2 fest mit Digest in der `docker-compose.yml`) | `guacd` / `4822` |
| `PK_BREAKGLASS_USER` / `_PASSWORD` | Optionaler Notfallzugang ohne AD | aus |

Zeitpläne, Parallelität, Aufbewahrung, LDAP-Filter, Repository-Pfad und winget-Optionen werden in der Oberfläche unter **Einstellungen** gepflegt und in der Datenbank gespeichert.

## Pakete

Pakete → **Neues Paket** (winget- und Datei-Pakete sind ein gemeinsamer Dialog). Links steht der Aufbau, rechts die Einstellungen des gewählten Eintrags:

```
Paket-Eigenschaften   Name, Version, Mandant, Ausführen als, Zeitlimit, Neustart am Ende, danach scannen
Bedingungen           gelten für das ganze Paket
Vorbereitung          z. B. Programm beenden, alte Version entfernen
Schritte              Installieren, Befehl, PowerShell, verschachteltes Paket, Dateikopie, Registry, Scannen, Neustart, Warten, Nachricht, Abmelden
Abschluss             läuft immer – auch nach einem Fehler (z. B. Aufräumen)
```

Schritte lassen sich per Ziehen, ↑/↓ oder Rechtsklick sortieren, duplizieren, deaktivieren und zwischen den Abschnitten verschieben.

| Schritt | Was passiert |
|---|---|
| Installieren | **Datei aus dem Repository** (MSI, MSP, MSU, EXE, CMD/BAT, PS1, REG – der Ordner wird kopiert; `.reg` per `reg.exe import`) oder **winget** (leere Version = immer die neueste). Bei einer MSI liest POCKET KNIFE beim Auswählen ProductName, Version, Hersteller, Produktcode und Plattform und füllt Name, Version, Erkennung und die Architektur-Bedingung vor |
| Deinstallieren | sucht beim Ausführen live in „Programme und Features“ nach dem Anzeigenamen (Platzhalter `*`, optional Version) und deinstalliert still – MSI-Produktcode, Silent-Befehl des Herstellers, bekannte Uninstaller oder winget; Benutzer-Installationen als der jeweilige Benutzer. Schnell angelegt per Rechtsklick auf ein Programm → „Deinstallationspaket erstellen …“ |
| Befehl / PowerShell | mehrzeilig, eigene Erfolgs-Exitcodes |
| Verschachteltes Paket | die Schritte eines anderen Pakets an dieser Stelle (bis 5 Ebenen, Schleifen werden erkannt) |
| Dateikopie | Datei oder Ordner aus dem Repository nach z. B. `%ProgramData%\Firma` (Robocopy, wahlweise ohne Überschreiben) |
| Registry | Werte setzen (alle gängigen Typen), Werte/Schlüssel löschen, Schlüssel anlegen; 64- oder 32-Bit-Ansicht; „Aus .reg importieren“ übernimmt eine exportierte .reg-Datei als Einträge |
| Scannen | Inventar-Scan mit einem Scanprofil |
| Neustart | startet neu, wartet bis der Computer wieder erreichbar ist, und macht dann weiter |
| Warten / Nachricht / Abmelden | Pause, Meldung an alle Sitzungen (`msg *`), Benutzer abmelden |

**Computername & Co. in Paketen:** Die Tool-Variablen `$(Computer)` usw. gelten nur für Tools. In Paketschritten (Befehl, PowerShell, Pfade) nimmt man die Windows-Umgebungsvariablen des Clients: `%COMPUTERNAME%`, `%USERDOMAIN%`, `%ProgramData%` im Befehl bzw. `$env:COMPUTERNAME` in PowerShell. Achtung: `%USERNAME%` / `$env:USERNAME` ist das **ausführende** Konto (Dienstkonto bzw. `SYSTEM`, Computerkonto `PC-0815$`), nicht der angemeldete Benutzer – außer bei *Ausführen als: Angemeldeter Benutzer*. Den angemeldeten Benutzer liefert in PowerShell `(Get-CimInstance Win32_ComputerSystem).UserName`.

**Bedingungen** (Paket und jeder Schritt): Betriebssystem (Windows 10/11, Server 2016–2025), Architektur (x86, x64, ARM64) – standardmäßig ist **alles ausgewählt** und wird nur bei Bedarf abgewählt –, PowerShell-Mindestversion, Anmeldestatus, Datei (vorhanden/fehlt/Version), Registry (vorhanden/fehlt/Wert/Version) und Sammlungsmitgliedschaft. Nicht erfüllt = Paket bzw. Schritt wird übersprungen.

**Ausführen als**: Standard ist das **Dienstkonto (Deploy-Benutzer)** – der Schritt läuft direkt als Prozess des Dienstkontos mit vollen Administratorrechten; auf dem Client wird kein Kennwort gespeichert (bis 2.13 lief er über eine geplante Aufgabe mit hinterlegtem Kennwort – solche Reste entfernt der nächste Scan). Netzwerkfreigaben sind aus dem Schritt heraus nicht erreichbar (keine Anmeldedaten auf dem Client); Dateien aus dem Repository kopiert POCKET KNIFE ohnehin vorher. Braucht ein eigener Befehl eine Freigabe, „Lokales System“ wählen und dem Computerkonto (z. B. „Domänencomputer“) Leserecht geben. Optional **Lokales System** oder **Angemeldeter Benutzer**, je Paket und je Schritt. winget-Schritte laufen automatisch als System, weil der App-Installer für das Dienstkonto meist nicht registriert ist. Pakete aus älteren Versionen behalten „Lokales System“, damit sie sich wie bisher verhalten.

**winget auf Servern**: Vor jedem winget-Schritt prüft POCKET KNIFE, ob winget vorhanden ist. Fehlt es (Windows Server, ältere Windows-10-Builds), wird der App-Installer samt Abhängigkeiten nachinstalliert – offline aus einem Repository-Ordner (Einstellungen → winget → Offline-Quelle; dort `Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle`, `*_License1.xml` und `DesktopAppInstaller_Dependencies.zip` aus dem [GitHub-Release](https://github.com/microsoft/winget-cli/releases) ablegen) oder der Client lädt die Dateien selbst von github.com. Vor Windows 10 1809 / Server 2019 (z. B. Server 2016) gibt es kein winget; dort werden winget-Schritte übersprungen. Das Tool „winget prüfen / nachinstallieren“ erledigt das auch ohne Paket.

**Danach scannen**: Nach jeder Installation oder Deinstallation wird der Computer mit dem Profil „Anwendungen“ neu inventarisiert (im Paket änderbar, auch „Nicht scannen“).

**Wird ausgeführt** zeigt für jeden Schritt den tatsächlichen Aufruf auf dem Client, z. B.

```
Direkt als Dienstkonto FIRMA\svc-pocketknife (eigener Prozess, keine geplante Aufgabe):
msiexec.exe /i "C:\Windows\Temp\PocketKnife\<Lauf>\7z2408-x64.msi" /qn /norestart ALLUSERS=1 /l*v "C:\Windows\Temp\PocketKnife\<Lauf>\pk_msi.log"
```

`<Lauf>` ist der Arbeitsordner der Bereitstellung (`<Bereitstellung>_<Ziel>_<Schritt>`), der danach gelöscht wird. In der Bereitstellung zeigt „Details“ je Computer das Ergebnis jedes Schritts mit Exitcode und die vollständige Ausgabe.

Repository-Aufbau:

```
\\fileserver\software$
├── 7zip\24.08\7z2408-x64.msi
├── Greenshot\1.3\Greenshot-INSTALLER-1.3.exe
└── Firmen-Tools\2.0\install.ps1  (+ beliebige weitere Dateien)
```

- **MSI/MSP/MSU**: `/qn` bzw. `/quiet` und `/norestart` setzt POCKET KNIFE selbst; ohne eigenes `/log` wird ein MSI-Protokoll geschrieben, dessen Fehlerzeilen bei einem Fehlschlag in der Ausgabe stehen.
- **Erfolgs-Exitcodes**: Standard `0,3010,1641` bei Installationen, `0` bei Befehlen (3010/1641 = Neustart erforderlich).
- **Erkennung**: Ist die Software (Anzeigename-Muster wie `7-Zip*`) in dieser Version oder neuer installiert, wird der Schritt übersprungen.

## Zeitpläne

Navigation → **Zeitpläne** – hier liegt alles, was automatisch verteilt oder aktualisiert wird (die früheren „Regeln“ wurden beim Update auf 2.5 automatisch in Zeitpläne umgewandelt):

- **Art**: „Pakete verteilen“ (mehrere Pakete, je Computer nacheinander in fester Reihenfolge) oder „winget-Updates“ (veraltete Software per `winget upgrade`, alle oder bestimmte IDs, Ausnahmen, Bereich; je Programm eine Bereitstellung)
- **Auslöser**: Einmalig, Intervall, Täglich, Wöchentlich, Monatlich, **Heartbeat** (jedes Ziel, sobald es erreichbar ist und noch nicht versorgt wurde – nach dem Einschalten, bei neuen Zeitplänen oder neuen Sammlungsmitgliedern beim nächsten Heartbeat)
- **Zeitfenster** (optional): Wochentage und Uhrzeit, auch über Mitternacht; fällige Läufe außerhalb werden zu Beginn des Fensters nachgeholt, online kommende Computer vorgemerkt. „Jetzt ausführen“ ignoriert das Fenster.
- **Ziele**: Sammlungen (bei jedem Lauf neu ermittelt, auch „Alle Computer“) und einzelne Computer
- **Optionen**: offline Computer vormerken und beim Heartbeat versorgen (oder überspringen); Wartezeit nach Fehlschlag; **fehlgeschlagene nachholen** (ab 2.14 Standard: fehlgeschlagene Computer werden nach der Wartezeit, mindestens 1 Stunde, automatisch erneut versorgt – höchstens „Max. Fehlversuche“ Mal, bei 0 dreimal; ein Lauf, der gar nicht starten konnte, wird alle 15 Minuten wiederholt); bei Paketen zusätzlich: bei Fehler die weiteren Pakete auslassen, erfolgreiche Ziele nicht erneut versorgen bzw. **erneut versorgen nach X Stunden**, maximale Fehlversuche
- **Vorschau**: was ein Lauf jetzt tun würde (bei winget: welche Programme auf welchen Computern)
- **Zielverlauf**: Status, letzter Lauf und Erfolg, Fehlversuche je Computer; „Auswahl erneut versorgen“ und „Verlauf zurücksetzen“

## Mandanten

Unter **Einstellungen → Mandanten** (z. B. Kunden oder Standorte). Pakete und Zeitpläne lassen sich einem Mandanten zuordnen, die Paketliste danach filtern. Ist dem Mandanten eine Sammlung zugeordnet, werden seine Pakete nur auf deren Computer verteilt (andere Ziele werden ausgelassen und gemeldet). Darf ein Mandant eigene Pakete bauen, bekommt er automatisch den Repository-Ordner `Mandanten\<Name>` (oder einen eigenen); seine OU-Admins sehen und bearbeiten im Repository nur diesen Ordner, optional mit Speicherkontingent. Alle dürfen alle Pakete verteilen, ändern nur die eigenen; dieselbe Software gibt es nur einmal (Duplikatsperre über winget-ID, MSI-Produktcode, SHA-256).

## Aktionen auf den Clients

| Aktion | Wo | Hinweis |
|---|---|---|
| Software deinstallieren | Computer → Anwendungen (einzeln oder Auswahl), Software → Programm → Auswahl | Stiller Befehl wird automatisch ermittelt und kann vorher geändert werden. Benutzerbezogene Installationen (HKEY_USERS) lassen sich als SYSTEM nicht immer entfernen |
| Dienst starten / stoppen / neu starten / Starttyp | Computer → Dienste | Stoppen beendet auch abhängige Dienste |
| Prozess beenden | Computer → Prozesse | Vorher „Live vom Computer laden“, damit die Liste aktuell ist |
| LAPS-Kennwort | Computer → Übersicht → Kennwörter & Schlüssel | Liest `msLAPS-Password` (Windows LAPS) bzw. `ms-Mcs-AdmPwd` (Legacy). Mit Verschlüsselung gespeicherte Windows-LAPS-Kennwörter können nur unter Windows entschlüsselt werden |
| BitLocker-Schlüssel (AD) | ebenda | Liest die `msFVE-RecoveryInformation`-Objekte unter dem Computerkonto |
| BitLocker vom Computer | ebenda | Liest die Wiederherstellungskennwörter direkt über WinRM (`Get-BitLockerVolume`) |

Damit das Dienstkonto LAPS-Kennwörter und BitLocker-Schlüssel lesen darf (einmalig, auf die OU der Computer):

```powershell
# Windows LAPS
Set-LapsADReadPasswordPermission -Identity "OU=Clients,DC=firma,DC=local" -AllowedPrincipals "FIRMA\svc-pocketknife"
# Legacy LAPS
Set-AdmPwdReadPasswordPermission -OrgUnit "OU=Clients,DC=firma,DC=local" -AllowedPrincipals "FIRMA\svc-pocketknife"
# BitLocker: in „Objektverwaltung zuweisen“ für msFVE-RecoveryInformation-Objekte „Lesen“ + „Alle Eigenschaften lesen“ vergeben
```

Jeder Abruf von Kennwörtern oder Schlüsseln wird mit Benutzername protokolliert. Die Werte werden nicht gespeichert.

## Tools

Computer auswählen (oder einen Computer öffnen) → **Tools ▾** → Tool wählen. Die Ausgabe erscheint je Computer.

| Typ | Wird ausgeführt | Beispiel |
|---|---|---|
| PowerShell | auf dem Client, als Dienstkonto (WinRM) oder als SYSTEM | `Get-Tpm \| Format-List` |
| Befehl (CMD) | auf dem Client, mehrzeilig wie eine .cmd-Datei | `ipconfig /flushdns` |
| URL | im Browser des Bedieners | `vnc://$(IP)`, `https://$(Host):8443` |

Variablen (werden vor dem Ausführen durch die Werte des Computers ersetzt):

| Variable | Wert | Beispiel |
|---|---|---|
| `$(Computer)` | Computername (NetBIOS) | `PC-0815` |
| `$(Host)` | vollständiger DNS-Name; falls unbekannt der Computername | `pc-0815.firma.local` |
| `$(IP)` | erste IP-Adresse aus dem letzten Scan | `192.168.10.25` |
| `$(MAC)` | erste MAC-Adresse aus dem letzten Scan (Scanner „Netzwerkkarten“) | `00:11:22:33:44:55` |
| `$(User)` | zuletzt erkannter angemeldeter Benutzer (Scan/Heartbeat) | `FIRMA\mmuster` |
| `$(Domain)` | Domäne des Computers aus dem Scan, sonst die Domäne aus den Einstellungen | `FIRMA` |

Groß-/Kleinschreibung beachten; unbekannte Werte werden durch einen leeren Text ersetzt.

Eingebaut sind Ping & Ports, Remotedesktop und VNC im Browser, Wake-on-LAN, Neustart/Herunterfahren, gpupdate, „Scannen mit Profil“ und **Speicherplatz analysieren**.

**Speicherplatz analysieren:** Ordnergrößen eines Laufwerks oder Ordners als Ringdiagramm (Mitte = aktueller Ordner, innerer Ring = Inhalt, äußerer Ring = eine Ebene tiefer) mit Liste daneben. Klick zoomt in den Ordner, Ebenen unterhalb der gewählten Tiefe werden per Klick nachgescannt. Der Client zählt mit einem kleinen C#-Helfer über WinRM (keine Installation, Junctions werden nicht verfolgt); die letzten 8 Ergebnisse je Computer werden gespeichert. Über das Ordnersymbol bzw. „Im Explorer öffnen“ geht es direkt in **Dateien**, um dort aufzuräumen.

**Wake-on-LAN über einen Relay-Computer**: Broadcasts verlassen ihr Subnetz nicht. POCKET KNIFE sucht deshalb einen gerade erreichbaren Computer (online + WinRM) im selben Subnetz wie das Ziel – ermittelt aus IP und Subnetzmaske der Netzwerkkarten des letzten Scans – und lässt ihn das Magic Packet senden (bis zu 3 Relays, Ports 7 und 9, an 255.255.255.255 und den gerichteten Broadcast des Netzes). Ist im Zielnetz keiner online, sendet ein beliebiger Online-Client einen gerichteten Broadcast (klappt, wenn der Router ihn weiterleitet). Zusätzlich sendet der Container selbst. Die Ausgabe nennt den verwendeten Relay. Voraussetzung: Ziel wurde mindestens einmal mit dem Scanner „Netzwerkkarten“ gescannt (MAC-Adresse).

**Dateien (Explorer)** (Tools ▾ bzw. beim Computer unter „Laufwerke“): verwaltet die Dateien des Clients im Browser über die administrativen Freigaben (`\\PC\C$`) – durchsuchen, herunter- und hochladen, kopieren, verschieben, umbenennen, löschen, neue Ordner, **suchen** (ab dem geöffneten Ordner in allen Unterordnern – die Suche läuft per WinRM auf dem Client selbst, auch im Constrained Language Mode). Keine Installation auf dem Client, läuft als Dienstkonto, alles wird protokolliert.

**Ereignisanzeige** (Tools ▾): liest die Windows-Ereignisprotokolle (System, Anwendung, Sicherheit, Setup, Defender) live über WinRM, mit Filter nach Stufe und Suche. Ebenfalls im Browser, ohne Installation.

**Tool-Läufe auf dem Server:** Eigene Tools laufen als Auftrag im Container weiter, auch wenn der Browser geschlossen oder der Bediener abgemeldet wird. Das Ergebnis steht im Protokoll; Bereitstellungen und Scans liefen schon immer serverseitig.

## Software-Kiosk

Unter `https://<docker-host>:8443/kiosk` installieren Nutzer freigegebene Software selbst (Anmeldung mit dem Windows-Konto). Eingerichtet wird er unter **Kiosk**:

- **Zugang:** AD-Gruppen (auch verschachtelt) und/oder OUs (mit Unter-OUs) – eins von beiden genügt; Auswahl bequem im AD-Baum („Aus dem AD wählen …“) mit Gruppensuche
- **Katalog:** vorhandene Pakete, je Paket „sofort“ oder „nach Freigabe“ (Mail an die IT), Symbol (PNG/JPEG/GIF/WebP/ICO), Kategorie, Beschreibung, optional Deinstallationspaket und „nur für Sammlung“ bzw. „nur für Gruppen“ (AD-Gruppen, auch verschachtelt)
- **Anfragen:** freigeben oder ablehnen (mit Begründung); Verlauf mit Link zur Bereitstellung
- **Sicherheit:** Nutzer sehen nur den Computer, an dem sie gerade angemeldet sind (live per WinRM geprüft), höchstens N Anfragen in 24 Stunden; Kiosk-Anmeldungen haben keinerlei Zugriff auf die Verwaltung oder Fernsitzungen
- **Neustart planen:** jetzt, in 1 Stunde oder zu einer Uhrzeit – mit Hinweis am Computer, wieder absagbar
- **Anmeldung mit Windows (SSO), optional – Standard aus:** Nutzer an Domänen-PCs werden per Kerberos automatisch angemeldet, das Formular bleibt als Rückfall. Ergibt immer nur eine Kiosk-Sitzung, nie Zugriff auf die Verwaltung. Kurz:
  1. A-Eintrag `pocketknife.firma.local` → Docker-Host (kein CNAME), HTTPS-Zertifikat auf diesen Namen
  2. Konto ohne Rechte, z. B. `svc-pk-http` („Kennwort läuft nie ab“, AES 256 aktiviert) – nicht das Dienstkonto
  3. `setspn -S HTTP/pocketknife.firma.local FIRMA\svc-pk-http`
  4. `ktpass -princ HTTP/pocketknife.firma.local@FIRMA.LOCAL -mapuser FIRMA\svc-pk-http -crypto AES256-SHA1 -ptype KRB5_NT_PRINCIPAL -pass * -out pocketknife-http.keytab`
  5. **Kiosk → Einstellungen → Windows-Anmeldung:** Keytab hochladen, einschalten, speichern, testen (die Keytab wird verschlüsselt gespeichert)
  6. GPO: `https://pocketknife.firma.local` in die Zone „Lokales Intranet“ (1), für Edge/Chrome zusätzlich `AuthServerAllowlist`, für Firefox `Authentication → SPNEGO`
  7. Verknüpfung auf `https://pocketknife.firma.local:8443/kiosk`

  Ausführlich mit Fehlersuche: Hilfe → Software-Kiosk → Anmeldung mit Windows (SSO).

## Fernsitzungen im Browser (RDP und VNC)

Computer öffnen → **Remotedesktop**, oder in der Computerliste **Tools ▾ → Remotedesktop im Browser / VNC im Browser** (mehrere Computer = mehrere Tabs). Die Sitzung läuft in einem eigenen Browser-Tab, ohne Plugin und ohne Ressourcen aus dem Internet – die Guacamole-Bibliothek (Apache 2.0) ist lokal gebündelt.

```
Browser ──WebSocket (HTTPS 8443)──► POCKET KNIFE ──TCP 4822──► pocketknife-guacd ──RDP 3389 / VNC 5900──► Client
```

| Anmeldung | Bedeutung |
|---|---|
| Mit meiner POCKET KNIFE-Anmeldung | Single Sign-on mit dem Konto, mit dem Sie sich an POCKET KNIFE angemeldet haben. Das Kennwort wird bei der Anmeldung verschlüsselt **nur im Arbeitsspeicher** gehalten (nie in Datenbank oder Log) und beim Abmelden, Ablauf der Sitzung oder Neustart verworfen. Abschaltbar unter Einstellungen → Fernsitzungen. Nicht beim Notfallzugang |
| Andere Anmeldedaten | z. B. `DOMÄNE\admin-max` oder `.\Administrator` (LAPS-Kennwort aus „Kennwörter & Schlüssel“) |
| Erst auf dem Computer anmelden | ohne Vorgabe – der Computer bzw. guacd fragt nach (bei NLA erscheint ein Anmeldedialog im Browser) |

In der Sitzung: Strg+Alt+Entf, Windows-Taste, Zwischenablage (Text in beide Richtungen), an Fenster anpassen/Originalgröße, Vollbild (Leiste blendet sich aus, Tastenkürzel wie Alt+Tab werden im Vollbild weitergereicht, sofern der Browser das unterstützt), Ton, Konsolensitzung, dynamische Auflösung beim Ändern der Fenstergröße. Unter „Weitere Einstellungen“: Auflösung, Tastaturlayout des Servers, Farbtiefe, Port, bei VNC „Nur ansehen“. **VNC-Anmeldung:** ohne Kennwort / Nachfrage vom Server, mit Kennwort oder mit dem **zentralen VNC-Kennwort** aus **Einstellungen → Fernsitzungen** (standardmäßig leer, verschlüsselt gespeichert; ist es gesetzt, wird es im Dialog vorausgewählt). Jede Sitzung wird mit Bediener, Computer und Anmeldekonto protokolliert; aktive Sitzungen lassen sich unter **Einstellungen → Fernsitzungen** beenden.

Voraussetzungen: Remotedesktop auf dem Client aktiviert, Firewall **TCP 3389** (VNC: 5900) vom Docker-Host erlaubt, das Anmeldekonto ist Administrator oder Mitglied von „Remotedesktopbenutzer“. NLA, TLS und selbstsignierte RDP-Zertifikate werden automatisch ausgehandelt. Der zusätzliche Container `pocketknife-guacd` (Image `guacamole/guacd`) wird von `docker compose up -d` bzw. `update.sh` automatisch geladen und ist nur intern erreichbar. Der Browser muss für die Seite Pop-ups erlauben.


## Scanprofile

Navigation → **Scanprofile**. Ein Profil besteht aus

- **Scannern:** beliebige der 20 Standard-Scanner (Computer, Anwendungen, CPU, Speichermodule, Laufwerke, Bildschirme, Umgebungsvariablen, Hardware, Hotfixes, Lokale Gruppen, Lokale Benutzer, Netzwerkkarten, Drucker, Prozesse, Produktschlüssel, Sicherheit, Dienste, Freigaben, Windows-Features, Geplante Aufgaben) und eigenen Scannern, dazu dem optionalen Scanner **Speicherplatz** (Ordnergrößen der Laufwerke als Diagramm; nicht im Standardscan, da er den Client stärker belastet – gezielt per Auslöser einplanen). Ein Scan ersetzt nur die Daten der Scanner, die im Profil enthalten sind – alles andere bleibt erhalten.
- **Zielen:** auf welche Computer die Auslöser wirken – „Alle Computer“, Sammlungen (z. B. „Workstations“ oder eine eigene dynamische Sammlung) und/oder einzelne Computer. Neue Profile zielen zunächst auf „Alle Computer“. Ohne Ziel läuft das Profil nur manuell.
- **Auslösern:**

| Auslöser | Bedeutung |
|---|---|
| Einmalig | zu einem festen Zeitpunkt |
| Intervall | alle n Minuten/Stunden/Tage ab Startzeit |
| Täglich | jeden n-ten Tag um hh:mm |
| Wöchentlich | an gewählten Wochentagen, jede n-te Woche |
| Monatlich | an bestimmten Tagen (z. B. 1, 15, letzter) |
| Scan-Alter | sobald der letzte Scan mit diesem Profil älter als d/h/m ist – optional nur in einem Zeitfenster (auch über Mitternacht) |
| Heartbeat | sobald ein Computer (wieder) online erkannt wird |

Zeitgesteuerte Auslöser scannen die Online-Mitglieder; mit „offline Computer nachholen“ werden die übrigen gescannt, sobald sie online kommen. Jeder Auslöser kann ein Enddatum haben. Läuft auf einem Computer schon ein Scan, wartet der nächste. Das **Standardprofil** wird nach dem Update aus dem bisherigen Scan-Intervall erzeugt (Scan-Alter + Heartbeat, Sammlung „Alle Computer“); nach Bereitstellungen wird das schlankeste Profil mit „Anwendungen“ verwendet. Unter „Scannen ▾“ (Computerliste und Computeransicht) wählt man das Profil; die Kategorie **Scans** eines Computers zeigt den Stand je Profil und den Verlauf mit Profilname.

## Dynamische Sammlungen und Zeitpläne

Beispiel für ein sich selbst pflegendes Setup:

1. **Sammlung** „7-Zip fehlt oder veraltet“: Gruppe *Eine* mit
   - Gruppe *Keine*: Anwendungen · Name · beginnt mit · `7-Zip`
   - Gruppe *Alle*: Anwendungen · Name · beginnt mit · `7-Zip` **und** Anwendungen · Version · Version kleiner als · `24.08`
2. **Paket** „7-Zip 24.08“ (MSI aus dem Repository, Erkennung `7-Zip*`)
3. **Zeitplan** „Pakete verteilen“: Paket 7-Zip, Ziel Sammlung „7-Zip fehlt oder veraltet“, Auslöser Intervall 1 Std. + Heartbeat, Zeitfenster werktags 7–18 Uhr

Neue oder zurückgekehrte Computer landen beim nächsten Scan in der Sammlung, bekommen das Paket und fallen nach dem automatischen Scan wieder heraus.

Bedingungen derselben Tabelle in einer *Alle*-Gruppe gelten für **denselben** Eintrag. „Computer ohne X“ baut man mit einer *Keine*-Gruppe – „Name enthält nicht X“ würde jeden Computer treffen, der irgendein anderes Programm hat.

### Sammlungen bedienen

- **Rechtsklick** auf eine Sammlung: Öffnen, Bearbeiten, Neu ▸ (dynamisch/statisch/Ordner), Duplizieren, Sammlung scannen, Heartbeat, Bereitstellen, Tools, Bericht, Ausschneiden/Kopieren/Einfügen, Umbenennen, Löschen. Tasten: `Strg+X`, `Strg+C`, `Strg+V`, `F2`, `Entf`.
- **Ziehen & Ablegen:** Sammlung auf einen Ordner oder eine andere Sammlung ziehen verschiebt sie, mit gedrückter `Strg`-Taste wird sie kopiert. Computer aus der Liste auf eine **statische** Sammlung ziehen fügt sie hinzu.
- Eine dynamische Sammlung *unter* einer anderen Sammlung (nicht unter einem Ordner) enthält nur Computer, die auch in der übergeordneten Sammlung sind.
- Standard- und AD-Sammlungen sind schreibgeschützt; „Duplizieren“ bzw. „Als Kopie bearbeiten“ erzeugt eine eigene, änderbare Kopie.
- **Rechtsklick auf Computer** (auch mehrere, Auswahl mit `Strg`/`Umschalt`): Details, Scannen ▸ Profil, Heartbeat, Bereitstellen, Tools ▸, Remotedesktop, Zur Sammlung ▸, Aus Sammlung entfernen, Namen kopieren, CSV, „Scannen erlaubt“ (sperrt automatische Scans für diesen Computer), Löschen.
- **Spalten:** Spaltenkopf ziehen zum Verschieben (linke Hälfte = davor, rechte = dahinter), am rechten Rand des Kopfs ziehen für die **Breite** (Doppelklick = automatisch), ▾ im Spaltenkopf zum Filtern nach Werten oder Text, „Spalten“ öffnet ein Fenster zum Ein-/Ausblenden und Umsortieren (⠿ ziehen oder ▲▼). Die Einstellungen werden je Ansicht im Browser gemerkt.

### Active Directory einrichten

Unter **Einstellungen → Active Directory**: Container **einschließen oder ausschließen** (die nächstgelegene Regel gewinnt, z. B. `Clients` einschließen, `Clients/Ausgemustert` ausschließen), Abgleich-Intervall in Minuten/Stunden/Tagen, was mit verschwundenen Computern passiert (markieren oder löschen), ob deaktivierte Computerkonten übernommen werden, sowie **AD-Sammlungen**: die OU-Struktur unter einem Eintrag mit dem Domänennamen und ein Ordner „Gruppen“ mit allen AD-Gruppen, die Computer enthalten. Diese Sammlungen pflegt der Abgleich selbst.

## Automatische winget-Updates

Zeitplan der Art **winget-Updates**, z. B. „Mittagspause“: *alle veralteten Programme, außer `Microsoft.Office`, Ziel „Workstations“, Auslöser Intervall 24 Std. + Heartbeat, Zeitfenster Mo–Fr 12:00–13:00.*
Für jedes veraltete Programm wird eine Bereitstellung `winget upgrade --id … --exact --silent` für die betroffenen **Online**-Computer erzeugt; offline Computer werden vorgemerkt und beim Heartbeat (im Zeitfenster) versorgt. Nach einem Fehlschlag wird dieselbe Kombination erst nach der eingestellten Wartezeit erneut versucht. Mit **Vorschau** sehen Sie vorab, was passieren würde.

Hinweis zum Versionsabgleich: Die installierte Version stammt aus der Windows-Registry (Programme und Features), die aktuelle aus dem winget-Index (inkl. dessen ARP-Version, wenn vorhanden). Falls ein Programm falsch zugeordnet wird, lässt sich die winget-ID unter **Software** manuell festlegen oder das Programm ignorieren.

**Ausnahmen der Aktualitätsprüfung:** eigene Liste (Software → *Ausnahmen …*, Einstellungen → Repository & winget, oder Rechtsklick auf Programme) mit Mustern nach Name, Hersteller oder winget-ID, z. B. `Microsoft Visual C++*`, `*Runtime*`, Hersteller `Datev*`. Treffer gelten nie als veraltet, erscheinen nicht in „Veraltete Software“ und werden von winget-Zeitplänen nicht aktualisiert; die Vorschau zeigt beim Anlegen die betroffenen Programme.

## E-Mail: Berichte und Benachrichtigungen

SMTP-Server unter **Einstellungen → E-Mail & Benachrichtigungen** eintragen (STARTTLS/SSL/ohne, mit oder ohne Anmeldung; Kennwort verschlüsselt) und mit **Testmail senden** prüfen.

- **Berichtsversand** (Berichte → E-Mail-Versand oder „Per E-Mail …“ in einem Bericht): einen oder mehrere Berichte mit Parametern zusammenklicken, Zeitplan wie bei Scanprofilen (einmalig, Intervall, täglich, wöchentlich, monatlich), Empfänger. Die Mail enthält jeden Bericht als Tabelle (Zeilenzahl einstellbar) und als CSV-Anhang; optional nur senden, wenn nicht alle Berichte leer sind. Vorschau und „Jetzt senden“ (auch an eine Testadresse).
- **Eigene Berichte** lassen sich ebenso verschicken und als Benachrichtigung einschalten (Mail, sobald der Bericht Zeilen liefert; jede Zeile einmal).
- **Benachrichtigungen** (jede einzeln einschaltbar, Standard aus): wenig Speicherplatz (GB/% je Laufwerk, Sammlung), Computer lange offline, Scan fehlgeschlagen, Bereitstellung fehlgeschlagen, Dienstkonto-Schutz ausgelöst, Sicherung fehlgeschlagen. Jeder Fall wird einmal gemeldet (gesammelt je Mail), optional Erinnerung nach N Stunden und Entwarnung bei Behebung. Prüfung jede Minute; höchstens 120 Mails pro Stunde.

## Kerberos statt NTLM

WinRM und SMB melden sich standardmäßig per **NTLM** an; Microsoft hat NTLM abgekündigt. Ab 2.6 enthält das Image Kerberos (MIT krb5 + gssapi), umschaltbar unter **Einstellungen → Verbindung & Konten → Anmeldeverfahren**: *NTLM*, *Negotiate* (Kerberos, sonst NTLM) oder *Nur Kerberos* – sofort wirksam, ohne Neustart. Der **Test** holt ein Ticket für das Dienstkonto, Diensttickets `HTTP/` und `cifs/` für einen gewählten Computer, meldet sich per WinRM und SMB an und zeigt, welches Verfahren der Client tatsächlich gesehen hat (`AuthenticationType`), dazu die Zeitabweichung.

Voraussetzungen: Computer mit DNS-Namen (keine IP-Adressen), Zeitabweichung Docker-Host ↔ DC unter 5 Minuten, Port 88 zu den DCs, Repository-Pfad mit DNS-Namen. KDCs per DNS-SRV oder `PK_KDC`; Realm aus `PK_DOMAIN` oder `PK_KERBEROS_REALM`; eigene `/data/krb5.conf` hat Vorrang. SPNs müssen nicht angelegt werden. Nicht betroffen: LDAPS (einfache Anmeldung über TLS). RDP über guacd nutzt weiterhin NLA/NTLM. Empfohlen: Testen → Negotiate → nach einigen Tagen Nur Kerberos (Details in der Hilfe, Abschnitt „Kerberos statt NTLM“).

## Sicherheit

- Weboberfläche nur per HTTPS, Sitzungs-Cookie `HttpOnly`, `Secure`, `SameSite=Strict`, CSRF-Header-Prüfung, strenge CSP ohne externe Quellen
- Sperre nach 8 Fehlanmeldungen je Benutzer und IP bzw. 30 je IP für 15 Minuten (Schreibweisen des Benutzernamens zählen gemeinsam)
- **Abmeldung bei Inaktivität** nach 30 Minuten ohne Maus-/Tastatureingabe (5–720 Minuten, Einstellungen → Verbindung & Konten; automatische Aktualisierungen zählen nicht, offene Fernsitzungen werden mit beendet)
- Laufende Sitzungen werden im Hintergrund alle 2 Minuten gegen das AD geprüft (deaktiviert / nicht mehr in der Zugriffs- bzw. Mandanten-Gruppe → abgemeldet, andere Gruppe → passender Bereich); Abmelden beendet auch Fernsitzungen
- LDAPS: Zertifikat je DC beim ersten Kontakt gemerkt und vor dem Senden des Kennworts verglichen; optional strikt (Standard für neue Installationen ab 2.13) oder mit `PK_LDAP_CA_FILE` gegen die CA
- Arbeitsordner `C:\Windows\Temp\PocketKnife` auf den Clients nur für SYSTEM/Administratoren; ein vorab von Benutzern angelegter Ordner wird beiseitegeschoben
- Anmeldefehler verraten nicht, ob Benutzername, Kennwort oder Gruppenmitgliedschaft das Problem war; Anfragen sind in der Größe begrenzt (Standard 2 MB)
- Sicherungen zum Herunterladen enthalten keine Schlüsseldateien; CSV-Exporte sind gegen Formel-Injektion geschützt
- Kennwörter (Dienstkonto, Repository-Konto, VNC) liegen verschlüsselt in der Datenbank, der Schlüssel getrennt in `/data/pocketknife.key`; auf den Clients wird nie ein Kennwort gespeichert
- Neuer Repository-Server bzw. neue Freigabe nur mit Bestätigung durch das Kennwort des Zugriffskontos
- Container ohne root, schreibgeschützt, ohne Capabilities; alle Versionen festgeschrieben (siehe Installation)
- WinRM über HTTP ist durch die Nachrichtenverschlüsselung von NTLM bzw. Kerberos geschützt; WinRM über HTTPS ist optional möglich
- Das Dienstkonto ist lokaler Administrator auf allen Clients – behandeln Sie es entsprechend (langes Passwort, Anmeldung an Clients interaktiv per GPO verweigern)

### Dienstkonto in der Oberfläche

Seit 2.2 wird das Dienstkonto (und optional ein eigenes Repository-Konto) unter **Einstellungen → Dienstkonto & Repository-Konto** hinterlegt. Vor dem Speichern prüft POCKET KNIFE die Anmeldung per LDAPS – ein Tippfehler führt so nicht durch automatische Zugriffe zur Kontosperre. Das Kennwort liegt verschlüsselt (Fernet/AES) in der Datenbank, der Schlüssel in `/data/pocketknife.key` (nur root). Ein in der Oberfläche hinterlegtes Konto hat Vorrang; `PK_SERVICE_PASSWORD` kann danach aus der `.env` entfernt werden. Die Anmeldung an POCKET KNIFE selbst braucht das Dienstkonto nicht.

## Daten & Sicherung

```
PK_DATA_PATH (z. B. /opt/pocketknife/data)
├── pocketknife.db     SQLite-Datenbank (WAL-Modus)
├── pocketknife.key    Schlüssel für die gespeicherten Kennwörter – immer mitsichern
├── logs/              pocketknife.log (rotierend, 10 × 10 MB)
├── tls/               server.crt / server.key, backup/ (Zertifikatssicherungen)
├── winget/index.db    winget-Katalog
└── reports/
```

Sicherung: Container kurz stoppen und `data/` kopieren – oder im laufenden Betrieb `sqlite3 data/pocketknife.db ".backup sicherung.db"`.

## Fehlersuche

**Selbsttest:** `docker compose exec pocketknife python3 tools/selftest.py` prüft mit einer temporären Datenbank, dass bereits behobene Fehler nicht zurückgekehrt sind (E-Mail/TLS, Filter, Berichte, Pfadprüfung …). Er verändert keine Daten.

**Diagnosepaket:** **Einstellungen → Wartung → Diagnosepaket** erstellt ein ZIP mit Protokoll, Fehlern, Statistik und Einstellungen der letzten 1–30 Tage zum Weitergeben – standardmäßig anonymisiert (Rechner, Benutzer, Domäne, Adressen → feste Platzhalter wie PC-001), nie mit Kennwörtern oder Schlüsseln. Platzhalter lassen sich dort wieder dem echten Namen zuordnen. Jede Zeile trägt die Version, unter der sie entstand; die Statistik trennt die laufende Version von älteren, Änderungen und behobene Fehler liegen bei. Nach einem Update am besten den Zeitraum „Seit dem letzten Update“ wählen.


| Meldung | Ursache / Lösung |
|---|---|
| `LDAPS-Verbindung … fehlgeschlagen` | DC-Namen/Port prüfen, DNS-Server in der .env (`PK_DNS_1`), LDAPS auf dem DC aktiv? |
| `Anmeldung des Dienstkontos am LDAP fehlgeschlagen` | Benutzer/Passwort in `.env` prüfen |
| `Das Dienstkonto ist im AD gesperrt` / Fehler 0xC0000234 | Konto entsperren, Passwort in der .env prüfen (siehe Hinweis zu `$`), dann unter Einstellungen „Prüfen & fortsetzen“. Die Quelle der Fehlanmeldungen zeigt das Ereignis 4740 auf dem PDC-Emulator (Feld „Aufrufername des Computers“) |
| `Kein Zugriff: … nicht Mitglied der Gruppe` | Benutzer in `PK_ACCESS_GROUP` aufnehmen, neu anmelden |
| `WinRM-Port 5985 nicht erreichbar` | WinRM-GPO, Firewall, Computer eingeschaltet? |
| `Zugriff verweigert` | Dienstkonto nicht in der lokalen Administratorengruppe |
| Kopieren schlägt fehl | Port 445 / ADMIN$-Freigabe auf dem Client, Leserecht auf der Repository-Freigabe |
| `winget nicht gefunden` | App-Installer auf dem Client installieren/aktualisieren |
| Schritt als Dienstkonto: „Prozess als Dienstkonto konnte nicht gestartet werden“ | WMI-Dienst (Winmgmt) auf dem Client prüfen; das Dienstkonto muss lokaler Administrator sein – oder den Schritt auf „Lokales System“ stellen |
| Eigener Befehl als Dienstkonto erreicht `\\server\freigabe` nicht | Ab 2.14 liegt auf dem Client kein Kennwort mehr. Dateien über einen Schritt „Datei aus dem Repository“ kopieren lassen oder als „Lokales System“ ausführen (Leserecht für das Computerkonto) |
| Container startet nicht: „Rechte konnten nicht abgegeben werden“ / Fehler mit `read_only` | Docker ab 20.10 nötig; bei Sonderfällen `read_only: true` in der `docker-compose.yml` auskommentieren |
| `/repo` nicht lesbar (Warnung beim Start) | Ab 2.14 läuft der Dienst als UID 10001: `chmod -R o+rX` auf den Repository-Ordner oder `chown -R 10001` |
| Dateien-&-Ordner-Scanner findet nichts mit `%SYSTEMDRIVE%` o. ä. | Ab 2.5.15 werden Umgebungsvariablen (`%VAR%`, `$env:VAR`) auf dem Client aufgelöst. `%USERPROFILE%`/`%APPDATA%` sind die des Dienstkontos – für alle Benutzer `%SYSTEMDRIVE%\Users\*\…`. `**` = beliebig viele Ebenen (langsamer), `*` = eine Ebene |
| Eigener Scanner zeigt Bytes statt GB | Scanner bearbeiten → **Größenangaben**: „automatisch erkennen“ greift bei Spaltennamen wie Size/Length/FreeSpace mit reinen Zahlen; sonst „nur diese Spalten“ und die Spalten eintragen. Gespeichert wird weiter der Bytewert |
| PowerShell-Scanner liefert nichts | Im Scanner-Dialog „Testen“ – Fehler stehen in der Spalte „fehler“. Der Scanner läuft im festen Profil „PowerShell“ (dort einen Auslöser setzen) oder in eigenen Profilen, nicht im Standardprofil |
| winget Exitcode `-1978335216` | Kein passender Installer (Scope/Architektur) – im Paket den Installationsbereich auf „Standard des Pakets“ stellen |

## Technik

Python 3.12 (exakte Version im `Dockerfile`), FastAPI, ldap3, pypsrp (PowerShell Remoting), smbprotocol, SQLite – Oberfläche in reinem HTML/CSS/JavaScript; einzige Fremdbibliothek ist guacamole-common-js (Apache 2.0, lokal unter `static/vendor/`) für die Fernsitzungen.

```
app/
├── main.py          Web-API
├── ad.py            LDAPS, Anmeldung, Gruppenprüfung, Computer-Abgleich
├── inventory.py     Scan-Ablauf und Speicherung (je Standard-Scanner)
├── scan_modules.ps1 die 20 Standard-Scanner (PowerShell)
├── profiles.py      Scanprofile und Auslöser
├── scanners.py      eigene Scanner
├── tools.py         Tools, Wake-on-LAN über Relay
├── remote_desktop.py Fernsitzungen: Guacamole-Tunnel (WebSocket ↔ guacd), Anmeldedaten-Tresor im RAM
├── remote.py        WinRM/PSRP, SMB, Ausführung als Dienstkonto/SYSTEM/Benutzer
├── deploy.py        Bereitstellungen
├── steps.py         Paketschritte, Bedingungen, Ablauf je Computer
├── schedules.py     Zeitpläne (Pakete und winget-Updates)
├── msi.py           MSI-Eigenschaften lesen (ohne Windows)
├── winget.py        winget-Index und Versionsabgleich
├── collections_.py  Sammlungen und Filter
├── reports.py       Berichte
├── scheduler.py     Hintergrundaufgaben
├── privileges.py    Start ohne root (Datenordner übergeben, Rechte abgeben)
├── kiosk.py         Software-Kiosk (Katalog, Anfragen, Freigabe, Neustart)
├── sso.py           Kiosk: Anmeldung mit Windows (Kerberos/SPNEGO, Keytab)
└── static/          Oberfläche (index.html, app.css, app.js, logo.svg)
```
