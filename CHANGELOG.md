# Changelog

New entries are in English. Entries up to and including 2.38.4-s1 (November 2026) are in German.

## pocketknife-2.40.0 (11.11.2026)
- **New - update status for Windows and Office:** every installation is compared with the newest state of its own Windows build or Office channel - no fixed version or channel. Windows: build and patch level (UBR) from the scan against the patched builds Microsoft published on the last patch days (MSRC security update guide, fetched by the server once a day, optional) and the newest state in the own environment. Office Click-to-Run (Microsoft 365 Apps, LTSC 2024/2021, 2024/2021, Visio/Project): version and update channel from the registry against the newest version of the same channel; MSI Office from the software list. Grace period (default 7 days) before a new patch counts. Microsoft lifecycle dates give "ohne Support" (Windows 10, Windows 11 up to 23H2/24H2 Home/Pro, Office 2016/2019/2021 …)
- **New - ready-made collections "Windows Updates" and "Microsoft Office":** Workstations → Windows 11/10 → per version, Server → per version, each with "Aktuell"/"Veraltet", plus "Ohne Support" and "Neustart ausstehend"; Office per product with "Aktuell"/"Veraltet", "Office ohne Support", "Kein Office installiert". They fill themselves from the inventory; a new version or product gets its node at the first scan; known versions are shown with (0)
- **New - fields and display:** computer columns "Windows-Update-Status", "Windows-Edition", "Office-Update-Status", table "Office-Installationen" for own collections; on the computer page under "Betriebssystem" and a new category "Office"; settings under "Inventar & Felder → Update-Status"
- **Scan:** additionally Windows edition (LTSC recognised) and Office Click-to-Run version/channel/products (registry, CLM-safe)
- **Self-test:** products and channels, lifecycle, MSRC parsing (only Microsoft's own documents), grace period, environment reference, MSI detection, collections and their members

## pocketknife-2.39.6 (11.11.2026)
- **New - "VNC" button on the computer page:** right next to "Remotedesktop" (before only under Tools → "VNC im Browser"). Opens the VNC session in the browser with the central VNC password from Settings → Fernsitzungen (stored encrypted, never sent to the browser) - e.g. for a TightVNC that is distributed as a package with this password. Same rights and log entry as Remote Desktop
- **Self-test:** button position, central password encrypted

## pocketknife-2.39.5 (11.11.2026)
- **Changed - file explorer: "Ausgeblendete Elemente" as in the Windows Explorer:** one checkbox in the toolbar (off by default, remembered by the browser) hides hidden folders and files and now also entries without access (pagefile.sys, hiberfil.sys, swapfile.sys, DumpStack.log.tmp …). Ticked, everything is shown, hidden entries dimmed. The number of hidden entries stays visible at the top right
- **Self-test:** filter including entries without access, default off, dimmed display

## pocketknife-2.39.4 (11.11.2026)
- **Fixed - interface "hangs" after double-clicking a collection:** while the collection dialog was still loading (filter schema, collections, computer list - slow with many computers), another double-click opened the same dialog again; several identical dialogs lay on top of each other, and closing one left the others (Esc each time with a "Dialog schließen?" question). Now there is one dialog per collection, further double-clicks while it loads are ignored, and the mouse pointer shows that it is loading
- **Changed:** Esc closes the collection dialog directly if nothing was changed (the question only comes after changes)
- **Changed - OU admins:** double-clicking a collection of the main admin no longer creates a copy by itself each time; right click → "Als Kopie für Ihren Bereich" does
- **Self-test:** guard while loading and open, no blocking after an error, Esc without changes, no automatic copies

## pocketknife-2.39.3 (11.11.2026)
- **Fixed - PowerShell step "Erfolgreich" despite errors:** if PowerShell reported an error (e.g. `Get-WindowsUpdate : Zugriff verweigert`) and the script still ended with exit code 0, the step counted as successful and the restart ran afterwards. Now the step fails ("PowerShell hat N Fehler gemeldet …"). Only errors PowerShell really outputs count (error records on the error stream); deliberately suppressed errors do not. New step option "Fehlermeldungen von PowerShell ignorieren – nur der Exitcode zählt" for scripts where error messages are normal
- **Changed - no restart after a failed step:** completion steps still run after an error (clean-up, scan), a restart step there no longer does
- **New - hint for Windows Update:** "Zugriff verweigert" (0x80070005) from Windows Update is named as such in the message, with the remedy depending on the account
- **Changed - tools as SYSTEM:** errors reported by PowerShell give exit code 1, like tools directly over WinRM
- **Self-test:** wrapper with PowerShell (marker only for exit code 0 with real errors), step fails, no restart, option to ignore, hint, tools

## pocketknife-2.39.2 (11.11.2026)
- **New - column "Ausgeführt als" in the step results:** "Details" of a target shows for every step the account it really ran with on the computer - Lokales System, Dienstkonto, Angemeldeter Benutzer, or "Dienstkonto (WinRM)" for what POCKET KNIFE does directly over WinRM (restart, message, log off, scan); uninstall steps with several hits on different accounts show "verschiedene". A dash: nothing ran on the computer (skipped, not run)
- **Changed:** restart, message, log off and scan steps now also name the account in the log header ("… (als Dienstkonto …)")
- **Self-test:** account in the result, no account when nothing ran, steps over WinRM, column in the interface

## pocketknife-2.39.1 (11.11.2026)
- **Fixed - retry after a package change used the old state:** if the deployment had been opened before the package was changed, "Fehlgeschlagene erneut", "Alle erneut", "Schritte wählen …" and "Schritt wiederholen" kept using the state of the deployment until the browser was reloaded (e.g. still "Dienstkonto" after switching the package to "Lokales System"). Now a changed package always counts with its current state; the state of the deployment only applies if chosen explicitly - and only for the package state it was chosen for. Retries started from other pages also take the current state
- **Changed - all settings:** a retry of individual steps now also takes restart and the other settings from the current package (before: restart from the old deployment)
- **Self-test:** default without a choice, explicit choice, slice with the current settings, no stale value in the interface

## pocketknife-2.39.0 (10.11.2026)
- **Changed - packages run as Local System by default:** the account for packages is now one setting for everyone (Settings → Repository & winget → "Ausführung der Pakete"): **Local System** (default) or the **service account**. In packages "Ausführen als" offers "Wie in den Einstellungen (…)" and "Angemeldeter Benutzer", steps additionally "Wie Paket (…)"; the brackets always name the account that really runs. Up to 2.38 the service account ran directly over WinRM (network logon) - Windows Update (e.g. PSWindowsUpdate) failed there with "Zugriff verweigert" (0x80070005). As Local System (scheduled task without a password) it works
- **New - service account with a local logon (opt-in):** if "Dienstkonto" is chosen, every step runs in a short-lived scheduled task with the service account and its password (local logon, so Windows Update works as well). The task is deleted right after the step (also on abort, leftovers by the next scan); the password goes to the client as a parameter, never in the script text. Switching asks for confirmation with a security warning (the password is on the client while the step runs; tenants with own packages are named) and is logged
- **Changed - no silent switching any more:** winget steps only offer Local System or the logged-on user (winget never ran as the service account; the editor showed "Dienstkonto" anyway). Tenant packages follow the same setting as all packages instead of being switched to Local System in the background
- **Migration:** on the first start all packages that ran as service account or Local System get "Wie in den Einstellungen", steps "Wie Paket"; "Angemeldeter Benutzer" stays, winget steps get their real account. Waiting deployments are converted too, finished ones stay as history. Packages whose own commands open a network path (`\\server\share`) are listed in the log - as Local System the computer account accesses it
- **Self-test:** model and resolution, migration (once, waiting deployments, history unchanged, network paths reported), password only as a parameter and the task removed afterwards, setting only via its own endpoint with confirmation and log, editor options

## pocketknife-2.38.11-s1 (10.11.2026)
- **Security update (patch day)** (built automatically): base image, Debian and Python packages brought up to date, rebuilt without cache, self-test and vulnerability scan (Trivy) passed. The program itself is unchanged (program state 2.38.11)
- Changed (1): `base image` previous state → new state (security updates from Debian/Python)

## pocketknife-2.38.11 (08.11.2026)
- **New - notification "Bruch im Repository":** mail when a file of a package changed or is missing (integrity check). Deployments and schedules of the package stop until a main admin clicks "Neuen Stand übernehmen" - easy to forget after replacing a file yourself. One case per package with the findings; all-clear once the new state is accepted (or the file is back). Optionally also deviations (other files in the package folder changed, deployments continue)
- **Self-test:** cases per package, deviations only on request, resolved after confirming

## pocketknife-2.38.10 (08.11.2026)
- **Security - weekly code audit:**
  - **"Test connection" on a computer only under a confirmed address:** the button still logged in with the service account (WinRM) and wrote a test file to `ADMIN$` even when the identity check had marked the computer's address as foreign (e.g. a changed dNSHostName pointing at another server). It now uses the same check as all other direct actions and says "Nicht ausgeführt – …"
  - **gpupdate /force no longer deletes recursively with Remove-Item:** the working folder under `%SystemRoot%\Temp` (writable for the logged-on user) and leftovers `pk_gp_*` were removed with `Remove-Item -Recurse`, which follows junctions in Windows PowerShell 5.1 - a user could have had files outside the folder deleted with administrator rights. Now `rd /s /q` (removes junctions themselves, never their target), only folder names POCKET KNIFE creates, never reparse points
- **Fixed - notifications under Constrained Language Mode (AppLocker/App Control):** the sending part still used .NET calls that are blocked there: every user ended up as "account not resolvable" (no delivery feedback), and the rights check of the `Programme\PocketKnife` folder silently passed. The SID now comes from the process (CIM `GetOwnerSid`), the rights check reads the SDDL text, leftover feedback is cleaned up via `Get-ItemProperty`. Remote deployment "as logged-on user": the name for "no user logged on" is only translated in full PowerShell (otherwise the SID is shown)
- **Self-test:** test connection with a foreign address, gpupdate without recursive Remove-Item, notification sending without blocked .NET calls (also in the general Constrained-Language check, which now covers gpupdate and the sending part of notifications), SDDL rights evaluation

## pocketknife-2.38.9 (07.11.2026)
- **New - copy a cell from any table:** right click on a cell → "<column> kopieren" copies the full value, also of truncated cells (e.g. the uninstall command in the software list); with several selected rows the values of all of them, one per line. Works in every table, also those without their own context menu (links, input fields and selected text keep the browser menu)
- **Self-test:** cell copy in table menus

## pocketknife-2.38.8 (07.11.2026)
- **Fixed - parallel deployments of one package failed with "being used by another process":** the SMB library opened repository files exclusively by default, so when several computers fetched the same package at once, only the first got it ("[NtStatus 0xc0000043] The process cannot access the file …"). Repository files are now opened with read sharing - any number of computers can copy the same file at the same time. If a file is being written (e.g. a new version is copied into the repository), POCKET KNIFE retries three times (2, 5, 10 s) instead of copying a half-written file
- **Improved - file explorer:** files a program on the client keeps open (e.g. log files) can now be downloaded
- **Self-test:** read sharing, retries only on sharing violations, no exclusive read of repository files

## pocketknife-2.38.7 (07.11.2026)
- **New - "Buy me a coffee":** a small button at the bottom of the navigation, visible to administrators only (not on the login page, not in the kiosk). It is a plain link to buymeacoffee.com/soleil0815 with its own icon - nothing is loaded from the internet before you click. Also in the help (License → Support), the README and as the "Sponsor" button on GitHub (`.github/FUNDING.yml`)
## pocketknife-2.38.6-s1 (07.11.2026)
- **Weekly security update** (built automatically): base image, Debian and Python packages brought up to date, rebuilt without cache, self-test and vulnerability scan (Trivy) passed. The program itself is unchanged (program state 2.38.6)
- Changed (1): `click` 8.5.0 → 8.5.1

## pocketknife-2.38.6 (06.11.2026)
- **New - schedules "winget-Updates": pick winget IDs from the inventory.** Next to "winget-IDs" and "Ausnahmen" a button "Aus dem Inventar wählen …" lists the programs installed in your environment with a sure winget assignment (name, winget ID, number of computers, outdated ones first). Selected IDs are appended to the field; IDs already listed are marked and not added twice. OU admins only see programs on their own computers
- **Self-test:** picker offers only sure, not excluded assignments, grouped case-insensitively; OU admins see only their own computers

## pocketknife-2.38.5 (06.11.2026)
- **Slimmer image:** after the build, everything only the build needs is removed - apt with its libraries and keys (via dpkg, so the security scan keeps seeing all other Debian packages), C headers and the static Python library, Tk/IDLE/turtle/2to3 of Python. `krb5-user` (`kinit`/`klist` by hand) is no longer installed; Kerberos for WinRM/SMB runs through the library as before. Fewer packages that can trigger security warnings (e.g. apt 3.0.3 on 2026-11-06)
- **Self-test:** checks in the built image that apt, kinit/klist and Tk are gone while the package database, ping and Kerberos are still there

## pocketknife-2.38.4-s2 (06.11.2026)
- **Weekly security update** (built automatically): base image, Debian and Python packages brought up to date, rebuilt without cache, self-test and vulnerability scan (Trivy) passed. The program itself is unchanged (program state 2.38.4)
- Changed (4): `base image` previous state → new state (security updates from Debian/Python), `pydantic` 2.14.2 → 2.14.3, `pydantic_core` 2.50.2 → 2.50.4, `typing-inspection` 0.4.4 → 0.4.5

## pocketknife-2.38.4-s1 (02.11.2026)
- **Wöchentliches Sicherheitsupdate** (automatisch gebaut): Basis-Image, Debian- und Python-Pakete auf den neuesten Stand gebracht, ohne Cache neu gebaut, Selbsttest und Schwachstellen-Prüfung (Trivy) bestanden. Am Programm selbst hat sich nichts geändert (Programmstand 2.38.4)
- Geändert (7): `anyio` 4.15.1 → 4.16.0, `Basis-Image` python:3.12.15-slim-trixie → python:3.12.16-slim-trixie, `opentelemetry-api` 1.45.1 → 1.45.2, `pydantic` 2.14.1 → 2.14.2, `pydantic_core` 2.50.1 → 2.50.2, `tzdata` 2026e-0+deb13u1 → 2026f-0+deb13u1, `websockets` 17.3 → 17.4

## pocketknife-2.38.4 (02.11.2026)
- **Behoben – gpupdate /force aktualisierte die Benutzerrichtlinie nicht für die angemeldeten Benutzer:** Über WinRM lief gpupdate im Dienstkonto – „Benutzerrichtlinie aktualisiert“ galt nur für dieses Konto. Benutzereinstellungen wie Browser-Lesezeichen, Laufwerkszuordnungen oder Desktop-Einstellungen kamen erst bei der nächsten Anmeldung oder Hintergrundaktualisierung an. Jetzt: Computerrichtlinie wie bisher, dazu für jeden angemeldeten Benutzer `gpupdate /target:user /force` in seiner eigenen Sitzung (kurze geplante Aufgabe, eingeschränkte Rechte, ohne Kennwort – wie Invoke-GPUpdate, ab Windows 10 1903 ohne sichtbares Fenster). Die Ausgabe zeigt Computer- und Benutzerrichtlinie je Benutzer getrennt, mit Fehlercode, falls etwas scheitert. Funktioniert auch im eingeschränkten Sprachmodus (Softwareeinschränkung/AppLocker)
- **Selbsttest:** gpupdate je angemeldetem Benutzer, im eingeschränkten Sprachmodus durchgespielt

## pocketknife-2.38.3 (01.11.2026)
- **Behoben – Auswahlkästchen in Listen:** Ein Klick auf das Kästchen einer Zeile (z. B. Computer abwählen, nachdem alle ausgewählt wurden) blieb teils wirkungslos. Ursache: Computer-Zeilen lassen sich auf Sammlungen ziehen – schon eine kleine Mausbewegung beim Klicken (Touchpad, Fernsitzung) startete ein Ziehen statt eines Klicks. Jetzt startet auf dem Kästchen kein Ziehen mehr, die ganze Kästchen-Zelle ist anklickbar, und wird außerhalb losgelassen, schaltet trotzdem nur das Kästchen um (statt nur diese Zeile auszuwählen). Zeilen ziehen geht weiter wie gewohnt über den Rest der Zeile
- **Selbsttest:** Kästchen-Zelle startet kein Ziehen und schaltet zuverlässig um

## pocketknife-2.38.2 (01.11.2026)
- **Sicherheit – Registry-Schritte:** Werte mit Namen wie `Password`, `Passwd`, `Pwd`, `Kennwort`, `Secret`, `Token`, `Credential` oder `ApiKey` stehen nicht mehr in der Ausgabe der Bereitstellung (`******** (verborgen)`). Beispiel: das TightVNC-Kennwort (REG_BINARY) ist nur mit einem öffentlich bekannten Schlüssel verschlüsselt und stand bisher als Zahlenfolge im Protokoll – sichtbar für alle, die die Bereitstellung öffnen dürfen, und so lange gespeichert wie diese. Die Zeile „gesetzt … → Password“ bleibt, nur der Inhalt fehlt
- **Selbsttest:** Werte mit solchen Namen nie in der Ausgabe (auch im eingeschränkten Sprachmodus)

## pocketknife-2.38.1 (01.11.2026)
- **Behoben – Tools als Dienstkonto bei Softwareeinschränkung:** Seit 2.38.0 laufen PowerShell-Tools als Datei aus `%TEMP%` (für den Exitcode bei `exit N`). Sperrt Softwareeinschränkung oder AppLocker Skripte dort auch für Administratoren, wäre das Tool gescheitert. Jetzt prüft ein kurzes Probeskript vorher, ob eine Datei dort laufen darf – sonst läuft das Tool wie bis 2.37 direkt (dann ohne `exit N`)
- **Selbsttest:** gesperrte Datei → Tool läuft trotzdem (direkt)

## pocketknife-2.38.0 (01.11.2026)
- **Neu – volle Funktion bei gehärteten Clients (Constrained Language Mode):** Mit Softwareeinschränkung, AppLocker-Skriptregeln oder App Control (WDAC) erlaubt PowerShell keine .NET-Aufrufe. POCKET KNIFE kommt jetzt ohne sie aus:
  - Bereitstellungen, Tools und der Rahmen auf dem Client schreiben ihre Skripte mit `Set-Content` (statt `[IO.File]`/Base64); `.cmd`-Dateien in der Codepage der Konsole
  - Registry-Schritte: dort über die Registry-Cmdlets (32-Bit-Ansicht über `WOW6432Node`); DWORD/QWORD schon in POCKET KNIFE umgerechnet
  - Bedingungen „Datei“, Dateikopie und Scanner „Dateien & Ordner“ lösen `%VARIABLEN%` und `$env:` selbst auf
  - Inventar: Programme über `Get-ItemProperty`, Mitglieder lokaler Gruppen über CIM, Architektur ohne .NET; eigene Registry- und PowerShell-Scanner (`Invoke-Expression`) laufen auch eingeschränkt
  - Prozessstatus, winget-Proxy, Windows-Benachrichtigung (Einrichtung), Kerberos-Test ohne gesperrte Aufrufe
  - Speicherplatz-Analyse braucht volle PowerShell und sagt das klar; **Verbindung testen** zeigt den Sprachmodus
- **Behoben – Tools als Dienstkonto: Exitcode:** `exit N` im PowerShell-Tool bzw. „Befehl ausführen“ beendet nur das Skript und kommt als Exitcode an, ebenso der Exitcode eines zuletzt fehlgeschlagenen Programms (vorher „0“)
- **Behoben – englische Oberfläche:** Namen von Paketen, Sammlungen, Mandanten, Scanprofilen und Computern in Auswahllisten werden nicht mehr „übersetzt“
- **Selbsttest:** keine gesperrten Aufrufe im Client-PowerShell; mit `PK_PWSH` (PowerShell 7) werden alle erzeugten Skripte geparst und die neuen Wege echt im Constrained Language Mode ausgeführt

## pocketknife-2.37.0 (01.11.2026)
Umsetzung der Vorschläge aus dem Code-Audit 2.36.3 (ohne den Umbau für den Constrained Language Mode – der kommt als eigene Version):
- **Sicherheit – OU-Admins sehen Pakete anderer Mandanten nicht mehr:** sichtbar und verteilbar sind zentrale Pakete und die des eigenen Mandanten (Liste, Auswahllisten, Export, Bereitstellen, Zeitpläne). Die Duplikatsperre nennt ein Paket eines anderen Mandanten nicht mehr beim Namen („gibt es schon bei einem anderen Mandanten – bitte den Hauptadmin, es zentral bereitzustellen“)
- **Sicherheit – Fernsteuerung:** mit mitgegebenen Anmeldedaten (eigene oder eingegebene) nur noch mit NLA; Hauptadmins bekommen auf Computern eines Mandanten mit OU-Admins vor der eigenen Anmeldung einen deutlichen Hinweis
- **Sicherheit – App-Installer (winget) nie mehr per Download auf dem Client:** POCKET KNIFE lädt das aktuelle Release von microsoft/winget-cli selbst einmal in den Repository-Ordner „Offline-Quelle“ (Standard `_winget`; Prüfsumme aus GitHub, nur HTTPS, täglich geprüft, nur bei neuer Version), die Clients installieren nur von dort. Einstellungen → winget → Offline-Quelle zeigt den Stand, **Jetzt laden** holt ihn sofort
- **Sicherheit – Bruchprüfung:** ist die Installationsdatei eines Pakets nicht lesbar, startet die Bereitstellung nicht mehr ungeprüft
- **Verbessert – Bereitstellungen:** warten nicht mehr blockierend auf denselben Computer – viele Bereitstellungen für einen PC belegen nicht mehr alle Plätze, andere Computer kommen weiter dran
- **Verbessert – Berichtsversand:** nach einem Fehler (z. B. SMTP kurz nicht erreichbar) neue Versuche nach 5, 15 und 30 Minuten
- **Verbessert – winget-Cache:** Sperre je Eintrag – ein langer Download blockiert das Entfernen anderer Einträge nicht mehr
- **Verbessert – Ausführen als angemeldeter Benutzer:** das Ergebnis ist als „vom angemeldeten Benutzer gemeldet“ gekennzeichnet (Arbeitsordner und Ergebnisdatei gehören dann dem Benutzer)
- **Behoben – Winterzeit:** Intervall-Auslöser in Minuten/Stunden setzten bei der Zeitumstellung eine Stunde aus
- **Verbessert – Leistung:** zusätzliche Datenbank-Indizes (Ziele nach Status, Bereitstellungen je Zeitplan, Zeitplan-Ziele und Sammlungsmitglieder je Computer)
- **Lieferkette:** das wöchentliche Sicherheitsupdate legt einen Pull Request an statt direkt auf main zu speichern und selbst zu veröffentlichen; Herkunftsnachweis (Attestation) für jedes Image (nur bei öffentlichem Repository – GitHub bietet ihn für private Repositorys von Einzelpersonen nicht an); täglicher Sicherheits-Scan für amd64 **und** arm64; guacd mit schreibgeschütztem Dateisystem (nur `/tmp` und das guacd-Benutzerverzeichnis im Speicher); Werte im Release-Workflow über Umgebungsvariablen; Selbsttest in der Prüfung zusätzlich auf dem Checkout (Workflows, Compose, update.sh)
- **Doku:** Abschnitt „Datenschutz“ in Hilfe und README; neutrale Beispiele in der Oberfläche (PC-0001, `\\fs01\PAKETE`)
- **Selbsttest:** alles oben (App-Installer mit nachgebautem GitHub, NLA, Sperre ohne Blockade, Wiederholungen, Winterzeit, Indizes, Workflows/Compose); bisherige Tests an die neue Sichtbarkeit angepasst

## pocketknife-2.36.3 (01.11.2026)
Ergebnis des monatlichen vollständigen Code-Audits (Stand 2.36.1):
- **Behoben – Direktaktionen nur unter bestätigter Adresse:** Dateien, Dienste, Prozesse, Ereignisanzeige, Live-Abfragen, Tools, Speicherplatz-Analyse, Fernsteuerung, Neustart/Herunterfahren/gpupdate, angemeldete Benutzer, Scanner-Test und Test-Benachrichtigung verbinden sich nicht mehr mit einem Gerät, das die Identitätsprüfung als fremd oder nicht bestätigt markiert hat (wie schon Bereitstellungen und Scans). Vorher hätte z. B. ein geänderter oder veralteter DNS-Name eines Computers das Dienstkonto (bei der Fernsteuerung die eigenen Anmeldedaten) an einen anderen Server geschickt
- **Behoben – Skript-Ausweichweg:** lädt POCKET KNIFE eine .ps1 wegen der Ausführungsrichtlinie selbst, werden im Pfad auch typografische Apostrophe (’) korrekt maskiert – ein solcher Dateiname führte zu einem Syntaxfehler
- **Behoben – Datenschutz:** Kiosk-Anfragen (Benutzername, Anzeigename, Computer) werden mit der Frist der Bereitstellungen gelöscht (offene bleiben), die gelöschter Computer beim nächsten Aufräumen
- **Behoben – OU-Admins:** Computer → Sammlungen zeigt keine Sammlungen fremder Mandanten mehr; die Scanprofil-Liste enthält nur noch eigene Computer und sichtbare Sammlungen, die Zahlen gelten nur für den eigenen Bereich
- **Behoben – Robustheit:** schlägt das Speichern einer Transaktion fehl (z. B. Datenträger voll), bleibt sie nicht mehr offen und blockiert keine weiteren Schreibzugriffe dieses Threads; der Download des winget-Index ist auf 1 GB begrenzt
- **Behoben – update.sh:** die Kopie der `.env` beim Zurückspringen ist nie, auch nicht kurz, für andere Benutzer lesbar
- **Doku:** Beispiele vereinheitlicht (`firma.local`, `FIRMA`, `\\fs01\PAKETE`, `10.0.10.x`), Aufbewahrung von Kiosk-Anfragen und Protokolldateien beschrieben
- **Selbsttest:** fünf neue Tests für diese Fixes

## pocketknife-2.36.2 (01.11.2026)
Ergebnis des wöchentlichen Code-Audits (2.32.11–2.36.1):
- **Behoben – Sperre einer winget-Version galt nicht für den Knopf „Aktualisieren“:** Er installierte per `winget upgrade` auch eine gesperrte Version (auch für OU-Admins und beim Wiederholen). Jetzt umgeht er nur die Zurückstellung; ist die neueste gesperrt, bekommt der Computer die zuletzt freigegebene Version
- **Behoben – Wechsel der Regel stellte Programme zurück:** Von „sofort freigeben“ (oder „zurückstellen“) auf „nur nach Freigabe“ galten alle seit der ersten Sichtung erschienenen Versionen plötzlich als nicht freigegeben – neue Computer hätten z. B. eine Monate alte Version bekommen, und der winget-Cache hätte sie wieder geladen. Jetzt bleiben alle bis zum Wechsel freigegebenen Versionen freigegeben
- **Behoben – Fehler beim Prüfen der Freigabe:** Ist die Freigabe nicht prüfbar (z. B. Datenbankfehler), schlägt der winget-Schritt mit Meldung fehl, statt die neueste – vielleicht gesperrte – Version zu installieren; der winget-Cache wird dann nicht benutzt. Eine freigegebene Version wird vor `winget --version` noch einmal auf gültige Zeichen geprüft
- **Behoben – update.sh startete die alte Version mitten in der Migration:** Sicherung „vor-<Version>“ und Datenbank-Umstellung laufen vor dem Start der Weboberfläche; nach etwa 3 Minuten meldete Docker „unhealthy“ und update.sh (mit `-y` ohne Rückfrage) startete die alte Version auf einer halb umgestellten Datenbank. Jetzt Startzeit des Gesundheitschecks 15 Minuten (Prüfung alle 10 s), update.sh wartet bis zu 20 Minuten
- **Behoben – update.sh ohne Internet:** Fehlt die `pocketknife-<Version>.sha256` neben dem Archiv (oder `sha256sum`), bricht es ab, statt ungeprüft zu laden (wie die Hilfe es beschreibt). Ein relativer Archivpfad gilt jetzt ab dem aktuellen Ordner (auch neben update.sh). Beim Zurück auf die alte Version behält die `.env` Besitzer und Rechte
- **Selbsttest:** Sperre und „Aktualisieren“, Wechsel der Regel, Fehler beim Prüfen, ungültige Version, Gesundheitscheck/Wartezeit, Prüfsumme Pflicht, relativer Pfad, `.env` beim Zurück

## pocketknife-2.36.1 (31.10.2026)
- **Lizenz: GNU Affero General Public License, Version 3 (AGPL-3.0-only).** Datei `LICENSE` im Repository und im Container (`/opt/pocketknife/LICENSE`), Kennzeichnung des Images (`org.opencontainers.image.licenses`), Abschnitt „Lizenz“ in Hilfe und README, Link „AGPL-3.0“ unten in der Seitenleiste und auf der Anmeldeseite
- **Sicherheit:** pyasn1 0.6.4 → 0.6.5 (vier neue Schwachstellen „Denial of Service“ beim Entschlüsseln von ASN.1-Daten, u. a. CVE-2026-119194 – betrifft die LDAP-/Kerberos-Anbindung)
- **Selbsttest:** Lizenztext unverändert, im Image enthalten, Hinweis in Hilfe und Oberfläche

## pocketknife-2.36.0 (30.10.2026)
- **Neu – neue winget-Versionen zurückstellen oder nur nach Freigabe:** Einstellungen → Repository & winget → winget → **Neue Versionen**: „sofort freigeben“ (wie bisher), „zurückstellen um … Tage“ oder „nur nach Freigabe im Paket“. Gezählt wird ab der ersten Sichtung der Version im winget-Katalog; die beim Einschalten vorgefundene Version gilt als freigegeben. Solange die neueste Version nicht freigegeben ist, aktualisieren Zeitpläne nur Computer mit einer älteren als der zuletzt freigegebenen Version und installieren genau diese, winget-Schritte in Paketen, verschachtelten Paketen und im Kiosk installieren sie per `winget --version` (nie herunterstufen; feste Versionen bleiben), und der winget-Cache lädt nur die freigegebene Version. Der Knopf „Aktualisieren“ unter Software installiert bewusst die neueste
- **Neu – Freigabe je winget-Paket:** Spalte **Freigabe** in der Paketliste; Rechtsklick → **Version … jetzt freigeben** bzw. **Freigabe …** mit allen bekannten Versionen: Freigeben, Sperren (eine fehlerhafte Version nie verteilen), Sperre aufheben, Freigabe zurücknehmen. Nach einer Freigabe lädt der winget-Cache die Version gleich; alles im Protokoll (nur Hauptadmins)
- **Selbsttest:** sofort/Tage/manuell, Ausgangsstand, Freigeben/Sperren, Schritt mit fester Version, Knopf „Aktualisieren“, Zeitpläne nur ältere Versionen, Cache behält die freigegebene Version

## pocketknife-2.35.6 (30.10.2026)
- **Verbessert – Dateien (Explorer): versteckte Einträge ausgeblendet:** Ordner und Dateien mit dem Attribut „versteckt“ (z. B. `ProgramData`, `$Recycle.Bin`, `System Volume Information`, `desktop.ini`) erscheinen wie im Windows-Explorer nicht mehr in der Liste. **Versteckte anzeigen** in der Werkzeugleiste blendet sie ein (der Browser merkt sich die Wahl); oben rechts steht die Anzahl der ausgeblendeten Einträge. Ein gezielt geöffneter Eintrag (z. B. Treffer der Suche) bleibt sichtbar, Hochladen fragt weiterhin auch bei versteckten Dateien vor dem Überschreiben
- **Selbsttest:** versteckte Einträge im Explorer

## pocketknife-2.35.5 (30.10.2026)
Ergebnis eines Code-Reviews von 2.35.1–2.35.4:
- **Behoben – „Prüfsumme des Katalogs nicht verlangen“ nur noch über HTTPS:** auch eine Weiterleitung des Herstellers muss HTTPS bleiben (Download und Abfrage des Kennzeichens). Vorher war http möglich – ohne Prüfsumme hätte dann jemand im Netz eine Datei unterschieben können
- **Behoben – Cache ersetzt keine Schritte mehr, die nicht passen:** nicht, wenn der Schritt als angemeldeter Benutzer läuft (ein Installer für alle Benutzer bräuchte Administratorrechte), nicht bei Bereich „Benutzer“ (auch ohne winget), und nie zum Herunterstufen, wenn die installierte Version nicht älter ist
- **Behoben – Wiederholen mit aktuellem Stand:** Ausschnitte (einzelne Schritte wiederholt) sind jetzt fest markiert, statt am Namen erkannt zu werden – bei langen, gekürzten Namen lief sonst das ganze Paket; ausgeschaltete Schritte bleiben ausgeschaltet
- **Behoben – Kiosk-Einträge, Zeitpläne und verschachtelte Schritte, die schon ein Cache-Paket benutzen:** die Auswahl zeigt es wieder (Bearbeiten und Speichern gingen nicht bzw. das Deinstallationspaket ging verloren); neu angeboten werden Cache-Pakete weiterhin nicht
- **Verbessert – winget-Cache:** Bruchprüfung je Cache-Paket höchstens alle 2 Minuten statt für jedes Ziel; geänderte Datei → Eintrag im Protokoll; Paket nur neu geschrieben, wenn sich etwas geändert hat (kein täglicher Hinweis „Paket geändert“); alte Versionen nicht löschen, solange eine Bereitstellung mit diesem Programm läuft; ZIP: Namen, die sich nur in der Schreibung unterscheiden, abgelehnt, Datei mit der Schreibung aus dem ZIP, halbe Ordner nach Fehlern gelöscht, Grenze entpackt 2× größte Datei; „Prüfsumme nicht verlangen“ umschalten wird auch während einer laufenden Prüfung übernommen; bei MSI ohne Katalog-Prüfsumme Produktcode aus der Datei, Version weiter die des Katalogs
- **Selbsttest:** HTTPS/Weiterleitung, Benutzer/Bereich/Herunterstufen, Ausschnitt mit gekürztem Namen und ausgeschaltetem Schritt, Schreibung im ZIP, Paket unverändert, Auswahllisten

## pocketknife-2.35.4 (30.10.2026)
- **Behoben – winget-Cache: „Nicht möglich … Im Manifest: zip“ bei manchen ZIP-Paketen (z. B. MobaXterm):** Steht im Manifest die Datei im ZIP (`NestedInstallerFiles`) auf oberster Ebene statt beim Installer, wurde sie nicht übernommen. Jetzt gelten alle Angaben der obersten Ebene für jeden Installer, wie bei winget
- **Behoben – Programme, die es nur als x86 gibt:** Im x64-Eintrag liegt dann der x86-Installer; beim Aufteilen zählt jetzt dessen Architektur – sonst wären Computer mit der installierten x86-Version ausgelassen worden. Die Spalte „Arch.“ zeigt die Architektur des Installers
- **Selbsttest:** Angaben auf oberster Ebene, x86-Installer im x64-Eintrag

## pocketknife-2.35.3 (30.10.2026)
- **Neu – winget-Cache: ZIP mit Installer:** Liegt der Installer in einem ZIP (z. B. paint.net), lädt POCKET KNIFE das ZIP, prüft seine Prüfsumme, entpackt es (nur relative Pfade mit gültigen Namen, Größe begrenzt, erst in einen Zwischenordner) und installiert die im Manifest genannte Datei mit deren Schaltern. ZIPs ohne Installer („portable“) bleiben „Nicht möglich“
- **Neu – „Prüfsumme des Katalogs nicht verlangen“ (je Eintrag, Rechtsklick):** für Hersteller, die unter derselben Adresse immer die neueste Datei liefern (z. B. Google Chrome) – dort passt die Prüfsumme im winget-Katalog oft nicht. POCKET KNIFE lädt dann über HTTPS vom Hersteller, nur wenn sich die Datei geändert hat (ETag bzw. Datum/Größe), merkt sich ihre Prüfsumme (Bruchprüfung) und liest bei MSI die Version aus der Datei. Der Fehlertext bei falscher Prüfsumme nennt die Möglichkeit
- **Selbsttest:** ZIP entpacken (auch: Pfad nach außen abgelehnt, genannte Datei fehlt, portable), Adresse ohne Version (Fehler mit Hinweis, Häkchen, gleiches ETag lädt nicht neu, neue Datei ersetzt alte)

## pocketknife-2.35.2 (30.10.2026)
- **Neu – winget-Cache auch für Computer mit winget:** Liegt ein Programm im winget-Cache und passt, installieren jetzt auch Computer mit winget aus dem Repository im LAN statt jeder für sich aus dem Internet – deutlich schneller, und der Internetzugang lädt jedes Programm nur einmal. Gilt für jeden winget-Schritt (Installieren, Aktualisieren) in Paketen, Zeitplänen, verschachtelten Paketen, Kiosk und „Aktualisieren“. Bei eigenen winget-Argumenten, Installation nur für einen Benutzer, anderer Architektur, fester anderer Version oder wenn „Aktualisieren“ ein nicht installiertes Programm träfe, bleibt es bei winget. Abschaltbar: Einstellungen des winget-Caches → „Auch mit winget“
- **Verbessert – keine doppelten Pakete:** Die erzeugten Pakete „… (winget-Cache)“ stehen nicht mehr in der Paketliste und nicht mehr zur Auswahl beim Bereitstellen, in Zeitplänen, verschachtelten Paketen und im Kiosk – das winget-Paket trägt nur die Markierung „winget-Cache“. Verwaltet wird über die Seite „winget-Cache“
- **Selbsttest:** Cache auch mit winget (Einstellung, Argumente, Benutzer, Aktualisieren nur bei installiertem Programm), Cache-Pakete nicht in Liste und Auswahl

## pocketknife-2.35.1 (30.10.2026)
- **Verbessert – Wiederholen nach einer Änderung am Paket:** Eine Bereitstellung merkt sich den Stand des Pakets beim Start; „Fehlgeschlagene erneut“, „Alle erneut“, „Schritte wählen …“ und „Schritt wiederholen“ nahmen deshalb auch nach einer Korrektur am Paket die alten Schritte – man musste das Paket neu zuweisen. Wurde das Paket seitdem geändert, zeigt die Bereitstellung jetzt einen Hinweis mit der Wahl „mit dem aktuellen Stand des Pakets“ (Standard) oder „mit dem Stand dieser Bereitstellung“. „Schritte wählen …“ zeigt dann die Schritte des geänderten Pakets (bisherige Ergebnisse über die Schritt-ID, neue Schritte ohne Ergebnis); war die Bereitstellung schon eine Wiederholung einzelner Schritte, bleibt es bei diesen Schritten aus dem aktuellen Paket
- **Verbessert – winget-Cache überall:** Jeder winget-Schritt (Installieren/Aktualisieren) auf einem Computer ohne winget läuft jetzt aus dem winget-Cache, wenn das Programm dort bereitliegt und passt – in Paketen, Zeitplänen „Pakete verteilen“, verschachtelten Paketen und im Kiosk (dort werden solche Pakete auf alten Windows wieder angeboten). Im Ergebnis steht „aus dem winget-Cache (Version)“
- **Verbessert – winget-Cache bei den Paketen:** eigener Menüpunkt unter „Pakete“, Knopf „winget-Cache …“ auf der Paketseite, Spalte „winget-Cache“ mit dem Stand, Rechtsklick „In den winget-Cache aufnehmen“ (auch für mehrere), Cache-Pakete als „winget-Cache“ gekennzeichnet; im Paket beim winget-Schritt „In den winget-Cache aufnehmen“. Der Knopf unter Software entfällt
- **Behoben – „null“ in der Oberfläche:** Über den Paket-Eigenschaften stand (seit 2.27.1) das Wort „null“, ebenso nach einem Import ohne Fehler
- **Selbsttest:** Wiederholen mit aktuellem/damaligem Stand, Ausschnitt bleibt Ausschnitt, gelöschter Schritt, unverändertes Paket, winget-Schritt aus dem Cache, Kiosk mit Cache, Menüpunkt und Paketseite, kein „null“ als Text

## pocketknife-2.35.0 (29.10.2026)
- **Neu – winget-Cache (Software → winget-Cache …):** Für Computer, auf denen winget nicht läuft (vor Windows 10 1809 / Server 2019), hält POCKET KNIFE die aktuelle Version ausgewählter Programme im Repository bereit – ohne selbst Pakete zu bauen. Programme aufnehmen per Suche im winget-Katalog oder „Aus dem Bestand …“ (installiert auf Computern ohne winget, veraltete zuerst). POCKET KNIFE lädt das Manifest wie winget selbst (Index v2: `versionData.mszyml` und Manifest, beides mit Prüfsumme), wählt den Installer (Architektur, für alle Benutzer, bevorzugte Sprache), lädt ihn, prüft die SHA-256 und legt ihn unter `winget-cache\<winget-ID>\<Version>_<Prüfsumme>\` ab. Daraus entsteht ein Paket „<Name> (winget-Cache)“ mit stillen Schaltern (aus dem Manifest, sonst die winget-Standards für MSI, Inno Setup, Nullsoft, WiX-Burn), Erfolgscodes und Erkennung. MSIX, ZIP, Store-Pakete und EXE-Installer ohne Schalter: „Nicht möglich“ mit Grund
- **Neu – immer nur die aktuelle Version:** täglich (oder „Alle prüfen“) auf neue Versionen prüfen; neue Version laden, Paket umstellen, alte löschen. Falsche Prüfsumme → nichts übernommen, die bisherige bleibt
- **Neu – Aktualisieren ohne winget:** Softwareübersicht, Computeransicht und Zeitplan „winget-Updates“ nehmen für Computer ohne winget automatisch das Paket aus dem Cache (alle anderen weiter per winget); ausgelassen werden Computer, für die das Paket nicht passt (neueres Windows verlangt, 32-Bit-Windows, andere Architektur oder nur für einen Benutzer installiert, installierte Version nicht älter); solange eine neuere Version nicht geladen ist, gilt die bisherige
- **Neu – kein Müll:** Entfernen löscht Eintrag, Paket und Ordner (wird das Paket noch verwendet, steht da wo); wird das Paket in der Paketliste gelöscht, verschwinden Eintrag und Ordner mit; „Aufräumen …“ löscht verwaiste Ordner im Cache-Ordner – nie außerhalb
- **Abhängigkeit:** PyYAML (Manifeste lesen) – fest in `requirements.lock`, Grenze in `requirements.caps`
- **Sicher aufräumen:** Cache-Ordner nur ein eigener, neuer oder leerer Ordner (nicht über die allgemeinen Einstellungen, nie das ganze Repository); Aufräumen und alte Versionen lassen alles liegen, was ein Paket oder eine geplante/laufende Bereitstellung benutzt; Prüfen und Entfernen eines Eintrags nie gleichzeitig; Reste abgebrochener Downloads werden beim Start gelöscht
- **Selbsttest:** MSZIP, Versionen als Text (1.10 bleibt 1.10), Installer-Wahl und Schalter, Laden mit nachgebautem winget-Server ins Repository, gleiche Version mit neuer Datei, neue Version ersetzt alte (nicht während einer Bereitstellung), falsche Prüfsumme (bisherige bleibt nutzbar), Aufteilen nach Windows-Version, Architektur und installierter Version, Bereitstellung aus dem Cache, Entfernen und Aufräumen (benutzte Ordner bleiben), Ordner-Einstellung, nur Hauptadmins

## pocketknife-2.34.0 (29.10.2026)
Windows Server – was dort anders ist (neues Hilfe-Kapitel „Windows Server“):
- **Neu – Rückfrage vor Neustart/Herunterfahren von Servern:** Sind Server oder Domänencontroller unter den Zielen, nennt der Dialog sie samt Zahl der angemeldeten Benutzer und führt erst nach dem Häkchen „Ja, auch die Server“ bzw. „… Domänencontroller“ aus (Rolle aus dem Scan, sonst aus dem Betriebssystem)
- **Neu – Standard-Sammlung „Domänencontroller“** und Feld **„Rolle“** (Arbeitsplatz/Server/Domänencontroller) – z. B. um DCs von Neustart-Zeitplänen auszunehmen
- **Neu – Virtualisierung erkannt:** Hyper-V, VMware, KVM/QEMU (z. B. Proxmox), Nutanix AHV, Xen, VirtualBox, Parallels, Amazon EC2, Google Cloud – aus Hersteller, Modell und BIOS (nicht aus „Hypervisor vorhanden“, das auch echte Hardware mit Hyper-V meldet). Feld **„Virtualisierung“**, in der Computeransicht unter System
- **Verbessert – Terminalserver:** Die winget-Version liest der Scan auf Servern aus der Registry statt mit `Get-AppxPackage -AllUsers` über alle Benutzerprofile (bei vielen Profilen minutenlang)
- **Verbessert – Server Core:** Ohne Explorer erkennt der Scan angemeldete Benutzer (und ihren Sitzungszustand) über `quser`
- **Behoben – Bildschirme auf VMs und Servern:** „nicht unterstützt“ ohne Bildschirm ist keine Scan-Warnung mehr
- **Verbessert – ältere Server:** keine Befehle mehr, die erst ab PowerShell 5 gehen, im Scan (Server 2012 R2 hat PowerShell 4)
- **Verbessert – Windows ohne winget** (vor Windows 10 1809 / Server 2019, also z. B. Server 2016 und 2012 R2): Veraltete Programme bleiben als „veraltet“ sichtbar (sie sind es ja), aber „Aktualisieren“ per winget wird dort nicht mehr angeboten – in der Computeransicht nicht, im Software-Dialog nicht und beim Aktualisieren über die Softwareübersicht werden solche Computer ausgelassen (Zeitpläne und Bereitstellungen ließen sie schon bisher aus bzw. meldeten „Übersprungen“). Hinweis, dass sich solche Programme mit einem eigenen Paket aktualisieren lassen. Computeransicht und Spalte „winget“: „nicht unterstützt“ statt „fehlt“. Im Kiosk erscheinen Pakete, die per winget installieren, auf diesen Computern nicht
- **Neu – Befehl ausführen … (Strg+Alt+C):** eingebautes Tool, das einen PowerShell- oder CMD-Befehl direkt auf den markierten Computern ausführt, ohne vorher ein Tool anzulegen – Ausführen als Dienstkonto oder SYSTEM, Variablen wie bei Tools, Ausgabe je Computer im selben Fenster, das zum Anpassen und erneuten Ausführen offen bleibt (Strg+Enter). **Als Tool speichern …** übernimmt den Befehl in „Neues Tool“. Verlauf der letzten 20 Befehle nur bis zum Neuladen der Seite (nicht gespeichert – ein Befehl kann ein Kennwort enthalten). Jeder Aufruf im Protokoll (Benutzer, Art, Ziele, Ergebnis – den Befehlstext nicht). Nur für Hauptadmins (wie Tools anlegen); hat ein eigenes Tool schon Strg+Alt+C, behält es das Kürzel
- **Review vor dem Release:** Kiosk erkennt alte Windows auch mit Build-Nummer samt UBR (z. B. 14393.6897), auch Deinstallieren per winget; Neustart-Rückfrage auch, wenn die Server-Prüfung fehlschlägt, und für alle Ziele; Server Core zählt den Konsolenbenutzer nicht doppelt; winget-Version nur aus installierten Paketen (nicht EndOfLife/Staged); doppelte Ziele laufen nur einmal
- **Selbsttest:** Rolle, Rückfrage im Dialog, Sammlung „Domänencontroller“, Felder, Erkennung der Virtualisierung, winget ohne Profile, Server Core, Bildschirme, Windows ohne winget, Befehl ausführen (Prüfung, nur Hauptadmins, Protokoll, Kürzel)

## pocketknife-2.33.4 (29.10.2026)
- **Behoben – Virenschutz auf Windows Server „undefined (aus)“:** Der Scanner liest den Virenschutz aus dem Windows-Sicherheitscenter (`root\SecurityCenter2`) – das gibt es nur auf Arbeitsplätzen. Auf Windows Server kam deshalb eine leere Ausgabe an, die PowerShell 5.1 als `{}` weitergibt und die Oberfläche als „undefined (aus)“ zeigte. Jetzt nimmt der Scanner auf Servern (und wenn das Sicherheitscenter nichts meldet) Microsoft Defender direkt (`Get-MpComputerStatus`; fehlt das Defender-Modul, wie auf manchen Servern, die WMI-Klasse `MSFT_MpComputerStatus` dahinter – gilt auch für die Zeile „Defender“): an = Virenschutz und Echtzeitschutz an, aktuell = Signaturen höchstens 7 Tage alt, passiver Modus wird genannt. Leere Einträge werden nie mehr gespeichert oder angezeigt. Sichtbar nach dem nächsten Scan
- **Selbsttest:** Defender auf Servern, immer eine Liste, leere Einträge fallen weg

## pocketknife-2.33.3 (29.10.2026)
- **Verbessert – Sammlungsbaum alphabetisch:** Links in der Computerliste stehen Ordner und Sammlungen je Ebene alphabetisch (Ordner zuerst, Groß-/Kleinschreibung egal, Zahlen natürlich sortiert) – bisher in der Reihenfolge, in der sie angelegt wurden. Oben bleibt es bei AD, Standard, eigene; die Standard-Sammlungen (Alle Computer, Online, Offline …) behalten ihre feste Reihenfolge
- **Selbsttest:** Sortierung des Baums

## pocketknife-2.33.2 (29.10.2026)
- **Behoben – `update.sh --cleanup` tat nichts:** Auf einem echten Docker-Host gibt `docker inspect` für einen Container ohne eingebundene Ordner (guacd) eine leere Zeile aus; daran beendete eine Schleife das Skript unter `set -e` ohne Meldung – noch bevor etwas angezeigt oder gelöscht wurde. Ebenso hätte ein fehlgeschlagenes Löschen das Skript still beendet. Alle Schleifen enden jetzt mit einem Befehl, der gelingt; Fehler beim Löschen werden genannt
- **Behoben – Release:** `.env.example` hieß bei den Dateien des Releases „default.env.example“ (GitHub erlaubt keinen Punkt am Anfang) – jetzt `env.example` (als `.env.example` speichern)
- **Selbsttest:** das nachgebaute docker gibt wie das echte leere Zeilen aus

## pocketknife-2.33.1 (29.10.2026)
- **Behoben – Umstieg von der ZIP-Installation:** Der Befehl zum Holen des neuen `update.sh` lief ohne `sudo` – ohne Docker-Rechte (oder ohne Anmeldung an der Registry) entstand eine leere `update.sh.neu`, und danach lief weiter das alte Skript (es baut wie früher vor Ort). Jetzt ein einziger Befehl als root: Image laden, Skript holen, ausführen – schlägt ein Schritt fehl, passiert nichts weiter. Das neue Skript legt sich am Ende selbst als `update.sh` ab. Ebenso bei der Installation
- **Behoben – Release:** `.env.example` fehlte bei den Dateien des Releases (`dist/*` erfasst keine Dateien mit Punkt am Anfang)
- **Selbsttest:** Umstieg mit `update.sh.neu` neben dem alten Skript; Anleitungen ohne den fehleranfälligen Zwischenschritt; `.env.example` im Release

## pocketknife-2.33.0 (29.10.2026)
Updates so, wie Docker-Nutzer es gewohnt sind – das Programm steckt komplett im Image:
- **Neu – Update mit einer Container-Verwaltung:** Dockhand, Watchtower, Tugtainer & Co. oder `docker compose pull && docker compose up -d` aktualisieren POCKET KNIFE gefahrlos – vor dem Start einer neuen Version sichert POCKET KNIFE seine Datenbank selbst (wie bisher). Empfehlung: Update-Zeit nachts
- **Neu – guacd als eigenes Image** `ghcr.io/soleil0815/pocketknife-guacd` (dieselbe offizielle, auf ihren Digest festgelegte Version, mit den Sicherheitsupdates veröffentlicht) – die `docker-compose.yml` muss sich dafür nicht mehr ändern. Festgelegt in `docker/guacd.txt`
- **Neu – Stand der docker-compose.yml:** `PK_COMPOSE_VERSION` in der Datei; ist sie älter als das Programm erwartet, zeigt POCKET KNIFE Hauptadmins einen Hinweis. Das Image bringt die passende Datei mit
- **Neu – Datenbank einer neueren Version:** Startet eine ältere Version auf einer Datenbank, die schon eine neuere benutzt hat, nennt POCKET KNIFE die Sicherung „vor-<Version>“ zum Zurückspielen (Sicherheitsupdates derselben Version zählen nicht)
- **Neu – update.sh** aktualisiert nur noch die Container: merkt sich die laufenden Images, zieht die neuen, legt eine neuere `docker-compose.yml` aus dem Image hin (die alte bleibt als `.alt`), wartet auf den Gesundheitscheck und startet bei einem Fehler die vorherigen Images wieder – ohne Download. `sudo sh update.sh <Version>` legt eine Version fest, `latest` hebt das auf. Ohne Internet: `sudo sh update.sh pocketknife-<Version>-amd64.tar.gz` (Archiv beider Images aus dem Release, Prüfsumme). Keine ZIP, kein Entpacken, kein Bauen
- **Neu – sudo sh update.sh --cleanup:** entfernt, was ältere Versionen im Ordner hinterlassen haben (Programmdateien, `.pk-vorher/`, ZIPs, Versionsordner, Ordner des früheren Update-Helfers) – nie Datenbank, Sicherungen, Repository, `.env` oder `docker-compose.yml`, auch nicht über Links
- **Entfernt – Programm-ZIP und Update-Knopf:** Releases enthalten keine Programm-ZIP mehr (dafür `docker-compose.yml`, `.env.example`, `update.sh`, Archive für ohne Internet); der Knopf „Jetzt aktualisieren“ und der Update-Helfer auf dem Host (2.32.9–2.32.12) sind ausgebaut – `update.sh` entfernt den Timer des Helfers selbst
- **Hilfe und README:** Installation und Update neu, Umstieg von einer Installation mit der Programm-ZIP Schritt für Schritt
- **Selbsttest:** update.sh (Umstieg, Version festlegen, Rückweg, neuere docker-compose.yml, ohne Internet, anderer Ordner, Timer des Helfers, --cleanup mit Links und Versionsordnern mit Daten, Selbsterneuerung), Stand der docker-compose.yml, Datenbank einer neueren Version, Release ohne ZIP

## pocketknife-2.32.12 (29.10.2026)
- **Behoben – Knopf „Jetzt aktualisieren“ fehlte (Update-Helfer: „host does not belong to root“):** Beim Start übergibt POCKET KNIFE den Datenordner dem Dienstbenutzer (chown) – seit 2.32.10 auch `data/update/host/`, den Ordner, der root gehören muss. Nach jedem Neustart lehnte der Helfer ihn deshalb ab und meldete sich nicht mehr. Jetzt lässt der Start diesen Ordner aus, und der Helfer übernimmt einen Ordner, den der Container besitzt, selbst wieder (nur der Ordner selbst wird umgestellt, sein Inhalt nur gelöscht – keinem Link wird gefolgt)
- **Selbsttest:** Start lässt `update/host` beim Übergeben aus; Helfer übernimmt einen Ordner des Containers (Links darin werden gelöscht, nie verfolgt), lehnt einen Ordner eines Dritten ab

## pocketknife-2.32.11 (29.10.2026)
- **Behoben – interner Fehler bei „Angemeldete Benutzer“ (`POST /api/computers/<id>/loggedon`, IndexError):** Waren alle Sitzungen aktiv (keine gesperrt oder getrennt), stand die Zeile `PKSTATES:` leer ganz am Ende der Ausgabe – das Auslesen scheiterte daran (seit 2.32.6). Gleiche Schwachstelle an drei weiteren Stellen beseitigt (Dateien-Explorer bei leerer Antwort, Titel eines Schritts ohne Befehl, SMTP-Test)
- **Selbsttest:** leere bzw. fehlende Zustandszeile, Schritt ohne Befehl/Skript

## pocketknife-2.32.10 (29.10.2026)
Code-Review des Updates per Knopf (2.32.9) – die Funde sind behoben:
- **Sicherheit – root schreibt nie in einen Ordner des Containers:** Der Update-Helfer schrieb Stand und Protokoll direkt nach `data/update/`, einen Ordner, den der Container verändern kann – mit einem dort angelegten Link hätte er root Dateien an anderer Stelle schreiben lassen können. Jetzt schreibt der Helfer nur in `data/update/host/`, einen Ordner, der root gehört (gehört er jemand anderem oder ist er ein Link, wird er nicht benutzt). Den Auftrag verschiebt er zuerst dorthin und liest ihn erst dann (ein Verschieben folgt keinem Link; nur eine normale Datei des Containers)
- **Sicherheit – nie zurück:** Aus der Oberfläche installiert `update.sh` nur eine wirklich neuere Version – auch bei „latest“ und unabhängig davon, was im Image steckt; ein Versions-Tag muss genau diese Version enthalten
- **Sicherheit – nur das Update:** Aus der Oberfläche löscht das Update keine alten Ordner und führt kein `docker image prune` aus (das hätte auch ungenutzte Images anderer Container entfernt)
- **Behoben – Update-Helfer unter cron/systemd:** Suchpfad (Docker in `/usr/local/bin`, z. B. Synology) und HOME (Anmeldung an der Registry von root) werden gesetzt; ein schon eingerichteter Helfer wird beim nächsten Update aufgefrischt – mit dem neuen Skript
- **Behoben – Ergebnis:** kommt jetzt als Kennwort von `update.sh` statt aus dem Protokoll (das auch Ausgaben des Containers enthält); „Image konnte nicht geladen werden“ mit Hinweis auf `sudo docker login`; ein abgebrochenes Update (Neustart des Hosts) bleibt nicht auf „läuft“ stehen; ein altes Schloss nach einem Absturz blockiert den Helfer nicht mehr
- **Behoben – Oberfläche:** „Auftrag zurücknehmen“ meldet, wenn der Helfer ihn schon abgeholt hat; der Fortschritt meldet sich nicht mehr fälschlich „zurückgenommen“ und gibt nach 15 Minuten ohne Antwort auf
- **Selbsttest:** eigener Ordner für den Helfer (Link/fremder Besitzer abgelehnt), Auftrag verschieben statt lesen, nie zurück (auch „latest“), Tag passt zur Version, kein Aufräumen aus der Oberfläche, abgebrochenes Update, altes Schloss, cron/systemd mit PATH/HOME und Auffrischen

## pocketknife-2.32.9 (28.10.2026)
- **Neu – Update per Knopf aus der Oberfläche:** Ist eine neue Version verfügbar, zeigt POCKET KNIFE Hauptadmins **Jetzt aktualisieren** (Neuigkeiten bzw. **Einstellungen → Wartung → Neuigkeiten und Updates**). Der Container bekommt dafür keinen Zugriff auf Docker: Er legt nur einen Auftrag in den Datenordner (`data/update/`), ein Helfer auf dem Docker-Host (`update.sh --watch`, alle 5 Minuten) führt `update.sh --pull <Version> -y` aus – mit Prüfsumme, Sicherung, Gesundheitsprüfung, automatischer Rückkehr zur bisherigen Version und Selbsttest – und meldet Stand und Protokoll zurück. Nichts zu aktivieren: Jedes Update mit `update.sh` richtet den Helfer gleich mit ein (systemd-Timer, sonst cron; auch Synology DSM 7) – nach dem ersten Update auf 2.32.9 von Hand geht es aus der Oberfläche. Dauerhaft abschalten mit `sudo sh update.sh --watch-remove`, von Hand einrichten mit `--watch-install`. Der Helfer nimmt nur Versionsnummern an, nie eine ältere als die laufende, und folgt keinen Links im Datenordner. **Empfehlung:** Updates nur per Knopf oder `sudo sh update.sh --pull`, keine automatischen Updates durch Watchtower, Dockhand, Tugtainer & Co. – die tauschen nur das Image (`docker-compose.yml` und guacd bleiben alt, kein automatisches Zurück); Hilfe, README und `docker-compose.yml` sagen das jetzt so
- **Selbsttest:** Auftrag nur für Hauptadmins und nur neuere Versionen, Stand/Protokoll ohne Links, Helfer lehnt ungültige/ältere Aufträge ab, Ordner als Link wird nicht benutzt, nie zwei Helfer gleichzeitig

## pocketknife-2.32.8 (28.10.2026)
- **Behoben – falsche Geschwindigkeit der Netzwerkkarte (z. B. 10 statt 1000 Mbit/s):** Der Scanner „Netzwerkkarten“ nahm den Wert aus WMI (`Win32_NetworkAdapter.Speed`), der bei manchen Treibern veraltet ist oder bei getrennten Karten 10 Mbit/s meldet. Jetzt kommt er aus `Get-NetAdapter` (fragt den Treiber direkt) und steht nur bei verbundenen Karten. Kabelgebundene Karten mit 10/100 Mbit/s bekommen einen Hinweis (Kabel, Switchport, Energiespar-Einstellung prüfen)
- **Neu – Scan erst nach dem Hochfahren:** Automatische Scans für einen gerade hochgefahrenen oder aufgewachten Computer laufen erst nach einer Wartezeit (Standard 3 Minuten, **Einstellungen → Zeitpläne & Leistung**), damit Netzwerk, Dienste und Anmeldung fertig sind. Manuelle Scans laufen sofort
- **Selbsttest:** Geschwindigkeit aus Get-NetAdapter, getrennte Karten ohne Wert, WMI nur als Rückfall (eingeschränkter Sprachmodus); Wartezeit nach dem Online-Kommen

## pocketknife-2.32.7 (28.10.2026)
- **Neu – Tools mit Dateien aus dem Repository:** Im Tool einen Ordner oder eine Datei aus dem Repository wählen (z. B. iperf). POCKET KNIFE kopiert sie vor dem Ausführen selbst auf den Computer, der Befehl läuft in diesem Ordner, danach wird aufgeräumt – als Dienstkonto und als SYSTEM. Kein Zugriff des Computers auf Freigaben nötig (ein Aufruf wie `\\server\freigabe\programm.exe` scheitert in einer WinRM-Sitzung grundsätzlich), keine Kennwörter auf dem Client. Mandanten-Ordner sind nicht wählbar – auch nicht nachträglich: Bekommt ein Mandant den Ordner später, läuft das Tool nicht mehr, und ein Mandant bekommt keinen Ordner, den ein Tool benutzt. Mit Dateien läuft ein Tool auch als Dienstkonto als eigener Prozess, damit das Zeitlimit das Programm wirklich beendet; der Ordner wird in jedem Fall aufgeräumt
- **Neu – Danach scannen:** Ein Tool kann danach scannen – *Nach Erfolg* oder *Immer*, mit wählbarem Scanprofil. Standard: nicht scannen
- **Neu – Zeitlimit und Erfolgscodes je Tool** (leer = 30 Minuten bzw. 0)
- **Behoben – Umlaute in der Ausgabe von CMD-Tools und Befehl-Schritten:** „ausf?hren“ statt „ausführen“ – die Ausgabe von cmd.exe wird jetzt in der Konsolen-Codepage gelesen
- **Hilfe und Handbuch:** Bildschirmfotos neu mit durchgehend neutralen Beispielnamen (Domäne firma.local, Computer PC-0001 …, Netz 10.0.10.x)
- **Selbsttest:** Dateien kopieren/Arbeitsordner/Aufräumen (Dienstkonto und SYSTEM), Mandanten-Ordner gesperrt, Erfolgscodes, Zeitlimit, Danach scannen (Standard aus), Konsolen-Codepage

## pocketknife-2.32.6 (28.10.2026)
- **Neu – Sitzungszustand angemeldeter Benutzer:** In der Computeransicht und in der Spalte „Benutzer“ der Computerliste steht neben jedem angemeldeten Benutzer **gesperrt** (Sperrbildschirm) oder **getrennt** (Remotedesktop geschlossen oder Benutzer gewechselt). Live beim Öffnen eines Online-Computers, sonst aus dem letzten Scan; mehrere Sitzungen eines Benutzers: die aktive zählt. Läuft auch im eingeschränkten Sprachmodus
- **Selbsttest:** Zustände aus `quser` (deutsch/englisch) und Sperrbildschirm, mehrere Sitzungen, eingeschränkter Sprachmodus, Scan und Live-Abfrage gleich

## pocketknife-2.32.5 (28.10.2026)
Code-Review aller Änderungen seit 2.29 – die Funde sind behoben:
- **Behoben – Deinstallieren-Schritt:** Wurde die Software per winget oder mit dem Befehl aus der Registry entfernt, baute POCKET KNIFE den falschen Befehl, und der Schritt schlug immer fehl
- **Behoben – Dienstkonto-Schutz:** „Konto gesperrt/deaktiviert/abgelaufen“ von einem Computer löst den Schutz jetzt sofort aus (bisher konnte ein bis zu 5 Minuten altes „Konto in Ordnung“ das verhindern – Gefahr einer Kontosperre). „Kennwort falsch“ von einem einzelnen Gerät wird weiter erst per LDAP geprüft; ist der Domänencontroller gerade nicht erreichbar, heißt das nicht mehr „Kennwort falsch“ (kein Stillstand wegen eines Geräts). „Konto in Ordnung“ gilt höchstens 60 Sekunden
- **Behoben – PowerShell-Skripte laden:** Lehnt Windows erst ein Skript ab, das das Skript selbst aufruft, wird nicht mehr nachgeladen (der Teil davor lief sonst doppelt); eine Sperre durch Softwareeinschränkung/AppLocker wird nie umgangen; ein Syntaxfehler im geladenen Inhalt ergibt Exitcode 1 statt „Erfolgreich“; `using namespace` am Anfang funktioniert
- **Behoben – Härtungscodes 9013–9016:** nur noch bei einem fehlgeschlagenen Schritt (Erfolgscodes des Schritts und winget-Meldungen wie „bereits aktuell“ gehen vor); Defender-/AppLocker-Ereignisse anderer Programme (z. B. lsass-Regel) werden nicht mehr als Grund genannt; bei einem Fehler schon beim Einrichten über WinRM passender Hinweis
- **Behoben – Tastenkürzel:** Strg+Alt+B ist jetzt die Vorgabe für Neustart/Herunterfahren (hat ein eigenes Tool schon B, bleibt es bei N). Kürzel wirken auch, wenn eine Zeile über ihr Kontrollkästchen markiert wurde; Wake-on-LAN auf mehreren Computern fragt nach; zweimal drücken startet nicht doppelt; ein umgehängtes Kürzel startet das richtige Tool; Strg+Alt+C/V/H lösen nicht zusätzlich Kopieren/Einfügen/Heartbeat aus; URL-Tools mit Rückfrage fragen auch
- **Behoben – Wake-on-LAN:** Schließen des Fensters bricht das Senden an die übrigen Computer nicht mehr ab, keine stille Grenze von 200 Computern; der zweite Versuch nutzt das einzige Relay im Zielnetz erneut statt nur des Containers; ein Relay im eingeschränkten Sprachmodus meldet, dass es nicht senden kann; Zeitpläne wecken nur wirklich ausgeschaltete Computer, auch bei der Wiederholung eines gescheiterten Termins, und je Zeitplan nur einmal gleichzeitig
- **Behoben – OU-Admins:** Die Kopie einer fremden dynamischen Sammlung übernahm deren Regeln (z. B. Computernamen anderer Mandanten) – jetzt „ist Mitglied von“; ungültige IDs/Namen ergeben eine Meldung statt eines internen Fehlers; Zeitplanliste schneller
- **Behoben – Pakete zusammenfassen:** Duplikatsperre lässt sich wie beim Speichern übersteuern (Hauptadmins)
- **Behoben – wöchentliches Sicherheitsupdate:** nur auf eine veröffentlichte Version (eine gemergte, noch nicht veröffentlichte Programmversion erschien sonst als „-s1“); eigener Ablauf, der keinen wartenden Release verwirft. update.sh vergleicht auch vierstellige Versionen richtig
- **Selbsttest:** alle Funde

## pocketknife-2.32.4 (28.10.2026)
- **Geändert – Tastenkürzel gehören zum Tool:** Statt zehn fester Plätze je Benutzer bekommt jedes Tool sein eigenes Kürzel: Strg+Alt (wahlweise + Umschalt) und ein Buchstabe, eine Ziffer oder F1–F12. Bei eigenen Tools im Tool-Dialog („Tastenkürzel“: Kombination drücken), bei eingebauten Tools unter **Tools → Tastenkürzel …** – dort mit Vorgaben (Strg+Alt+P Ping & Ports, R Remotedesktop, V VNC, W Wake-on-LAN, N Neustart/Herunterfahren, G gpupdate, D Dateien, E Ereignisanzeige) und der Übersicht aller Kürzel. Schon vergebene Kürzel werden sofort mit dem Namen des Tools angezeigt und nicht gespeichert. Gilt für alle Benutzer; die Ziffern-Kürzel aus 2.32.3 werden für eigene Tools übernommen, soweit frei
- **Selbsttest:** Schreibweise und erlaubte Kombinationen, Konflikte mit Namen (eigene und eingebaute Tools), Vorgaben, Übernahme aus 2.32.3

## pocketknife-2.32.3 (28.10.2026)
- **Neu – Tastenkürzel für Tools:** Unter **Tools → Tastenkürzel …** bis zu zehn Tools (eingebaute und eigene) auf **Strg+Alt+1 … 0** legen. Das Kürzel startet das Tool für die markierten Computer der Computerliste oder den geöffneten Computer; bei mehreren Computern fragt POCKET KNIFE vorher nach. Je Benutzer gespeichert (folgt auf jeden Rechner); in Eingabefeldern, Dialogen und Menüs aus, AltGr-Zeichen starten nie ein Tool. Menüs und die Tools-Seite zeigen das Kürzel an
- **Geändert – Neustart/Herunterfahren mit Wartezeit:** Das Tool heißt jetzt „Neustart / Herunterfahren …“ und fragt, ob neu gestartet oder heruntergefahren werden soll und nach welcher Zeit (0 = sofort bis 24 Stunden, Standard 60 Sekunden). Der Standardtext der Windows-Meldung nennt bei längeren Zeiten Minuten; ein eigener Text gilt unverändert. Bei „sofort“ schließt Windows offene Programme ohne Rückfrage
- **Selbsttest:** Tastenkürzel speichern/prüfen; Wartezeit beim Neustart/Herunterfahren (Grenzen, sofort mit /f, Meldungstext) (nur gültige Kürzel, ein Tool nur einmal, gelöschte Tools fallen weg, je Benutzer)

## pocketknife-2.32.2 (27.10.2026)
- **Behoben – „… ist nicht digital signiert“ trotz `-ExecutionPolicy Bypass`:** Legt eine GPO die Ausführungsrichtlinie fest (z. B. *MachinePolicy RemoteSigned*) und hält Windows die Datei für fremd (Sicherheitszonen), startete auf manchen Clients kein Schritt – auch Registry-Schritte und cmd-Befehle nicht, weil sie im PowerShell-Rahmen von POCKET KNIFE laufen. Jetzt startet POCKET KNIFE alle PowerShell-Skripte (Rahmen, PowerShell-Schritte, .ps1 aus dem Repository, Tools) erst normal als Datei; verweigert Windows sie, lädt es ihren Inhalt und führt ihn aus. Parameter, `$PSScriptRoot` und Exitcode bleiben gleich, das Skript läuft nie doppelt. Ohne GPO ändert sich nichts
- **Neu – gehärtetes Windows wird gemeldet:** Verhindert Windows die Ausführung trotzdem, steht statt „Exitcode 1“ ein eigener Code mit Klartext im Ergebnis: 9013 Ausführungsrichtlinie/Signatur, 9014 Virenschutz (AMSI), 9015 eingeschränkter Sprachmodus (AppLocker/WDAC), 9016 Programm gesperrt (AppLocker/Softwareeinschränkung). Läuft PowerShell im eingeschränkten Sprachmodus, steht das als Hinweis im Protokoll; startet ein Programm gar nicht, nennt das Protokoll den Grund
- **Selbsttest:** Start als Datei und Laden nach Verweigerung (mit Parametern, `$PSScriptRoot`, Exitcode, auch im eingeschränkten Sprachmodus), nie doppelt, Codes 9013–9016

## pocketknife-2.32.1 (27.10.2026)
- **Behoben – „Prozess ohne Ergebnis beendet“ nannte einen falschen Grund:** POCKET KNIFE wertete jeden Defender-ASR-Eintrag als Sperre – auch „Vorgang überwacht“ (Ereignis 1122). Steht eine Regel auf „Überwachen“, entsteht so ein Eintrag bei jedem Start über WMI; er blockiert nichts und ist nie der Grund. Jetzt zählt nur „blockiert“ (1121), die Regel steht mit Namen statt des langen Ereignistexts dabei, nur Ereignisse seit dem Start dieses Laufs. Die Fehlerausgabe von PowerShell steht immer in der Meldung; neu erkannt werden auch Virenschutz-Funde (AMSI, Ereignis 1116) und Abstürze von PowerShell
- **Selbsttest:** blockiert/überwacht unterschieden, Regelnamen, Fehlerausgabe, Virenschutz; Abfrage auf dem Client in PowerShell geparst

## pocketknife-2.32 (27.10.2026)
- **Neu – Pakete zusammenfassen:** mehrere Pakete markieren → Rechtsklick → „Zu neuem Paket zusammenfassen …“. Reihenfolge, Name, „Bei Fehler weitermachen“; **verschachtelt** (bleibt mit den Originalen verbunden, deren Bedingungen, „Ausführen als“ und Neustart gelten weiter) oder **Schritte kopieren** (unabhängig; „Ausführen als“ bleibt je Schritt; Pakete mit eigenen Bedingungen nur verschachtelt). Nie über Mandanten hinweg – zentrale Pakete gehen mit jedem
- **Geändert – Tool „gpupdate /force“:** jetzt das normale `gpupdate /force` (Computer- und Benutzerrichtlinien) statt nur `/target:computer`. Fragt gpupdate nach Abmelden oder Neustart, antwortet POCKET KNIFE „N“ – der Aufruf hängt nicht. Die Benutzerrichtlinien sind die des ausführenden Kontos (Dienstkonto), nicht die des angemeldeten Benutzers
- **Behoben – Scan nach der Bereitstellung mit dem gewählten Profil:** Bei mehreren Paketen in einer Bereitstellung (Mehrfachauswahl) und bei Zeitplänen wurde immer mit „Anwendungen“ gescannt – das im Paket unter „Danach scannen“ gewählte Profil und „Nicht scannen“ zählten nicht. Jetzt gilt das Profil jedes einzelnen Pakets (jedes Profil einmal). Außerdem fiel der Scan weg, wenn für den Computer gerade ein anderer Scan lief; er wird jetzt danach nachgeholt (der laufende hatte womöglich noch den Stand vor der Installation). Beim Zusammenfassen übernimmt das neue Paket die Profile der Einzelpakete
- **Behoben – Selbsttest nach dem Update meldete einen Fehler (Kiosk 2.15):** Der Test lief mit den Einstellungen der Instanz aus der `.env`; mit einem anderen NetBIOS-Namen als dem der Testdaten („FIRMA“) galt der Test-Benutzer als „anderer Benutzer“. POCKET KNIFE selbst war nicht betroffen. Der Selbsttest ignoriert jetzt alle `PK_…`-Einstellungen der Instanz (auch das Dienstkonto) und läuft überall gleich; die Warnung zu `fork()` ist ebenfalls weg
- **Selbsttest:** Zusammenfassen verschachtelt und als Kopie (Reihenfolge, Neustart, Bei Fehler, „Ausführen als“, Bedingungen, Mandanten), gpupdate

## pocketknife-2.31 (27.10.2026)
- **Neu – Wake-on-LAN zuverlässiger:** jedes Magic Packet mehrfach (Standard 3×), auch an die MAC-Adresse kabelgebundener Karten ohne IP beim letzten Scan; danach prüft POCKET KNIFE, ob der Computer aufwacht („aufgewacht nach 70 s“ / „nicht aufgewacht“), und sendet nach der halben Wartezeit automatisch noch einmal – über andere Relays. Optional **feste Relays** (Einstellungen → Wake-on-LAN), dort auch Anzahl und Wartezeit
- **Neu – Wake-on-LAN-Bereitschaft im Inventar:** je Netzwerkkarte Magic Packet erlaubt, darf den Computer wecken, WoL-Einstellungen des Treibers, kabelgebunden; dazu Windows-Schnellstart. Beim Computer, im Bericht „Wake-on-LAN-Bereitschaft“ und als Sammlungsfilter „Wake-on-LAN bereit“ (ab dem nächsten Scan)
- **Neu – Zeitplan: ausgeschaltete Ziele wecken (optional, Standard aus):** bei einem Termin, im Zeitfenster und bei „Jetzt ausführen“ – nicht bei jedem Heartbeat; geweckte Computer werden versorgt, sobald sie hochgefahren sind
- **Behoben – winget (MobaXterm):** nach der Wiederholung ohne `--scope` scheiterte das Entpacken des ZIP-Archivs, die Wiederholung mit tar.exe lief aber nur vorher. Jetzt der Reihe nach: erst ohne `--scope`, dann – falls nötig – mit tar.exe, ebenfalls ohne `--scope`. Lokales System wird ohne .NET-Methode erkannt (Constrained Language Mode)
- **Behoben – winget (PowerShell 7):** „No installed package found“ (`-1978335212`) mit `--scope machine` wird ebenfalls ohne `--scope` wiederholt. „Keine passende Aktualisierung“ heißt nicht mehr automatisch „bereits aktuell“: gibt es eine neuere Version mit anderer Installationstechnik (9011) oder eine, die nicht zum System passt (9012), schlägt der Schritt mit Klartext und Abhilfe fehl
- **Behoben – Dienstkonto-Schutz durch einen einzelnen Computer:** Lehnte ein Gerät die Anmeldung mit „Kennwort falsch“ ab (z. B. falscher DNS-Eintrag – unter der Adresse antwortet ein anderes Gerät), pausierte POCKET KNIFE alle Zugriffe. Jetzt wird erst per LDAP geprüft, ob das Konto wirklich nicht geht; sonst betrifft der Fehler nur diesen Computer
- **Verbessert – „Prozess ohne Ergebnis beendet“:** Schritte als Dienstkonto schreiben die Fehlerausgabe von PowerShell jetzt mit; die Meldung nennt den Grund (PowerShell-Fehler, Sperre durch Defender-ASR oder AppLocker, letzte Ausgabe) und die Abhilfe („Lokales System“)
- **Neu – Rechenlast in Einstellungen → Wartung:** Durchschnitt, Hintergrundaufgaben mit der meisten Rechenzeit und die letzten Lastspitzen mit dem, was gerade lief (Aufgaben, Scans, Bereitstellungen, Anfragen der Oberfläche)
- **Selbsttest:** Wake-on-LAN (mehrfach, feste Relays, Kabelkarte, Abwarten, Bereitschaft, Bericht, Scanner in PowerShell geparst, Zeitplan nur auf Wunsch), winget-Reihenfolge und Auswertung in echter PowerShell nachgespielt, Dienstkonto-Schutz, Fehlergrund bei „ohne Ergebnis“, Rechenlast

## pocketknife-2.30.1-s1 (26.10.2026)
- **Wöchentliches Sicherheitsupdate** (automatisch gebaut): Basis-Image, Debian- und Python-Pakete auf den neuesten Stand gebracht, ohne Cache neu gebaut, Selbsttest und Schwachstellen-Prüfung (Trivy) bestanden. Am Programm selbst hat sich nichts geändert (Programmstand 2.30.1)

## pocketknife-2.30.1 (25.10.2026)
- **Behoben – winget meldete „bereits aktuell“, obwohl eine neuere Version da war:** Bei Paketen, die im winget-Katalog keinen Installationsbereich angeben (z. B. MobaXterm, kommt als ZIP mit MSI), lehnt winget die Aktualisierung einer installierten älteren Version mit `--scope machine` ab („A newer package version is available in a configured source, but it does not apply to your system or requirements“, Code -1978335189). POCKET KNIFE wertete das als „Keine passende Aktualisierung (bereits aktuell)“ – das Programm blieb alt. Jetzt wird der Schritt in diesem Fall einmal ohne `--scope` wiederholt, genau wie `winget upgrade` von Hand; ist das Paket wirklich aktuell, bleibt es bei „bereits aktuell“. Gilt für Pakete, Zeitpläne und den Kiosk
- **Selbsttest:** Wiederholung ohne `--scope` (nur bei festgelegtem Bereich, nie bei Deinstallation), in echter PowerShell nachgespielt: wird aktualisiert bzw. ist wirklich aktuell

## pocketknife-2.30 (25.10.2026)
- **Sicherheit – Mandanten sauber getrennt:** OU-Admins ändern nur noch, was ihnen gehört. Pakete und Zeitpläne, die ein Hauptadmin für ihren Mandanten angelegt hat, sehen sie, verteilen bzw. führen sie aus und können sie **als Vorlage kopieren** – ändern und löschen aber nicht mehr. Der Hauptadmin kann das je Paket bzw. Zeitplan mit **„OU-Admins dürfen ändern“** freigeben. Bestehendes, das ein OU-Admin angelegt oder geändert hatte, erkennt POCKET KNIFE einmalig am Protokoll und lässt es ihm
- **Neu – eigene Sammlungen für OU-Admins:** Mit „eigene Pakete“ beim Mandanten legen OU-Admins eigene dynamische und statische Sammlungen und Ordner an. Sie enthalten nie mehr als die Computer des Mandanten (egal, was die Regel sagt), sind für andere Mandanten unsichtbar und taugen nicht als Mandantenbereich. Sammlungen des Hauptadmins sind für OU-Admins nur lesbar („Als Kopie für Ihren Bereich“); Regeln sehen sie nur bei eigenen Sammlungen
- **Neu – zentrale Zeitpläne und Berichte für OU-Admins zum Nachsehen:** Zentrale Zeitpläne, die einen ihrer Computer erreichen, erscheinen nur lesend (Badge „zentral“) – mit Zielen, Zahlen, Vorschau und Verlauf nur ihrer Computer; starten und ändern nur Hauptadmins. Berichte (vorgegebene und eigene) können OU-Admins ausführen und als CSV laden – nur mit ihren Computern. Eigene Berichte anlegen, Mailversand und Benachrichtigungen bleiben Hauptadmins vorbehalten
- **Sicherheit:** Eine Sammlung, die einem Mandanten als Bereich zugeordnet ist, lässt sich nicht mehr löschen (sonst hätte der Mandant keinen Bereich mehr). Vorschläge im Sammlungseditor und die Vorschau zeigen OU-Admins nur Werte ihrer Computer
- **Behoben – Installation mit nur einem DNS-Server:** Ohne `PK_DNS_2` stand in der `docker-compose.yml` zweimal dieselbe DNS-Adresse. Ältere Docker-Compose-Versionen (z. B. auf einem NAS) brachen deshalb mit „services.guacd.dns array items[0,1] must be unique“ ab. `PK_DNS_2` ist jetzt echt optional – leer lassen, wenn es nur einen DNS-Server gibt
- **Verbessert – Start ohne Schreibrecht:** Darf POCKET KNIFE nicht in den Datenordner schreiben (typisch auf einem NAS wie Synology mit eigenen Freigabe-Rechten), bricht es jetzt mit einer klaren Meldung ab – welcher Ordner, welche Benutzer-ID und was zu tun ist – statt mit einem Traceback (`PermissionError … /data/tls/server.key.tmp`). Ein nicht beschreibbarer Sicherungsordner wird beim Start gemeldet
- **Neu – Installationsanleitung im Handbuch:** Schritt für Schritt vom leeren Docker-Host bis zur ersten Anmeldung: Docker-Host und Ports, jede wichtige Zeile der `.env` (Pflicht und optional), Start und Prüfung, erste Anmeldung, Container-Verwaltungen (NAS, Portainer, Dockhand), ein eigener Abschnitt **Auf einem NAS (z. B. Synology)** mit eigenem Benutzer und `PK_UID`/`PK_GID`, und häufige Fehler bei der Installation
- **Selbsttest:** Pakete/Zeitpläne nur lesbar, kopieren als Vorlage, Freigabe-Häkchen, zentrale Zeitpläne (nur eigene Ziele, nicht starten), eigene Sammlungen (Begrenzung auf den Mandanten, fremde Computer, fremde Ordner, Verweise, Sichtbarkeit), Berichte nur mit eigenen Computern (interner Parameter nicht von außen setzbar), einmalige Übernahme; DNS-Einträge der `docker-compose.yml` nie doppelt (mit einem und mit zwei DNS-Servern); Schreibprüfung beim Start als Benutzer ohne Rechte

## pocketknife-2.29.9 (25.10.2026)
- **Neu – mehrere Sammlungen je Mandant:** Beim Mandanten lassen sich jetzt beliebig viele Sammlungen wählen (z. B. mehrere OUs, Standorte oder AD-Gruppen) – zusammen ergeben sie seinen Bereich: Pakete und Zeitpläne des Mandanten erreichen genau diese Computer, OU-Admins sehen genau diese. Auswahl wie bei den Zeitplänen (Baum mit Suche). Bestehende Mandanten behalten ihre eine Sammlung
- **Sicherheit – OU-Admins sehen nicht mehr die ganze Domäne:** Bei den Sammlungen (Computerbaum, Auswahl in Paket-Bedingungen) erscheinen nur noch die Sammlungen ihres Bereichs samt Unterordnern, andere nur, wenn sie mindestens einen ihrer Computer enthalten (z. B. eine Ausnahme-Gruppe), und die Standardsammlungen. Die übrige Domäne und die Sammlungen anderer Mandanten bleiben verborgen. Eine gewählte, nicht sichtbare Sammlung in einer Bedingung bleibt erhalten („Sammlung #… (nicht sichtbar)“) statt still ersetzt zu werden
- **Selbsttest:** Bereich aus mehreren Sammlungen (Bereitstellung, Computerliste), ältere Mandanten mit einer Sammlung, sichtbare Sammlungen eines OU-Admins (eigene, Ausnahme-Gruppe, Standard, Ordner – nicht fremd, nicht unbeteiligt), Auswahl in Paket-Bedingungen, Schutz vor dem Löschen

## pocketknife-2.29.8 (25.10.2026)
- **Behoben – Übersicht:** Bei „Letzte Bereitstellungen“ schoben lange Namen (z. B. „Deinstallation: Microsoft Visual C++ 2008 Redistributable …“) die Zeilen über den Rand der Kachel hinaus. Lange Namen werden jetzt umgebrochen; allgemein kann kein Tabelleninhalt eine Kachel der Übersicht mehr breiter machen als ihren Platz
- **Selbsttest:** Umbruch und Begrenzung geprüft

## pocketknife-2.29.7-s1 (25.10.2026)
- **Wöchentliches Sicherheitsupdate** (automatisch gebaut): Basis-Image, Debian- und Python-Pakete auf den neuesten Stand gebracht, ohne Cache neu gebaut, Selbsttest und Schwachstellen-Prüfung (Trivy) bestanden. Am Programm selbst hat sich nichts geändert (Programmstand 2.29.7)

## pocketknife-2.29.7 (25.10.2026)
- **Sicherheitsupdates erkennbar benannt:** Das wöchentliche Sicherheitsupdate zählt nicht mehr die Programmversion hoch, sondern hängt `-s<n>` an – 2.29.7 → 2.29.7-s1 → 2.29.7-s2 heißt „gleiches Programm, nur neue Pakete“. Eine neue Programmversion fängt wieder ohne `-s` an. App (Neuigkeiten, Hinweis auf neue Versionen), `update.sh` und Release-Workflow sortieren richtig (2.29.7 < 2.29.7-s1 < 2.29.7-s2 < 2.29.8); Image-Tags z. B. `ghcr.io/…/pocketknife:2.29.7-s1`
- **Behoben:** Beim automatischen Update blieb guacd ohne Prüfsumme (Digest) – `docker compose config` scheitert auf GitHub ohne `.env`. Jetzt wird die Zeile direkt gelesen; lässt sich der Digest nicht ermitteln, bricht das Update ab (nie still ungepinnt)
- Wöchentliches Update und „Versionen aktualisieren“: keine Git-Anmeldedaten mehr im Arbeitsordner während Bau und Tests – das Token wird nur beim Speichern verwendet
- **Selbsttest:** Sortierung der `-s`-Versionen in App, `update.sh` und Workflow, Zählweise, keine gespeicherten Git-Anmeldedaten in allen Workflows, guacd immer mit Digest

## pocketknife-2.29.6 (24.10.2026)
- **Wöchentliches Sicherheitsupdate** (automatisch gebaut): Basis-Image, Debian- und Python-Pakete auf den neuesten Stand gebracht, ohne Cache neu gebaut, Selbsttest und Schwachstellen-Prüfung (Trivy) bestanden. Am Programm selbst hat sich nichts geändert
- Geändert (7): `Basis-Image` bisheriger Stand → neuer Stand (Sicherheitsupdates von Debian/Python), `certifi` 2026.10.19 → 2026.10.23, `gcc` – → 4:14.2.0-1, `libc6-dev` – → 2.41-12+deb13u4, `pycparser` 3.11 → 3.12, `starlette` 1.9.0 → 1.10.0, `tzdata` 2026c-0+deb13u1 → 2026e-0+deb13u1

## pocketknife-2.29.5 (24.10.2026)
- **Kein Versionssprung bei Python-Paketen:** Das wöchentliche Update hält jedes Python-Paket auf seiner Hauptversion (vor 1.0 auf seiner Unterversion, z. B. fastapi 0.155.x) – Fehler- und Sicherheitskorrekturen darin kommen weiter jede Woche. Die Grenzen stehen in `requirements.caps` (erzeugt von `tools/caps.py` aus den festgelegten Versionen) und gelten beim Bau. Kalenderversionen (certifi mit den Stammzertifikaten) kommen immer neu. Eine neue Hauptversion nur bewusst: `PK_ALLOW_MAJOR=1 tools/pin-versions.sh --update` mit vollem Test
- Wöchentliches Update und „Versionen aktualisieren“: schlägt das Aktualisieren fehl, steht die Ursache jetzt direkt am Lauf (Annotation)
- **Selbsttest:** Grenzen (Haupt-/Unterversion, Kalenderversionen), Grenzen passen zu `requirements.lock`, gelten in Bau- und Laufzeit-Stufe, werden vor dem Leeren der Datei erzeugt

## pocketknife-2.29.4 (24.10.2026)
- **Alles im Image aktuell:** Beim Bau bringt `apt-get upgrade` *alle* Debian-Pakete auf den Stand des Snapshots – auch die des Basis-Images (bisher nur die eigenen). pip wird nach der Installation aus dem fertigen Image entfernt (zur Laufzeit nicht gebraucht – eine Komponente weniger, die veralten kann)
- **Wöchentliches Update bleibt im Unterbau:** innerhalb der Python-Reihe und Debian-Version des Images (z. B. `python:3.12-slim-trixie`) – bisher hätte `python:3.12-slim` still auf die nächste Debian-Version gewechselt. guacd kommt immer in der neuesten Version (Docker Hub); dessen Befunde zeigt Trivy als Hinweis (Korrektur nur durch das Guacamole-Projekt)
- **Neu: Unterbau-Warnung** im wöchentlichen Lauf (`tools/unterbau-check.py`, Daten von endoflife.date): ab 12 Monaten vor dem Ende der Sicherheitsupdates für Python-Reihe oder Debian-Version ein Hinweis, ab 6 Monaten ein roter Lauf (Mail) – dann den Unterbau bewusst anheben (`PK_PYTHON_BASE=… tools/pin-versions.sh --update`). Blockiert die Sicherheitsupdates nie
- Behoben: Das Diagnosepaket nahm höchstens 40 CHANGELOG-Abschnitte auf – bei wöchentlichen Versionen zu wenige (jetzt 300)
- **Selbsttest:** Upgrade vor der Installation, pip entfernt, Update bleibt in Reihe und Debian-Version, guacd-Abfrage, Unterbau-Prüfung (Hinweis, rot, nicht erreichbar, altes und neues Datenformat)

## pocketknife-2.29.3 (24.10.2026)
- **Neu: wöchentliches Sicherheitsupdate** (montags 03:23 UTC, auch von Hand): GitHub bringt Basis-Image, Debian- und Python-Pakete und guacd auf den neuesten Stand (`tools/pin-versions.sh --update`), baut ohne Cache neu und lässt Selbsttest und Trivy laufen. **Nur wenn alles bestanden ist**, bekommt der Stand eine neue Versionsnummer (z. B. 2.29.3 → 2.29.4) mit Eintrag in CHANGELOG und Neuigkeiten (`tools/security-release.py`, listet die geänderten Pakete), und das Release baut amd64/arm64, prüft beide noch einmal und veröffentlicht unter `<Version>` und `:latest`. Mit Dockhand o. Ä. sind die Container so jede Woche aktuell – auch ohne Programmänderung. Ohne Änderung passiert nichts; schlägt etwas fehl, wird nichts veröffentlicht (Mail von GitHub). Abschalten: Actions → Wöchentliches Sicherheitsupdate → Disable workflow; bei einer Version bleiben: `PK_IMAGE=…:<Version>`
- Wöchentliches Update und Release laufen nie gleichzeitig
- **Selbsttest:** Zeitplan montags, Reihenfolge (aktualisieren/bauen/testen → Trivy → Version → speichern → Release), ohne Änderung keine Version, nur Release veröffentlicht; Versionsnummer, CHANGELOG mit geänderten Paketen, Neuigkeiten und README automatisch

## pocketknife-2.29.2 (24.10.2026)
- **Neu: Schwachstellen-Prüfung mit Trivy.** Ein Image mit bekannten Schwachstellen der Stufen HOCH/KRITISCH, für die es eine Korrektur gibt, wird nicht mehr veröffentlicht: `Release` prüft amd64 und arm64 vor dem Hochladen, `Test` bei jedem Push (Tabelle der Befunde in der Zusammenfassung des Laufs)
- **Neu: täglicher Sicherheits-Scan** des veröffentlichten Images (`:latest`) – Schwachstellen, die erst nach einem Release bekannt werden, meldet GitHub per Mail
- **Neu: Workflow „Versionen aktualisieren“** (von Hand): Basis-Image, Debian- und Python-Pakete und guacd neu festlegen (`tools/pin-versions.sh --update`), ohne Cache bauen, Selbsttest und Trivy – das Ergebnis kommt als Pull Request
- Trivy wird in fester Version geladen und per SHA-256 geprüft, ohne GitHub Action (wegen der Kompromittierung von trivy-action/setup-trivy im März 2026, CVE-2026-33634); auf dem Docker-Host: `sh tools/trivy-scan.sh pocketknife:local`. Bewusst hingenommene Befunde mit Begründung und Ablaufdatum in `.trivyignore`
- Erster Lauf: das veröffentlichte Image 2.29.1 und der aktuelle Stand haben keine Befunde
- **Selbsttest:** Trivy-Version und Prüfsummen fest (keine kompromittierte Version), alle Actions aller Workflows auf Commit festgelegt, keine Trivy-Action, Prüfung vor dem Hochladen, täglicher Scan, Ablauf sauber/Befund/nicht prüfbar mit Bericht

## pocketknife-2.29.1 (24.10.2026)
- **Neu: Skript-Schritte je Mandant freigeben.** Beim Mandanten gibt es unter „eigene Pakete“ den Schalter **„dürfen dabei Befehls- und PowerShell-Schritte schreiben“** – standardmäßig **aus**. Ohne Freigabe dürfen OU-Admins keine Schritte „Befehl“ und „PowerShell“ und keine eigenen Deinstallationsbefehle (im Paket und in der Software-Liste) anlegen oder ändern; der Server prüft das beim Speichern, Kopieren und Importieren. Skripte legen sie stattdessen als Datei (.ps1/.cmd) in ihren Repository-Ordner und führen sie mit „Installieren“ aus – dort erfasst sie die Bruchprüfung. Schritte eines Hauptadmins dürfen unverändert bleiben; bestehende Pakete laufen weiter. Die Paketansicht bietet die Schritte ohne Freigabe gar nicht an
- **Hinweis bei Zugriff auf Freigaben oder Internet:** Enthält ein Mandantenpaket in Befehlen, PowerShell oder Aufrufparametern Zugriffe auf Freigaben (`\\server\…`, `net use`, `New-PSDrive`) oder Downloads (`Invoke-WebRequest`, `curl`, `bitsadmin`, `https://` …), erscheint nach dem Speichern bzw. beim Import ein Hinweis, und er steht im Protokoll. Bewusst nur ein Hinweis, keine Sperre (lässt sich umschreiben)
- **Neu in der Hilfe: „Mandanten absichern“** (Deutsch und Englisch): die drei Stufen (Dateien nur aus dem eigenen Ordner, eigener Code nur mit Freigabe, Netzwerkrechte des Computerkontos), Freigaben prüfen (auch das Repository braucht keine Rechte für „Domänencomputer“), optional ausgehendes SMB per GPO-Firewall auf die nötigen Dateiserver beschränken
- **Selbsttest:** Befehl/PowerShell/eigener Deinstallationsbefehl ohne Freigabe abgelehnt, Skriptdatei aus dem eigenen Ordner erlaubt, Hauptadmin-Schritt unverändert ja/geändert oder zusätzlich nein, Software-Liste mit erkanntem/geändertem Befehl, Import, Hinweis bei Netzzugriff (Oberfläche und Protokoll), Freigabe nur mit „eigene Pakete“

## pocketknife-2.29 (24.10.2026)
Code-Audit (Anmeldung und Rechte, Ausführung auf den Clients, Daten und Geheimnisse, Paket-/Zeitplan-Logik, Oberfläche, Build und Update). Alle bestätigten Funde sind behoben und mit Selbsttests abgesichert.
- **Sicherheit – Bedingungen:** Fehlt eine Sammlung aus einer Paket-Bedingung (gelöscht, AD-Gruppe umbenannt) oder lässt sie sich gerade nicht auswerten, wird das Paket übersprungen – bisher wurde aus „nicht Mitglied von Ausnahmen“ still „überall installieren“. Sammlungen, die in Paket- oder Schritt-Bedingungen stehen, lassen sich nicht mehr löschen (auch nicht vom AD-Abgleich)
- **Sicherheit – Mandantenpakete laufen nie als Dienstkonto**, auch wenn ein Hauptadmin sie so gespeichert hat (die Dateien im Mandantenordner kann der OU-Admin austauschen) – stattdessen Lokales System. Der Mandantenbereich wird beim Start einer geplanten Bereitstellung erneut angewendet
- **Sicherheit – Arbeitsordner auf dem Client** (`C:\Windows\Temp\PocketKnife`): nach dem Anlegen werden Besitzer und Rechte erneut geprüft (ein im selben Moment von einem Benutzer angelegter Ordner fällt auf und wird beiseitegeschoben); läuft jetzt auch im eingeschränkten Sprachmodus (SRP/AppLocker) vollständig. Der winget-Proxy-Merker liegt in `HKLM\SOFTWARE\PocketKnife` statt unter `%ProgramData%`; winget über den Suchpfad nur mit gültiger Microsoft-Signatur
- **Sicherheit – Datei-Explorer:** Löschen und Kopieren von Ordnern folgt keinen Verknüpfungen (Junctions) mehr; robocopy bei Dateikopien mit `/XJ`
- **Sicherheit – CredSSP wird nicht mehr unterstützt** (gäbe das Kennwort des Dienstkontos an jeden Client weiter; `PK_WINRM_AUTH=credssp` gilt als Negotiate). Bei NTLM zeigt „Anmeldeverfahren“ einen Hinweis mit Empfehlung Negotiate → Nur Kerberos
- **Sicherheit – Kiosk:** kein Neustart von Servern/Terminalservern und keiner, solange andere Benutzer angemeldet sind
- **Sicherheit – Notfallzugang:** neue Benachrichtigung **„Notfallzugang benutzt“** (jede Anmeldung und jeder Fehlversuch); Warnung im Protokoll bei einem Kennwort unter 16 Zeichen
- **Sicherheit – Anmeldung:** Verbindungsfehler zum AD nennen keine Domänencontroller mehr; ein ins Namensfeld getipptes Kennwort steht nicht im Protokoll. OU-Admins sehen den Namen des Dienstkontos und die Sammlungen anderer Mandanten nicht mehr; geänderte Deinstallationsbefehle nur mit „eigene Pakete erlaubt“ (der erkannte Befehl geht weiter)
- **Sicherheit – Daten:** Diagnosepaket maskiert weitere Kennwortformen (`/pass:`, `-p`, `net user … Kennwort`, Bearer/Basic, GitHub-Tokens) und anonymisiert Mandanten, deren Admin-Gruppen, Anzeigenamen und Computer-Beschreibungen. Proxy-Kennwort wird nach Änderung von Adresse/Benutzer nicht an den neuen Proxy geschickt; SMTP: Abschalten der Zertifikatsprüfung verlangt das Kennwort erneut. Das GitHub-Token geht bei Weiterleitungen nicht an andere Rechner. DNS-Antworten nur vom gefragten Server und zur gestellten Frage (zufällige Kennung). Datenbank und Protokolle sind auch ohne Start als root nur für den Dienst lesbar. LDAPS: je Name mehrere Zertifikate (mehrere DCs hinter dem Domänennamen) statt ständiger Ablehnung; Abschalten der strikten Prüfung wird protokolliert
- **Behoben – Zeitpläne:** Ein vorübergehender Bruch im Repository verbraucht nicht mehr alle automatischen Wiederholungen. Bereitstellungen, deren Start scheiterte, hängen nicht mehr bis zum nächsten Neustart auf „wartend“ (und sperren ihre Computer für alle Zeitpläne)
- **Behoben – Bruchprüfung:** Stände, die gespeicherte Bereitstellungen noch brauchen („Erneut verteilen“, geplant), werden nicht mehr aufgeräumt; eine Installationsdatei mit geändertem Datum, deren Fingerabdruck vor dem Start nicht fertig wurde, lässt den Lauf nicht mehr durch (Dateien eines kopierten Ordners blockieren nicht)
- **Behoben – Paket-Import:** gleichnamige Sammlungen werden über den Ordnerpfad (bzw. AD-DN) eindeutig zugeordnet, sonst abgelehnt – nie die falsche
- **Behoben:** Identitätsprüfung bei Zeitüberschreitung bestätigt keine neue Adresse; Sammlungen verschieben/Mitglieder ändern wirkt sofort auf Mandanten; Kennzeichen-Nachtrag kommt reihum an alle Pakete; Nachrichten-Schritt ohne Anführungszeichen-Probleme; Tool-Variablen mit „-“ werden nicht unzitiert eingesetzt; Registry-Pfade der Deinstallation wörtlich
- **Oberfläche:** CSV-Export schützt auch Formeln mit Leerzeichen davor (auch im Diagnosepaket); Links in Hilfe/Neuigkeiten nur http(s)/mailto; strengere Sicherheitsrichtlinie der Kiosk-Symbole bleibt erhalten
- **Update/Build:** `update.sh`: lehnt ein Zurückgehen ab und läuft dann erneut, bleiben der letzte gute Stand in `.pk-vorher/` und das letzte gute Image erhalten (`pocketknife:rollback` = letztes gutes Image, `pocketknife:previous` erst nach Erfolg; geht ein weiteres Update danach zurück, passen Programmdateien und Image zusammen); liegt `pocketknife-X.zip.sha256` neben der ZIP, muss die Prüfsumme stimmen. Healthcheck als isoliertes Python (`-I`), ohne beschreibbares HOME; liest `PK_HTTPS`/`PK_PORT` auch aus `*_FILE`. Release-Workflow: nur vom Hauptzweig, nie zwei gleichzeitig, veröffentlichte Versionen werden nie überschrieben, `latest` (Image und Release) nur für die höchste Version, minimale Rechte, keine gespeicherten Git-Anmeldedaten
- **Selbsttest:** alle oben genannten Fälle (u. a. gelöschte/kaputte Ausnahme-Sammlung, Verwendung in Bedingungen, Wiederholungen bei Bruch, hängende Läufe, Mandant beim Start, Import gleichnamiger Sammlungen, Junctions beim Löschen/Kopieren, Arbeitsordner in PowerShell im eingeschränkten Sprachmodus, Kiosk-Neustart, Fehlermeldungen, Maskierung, Proxy, Weiterleitung, DNS, update.sh nach abgelehntem Zurückgehen und Abbruch beim Umbenennen, Prüfsumme der ZIP)

## pocketknife-2.28.2 (24.10.2026)
- **Automatische Updates mit `:latest`:** `docker-compose.yml` nimmt standardmäßig das fertige Image `ghcr.io/soleil0815/pocketknife:latest` (amd64/arm64) – ohne `build:`. Eine Container-Verwaltung wie Dockhand oder Watchtower kann damit neue Versionen selbst ziehen und den Container neu starten (oder von Hand `docker compose pull && docker compose up -d`); vor dem Start sichert POCKET KNIFE die Datenbank und erweitert sie bei Bedarf. Bei einer Version bleiben: `PK_IMAGE=ghcr.io/soleil0815/pocketknife:<Version>` in der `.env` bzw. den Tag in der Container-Verwaltung festlegen. In der Übersicht steht nie mehr `pocketknife:local`, außer man baut selbst
- **Selbst bauen** (ZIP-Weg ohne Internet, `tools/pin-versions.sh`): `docker build -t pocketknife:local .`; `update.sh` setzt dazu `PK_IMAGE=pocketknife:local` in der `.env` (bei einem Fehlschlag mit zurückgelegt). Ältere Programmstände ohne `build:`-Eintrag werden beim Zurückgehen ebenso gebaut
- Erstinstallation: `docker login ghcr.io` (private Registry) und `docker compose up -d` – das Image wird geladen, nicht gebaut
- **Selbsttest:** Standard-Image aus der Registry ohne `build:`, ZIP-Weg baut lokal und trägt `pocketknife:local` ein, Bau-Fehler legt auch die `.env` zurück

## pocketknife-2.28.1 (24.10.2026)
- **Container zeigt die Herkunft:** Nach `sh update.sh --pull` läuft der Container – wie andere Container – unter dem Namen aus der Registry, z. B. `ghcr.io/soleil0815/pocketknife:latest` (bzw. `:2.28.1` bei `--pull 2.28.1`), statt `pocketknife:local`. Dafür trägt `update.sh` `PK_IMAGE` in die `.env` ein (wird bei einem Fehlschlag mit zurückgelegt); `docker-compose.yml` nimmt `image: ${PK_IMAGE:-pocketknife:local}`. Der ZIP-Weg baut wie bisher lokal als `pocketknife:local` und leert `PK_IMAGE`
- Wer schon 2.28 per `--pull` hat: nach dem Update auf 2.28.1 noch einmal `sh update.sh --pull` – ist das Image dasselbe, stellt es nur den Namen um (Neustart, kein Tausch)
- Rückweg: das bisherige Image wird über die Kennung des laufenden Containers als `pocketknife:previous` gemerkt und beim Zurückgehen unter genau dem Namen gestartet, den die bisherige `.env` erwartet (`…:latest` zeigt da schon auf die neue Version)
- **Selbsttest:** Name aus der Registry in der `.env`, nur Namen umstellen bei gleichem Image, ZIP-Weg setzt zurück auf lokal, Rückweg nach Fehlstart ohne Bauen unter dem richtigen Namen

## pocketknife-2.28 (24.10.2026)
- **Neu: Neuigkeiten in der App.** Unter der Version in der Seitenleiste steht nach einem Update dezent **„Neu“** (bei Sicherheitsupdates „Neu · Sicherheit“ mit rotem Punkt) – kein Fenster, das sich von selbst öffnet. Ein Klick zeigt kurz und verständlich, was sich geändert hat, je Punkt gekennzeichnet als **Sicherheit**, **Neu**, **Verbessert** oder **Behoben**; Sicherheit steht immer oben. Je Benutzer merkt sich POCKET KNIFE, was er gesehen hat – wer Versionen übersprungen hat, sieht alle seither; ein neuer Benutzer nur die aktuelle. OU-Admins sehen nur Punkte, die sie betreffen. Die Neuigkeiten von 2.25 bis 2.27.1 sind schon enthalten (Datei `app/news.json`, Deutsch und Englisch)
- **Neu: Hinweis auf neue Versionen** (nur Hauptadmins): Der Server – nie der Browser – fragt einmal täglich bei GitHub nach dem neuesten Release (über den Proxyserver, falls eingetragen) und zeigt in der Seitenleiste „↑ 2.28.1 verfügbar“, bei Sicherheitsupdates rot. Der Klick zeigt die Neuigkeiten der neuen Version und den Befehl `sh update.sh --pull` zum Kopieren; im Protokoll steht es einmal. Einstellbar unter **Einstellungen → Wartung → Neuigkeiten und Updates** (Repository, Lese-Token für private Repositorys – verschlüsselt im Tresor, nie angezeigt –, „Jetzt prüfen“, abschaltbar). Ohne Internet oder Token: einfach kein Hinweis
- Release-Workflow: bricht ab, wenn die Version keine Neuigkeiten in `app/news.json` hat
- **Selbsttest:** Aufbau von news.json, gesehen je Benutzer (neu, übersprungen, nie Zukunftsversion), Sicherheit zuerst, OU-Ansicht ohne Update-Hinweis, Versionsabgleich mit nachgestelltem GitHub (einmal täglich, Sicherheitsupdate hervorgehoben, Protokoll), Fehler 404/keine Verbindung verständlich und ohne Token, Token verschlüsselt und nirgends sichtbar, Einstellungen nur für Hauptadmins

## pocketknife-2.27.1 (24.10.2026)
- **Neu in der Hilfe: „WinRM per Gruppenrichtlinie einrichten und absichern“** (Deutsch und Englisch, mit Abbildungen) – Schritt für Schritt: Gruppenrichtlinie anlegen, Dienst, Listener, **Firewall nur für den POCKET-KNIFE-Server** (der IPv4-/IPv6-Filter der Richtlinie bestimmt nur, auf welchen eigenen Adressen der Client lauscht – nicht, wer zugreifen darf), Härtung des WinRM-Dienstes (Basic, unverschlüsselt, CredSSP, RunAs), was man nicht abschalten darf (Remoteshellzugriff, Kerberos/Negotiate), wer sich anmelden darf, Anmeldeverfahren im Vergleich (Kerberos, Negotiate, NTLM, CredSSP; „Geschützte Benutzer“ nur mit „Nur Kerberos“), optional HTTPS mit Zertifikat per automatischer Registrierung, Protokollierung, Prüfen und häufige Fehler. Die Voraussetzungen verweisen darauf; auch im Handbuch (PDF)
- **GitHub baut die Images:** Workflow „Release“ – bei einem Tag `v<Version>` Image für **amd64 und arm64** bauen, Selbsttest in beiden, Upload in die GitHub Container Registry (`ghcr.io/…/pocketknife:<Version>` und `:latest`), Release mit Programm-ZIP, SHA-256 und Image-Digest; nichts wird veröffentlicht, wenn ein Schritt fehlschlägt. Workflow „Test“ baut und testet jeden Push. Alle Actions sind auf einen Commit festgelegt
- **`sh update.sh --pull [Version]`:** fertiges Image statt lokal bauen – die Programm-ZIP kommt aus dem Image selbst (geprüft per SHA-256, Version muss passen), gestartet wird genau dieses Image per Digest. Private Registry: einmal `docker login ghcr.io` (Token mit `read:packages`, nicht in der `.env`); eigene Registry mit `PK_IMAGE_REPO`. Ist genau dieses Image schon aktiv, passiert nichts. Der ZIP-Weg bleibt unverändert
- **Rückweg ohne Bauen:** `update.sh` behält das bisherige Image als `pocketknife:previous` – startet die neue Version nicht sauber, geht es zurück, ohne neu zu bauen und ohne Internet (auch beim ZIP-Weg)
- **Selbsttest:** `update.sh --pull` (Digest, kein Bauen, Prüfsumme falsch, Image und ZIP passen nicht, schon aktuell, fehlende Anmeldung), Workflow (amd64/arm64, Selbsttest, Actions per Commit festgelegt), Programm-ZIP im Image

## pocketknife-2.27 (24.10.2026)
- **Neu: Ansicht „Repository“** (links im Menü): Installationsdateien direkt in POCKET KNIFE verwalten – niemand braucht mehr Zugriff auf die Freigabe. Geschrieben wird mit dem Repository-/Dienstkonto (braucht dafür **Schreibrecht** auf der Freigabe). Bedienung wie der Datei-Explorer: Mehrfachauswahl, Rechtsklick (Verschieben nach, Ausschneiden/Einfügen, Umbenennen, Neuer Ordner, Wer benutzt das?, Löschen), Ziehen auf Ordner und auf den Pfad oben, Spalte „Benutzt von“
- **Hochladen** von Dateien und ganzen Ordnern (Knopf oder Ziehen vom Desktop) in Stücken zu 8 MB mit Fortschritt; nach einem Abbruch **Fortsetzen** an derselben Stelle (auch nach Neuladen der Seite), Aufräumen abgebrochener Uploads nach 24 Stunden. Grenzen: größte Datei (Standard 4096 MB), für OU-Admins erlaubte Dateitypen und ein **Speicherkontingent je Mandant** (Einstellungen → Repository & winget → Hochladen ins Repository; beim Mandanten änderbar)
- **Verschieben und Umbenennen passen die Pfade an** – in allen Paketen (auch ausgeschaltete Schritte und Altfelder), Mandantenordnern, geplanten Bereitstellungen, der winget-Offline-Kopie und im bestätigten Stand der Bruchprüfung. Vorschau mit jedem betroffenen Paket (alt → neu), **Sicherung vorher**, bei einem Fehler wird alles zurückgedreht (Dateien und Datenbank)
- **Schutz:** Löschen und Überschreiben nur, wenn nichts die Datei bzw. den Ordner benutzt (Paket, auch verschachtelt oder ausgeschaltet, geplante/laufende Bereitstellung, Mandantenordner, winget-Offline-Kopie) – die Meldung nennt die Pakete. Gesperrt sind auch: Umbenennen/Verschieben einer Datei **in** einem benutzten Paketordner (außer der Installationsdatei selbst), zentrale Pakete in einen Mandantenordner, Mandantenpakete aus ihrem Ordner heraus, Windows-ungültige oder reservierte Namen, Schreiben durch Verknüpfungen (Junctions/Links). Jede Aktion steht im Protokoll (neue Kategorie „Repository“) mit Benutzer und Mandant
- **Assistent „Repository umstrukturieren“** (Hauptadmins): neuen Hauptordner wählen oder anlegen, bisherige Ordner markieren, Vorschau, in einem Rutsch ausführen – für die empfohlene Struktur `Zentral` + `Mandanten\<Name>`
- **Mandantenordner automatisch:** Darf ein Mandant eigene Pakete bauen und hat keinen Repository-Ordner, legt POCKET KNIFE `Mandanten\<Name>` an (beim Speichern des Mandanten, beim Start und spätestens beim ersten Zugriff). Zwei Mandanten können sich keinen Ordner mehr teilen (auch nicht verschachtelt). OU-Admins sehen und bearbeiten nur ihren Ordner, den Ordner selbst nicht umbenennen/löschen
- **Duplikatsperre:** gleiche winget-ID, gleicher MSI-Produktcode oder gleiche Installationsdatei (SHA-256, erkennt auch umbenannte Kopien) in einem **anderen Bereich** – zentral, Mandant A, Mandant B, auch zwischen zwei Mandanten – wird beim Speichern, Kopieren, Importieren und Hochladen abgewiesen, mit dem vorhandenen Paket in der Meldung. Im selben Bereich erlaubt; Hauptadmins dürfen übersteuern („Trotzdem speichern/hochladen“, protokolliert). Bestehende Duplikate blockieren andere Änderungen nicht; nachträglich erkannte stehen im Protokoll
- **Datei suchen (Reparatur per Fingerabdruck):** wurde außerhalb von POCKET KNIFE verschoben, findet „Datei suchen …“ im Paket die Dateien bzw. Ordner über Größe und SHA-256 wieder und übernimmt die neuen Pfade in alle Pakete (Sicherung vorher)
- **Mandanten dürfen alle Pakete sehen und verteilen** – zentrale und die anderer Mandanten; ändern weiterhin nur die eigenen. Verschachteln geht für alle (auch Hauptadmins) nur mit zentralen Paketen oder solchen desselben Mandanten – sonst könnte ein anderer Mandant ändern, was mitläuft (bei zentralen Paketen auf allen Computern)
- Ergebnis des Code-Reviews dazu – vor der Auslieferung behoben:
  - Löschen eines Ordners auf der Freigabe ging in Junctions darin hinein (der Dateiserver löst sie selbst auf – das Ziel wäre mitgelöscht worden): eigenes Löschen, das Verknüpfungen nur entfernt; ebenso beim Aufräumen der Kopien auf den Clients
  - OU-Admins konnten Dateien in einen Ordner legen, den ein zentrales Paket benutzt (z. B. eine DLL neben dessen Installationsdatei): Hochladen, Ordner anlegen und Hineinschieben in Ordner fremder Pakete gesperrt; zentrale Pakete können keine Dateien aus Mandantenordnern mehr benutzen; ein Mandantenordner darf keine Dateien anderer Bereiche enthalten; eine dort direkt abgelegte Datei ist bei fremden Paketen ein **Bruch** statt nur eine Abweichung
  - eine Paketänderung, die während eines Umbaus gespeichert wurde, konnte überschrieben werden (jetzt wird jedes Paket in der Transaktion neu gelesen); eine gleichzeitige Gesamtprüfung konnte bestätigte Fingerabdrücke aufräumen (sie verwirft ihren Stand jetzt); Zurückdrehen berücksichtigt auch ein Umbenennen, das auf dem Server geklappt hat, obwohl der Aufruf fehlschlug
  - erlaubte Dateitypen ließen sich per Umbenennen umgehen; Sonderzeichen wie das Kelvin-Zeichen hätten neben den eigenen Ordner geführt; Upload-Stücke werden an die richtige Stelle geschrieben; abgeschlossene Bereitstellungen werden mit angepasst („Erneut verteilen“); die Reparatur eines OU-Admins ändert nur seine Pakete und überschreibt keinen fremden Stand; der Import liest die Freigabe nicht mehr innerhalb der Datenbank-Transaktion; nachträglich berechnete Fingerabdrücke blockieren keine Altfälle; Mandantenordner werden nach einem Fehler erneut angelegt; „Zielordner wählen“ lässt sich abbrechen; ein angelegter Zielordner bleibt bei einem Problem nicht leer zurück
- **Selbsttest:** Mandantenordner und Überschneidung, Bereichsgrenzen, ungültige Namen, Verknüpfung nach draußen, Upload in Stücken (falsche Position, Fortsetzen nach Neustart, fremder Benutzer, Grenzen, Aufräumen), Schutz benutzter Dateien, Umbenennen mit Pfadanpassung (Paket, Altfelder, Bruchprüfung, geplante Bereitstellung), Zurückdrehen bei Fehler, Umstrukturieren, Duplikatsperre zwischen allen Bereichen und Übersteuern, Reparatur per Fingerabdruck, Index sofort aktuell, OU-Zugriff über die Schnittstelle, alle Review-Funde (Junction beim Löschen, fremde Ordner, Umbau während Speichern/Prüfung, Dateityp per Umbenennen, Kelvin-Zeichen, Stück an falscher Stelle)

## pocketknife-2.26 (24.10.2026)
- **Neu: Bruchprüfung der Pakete.** POCKET KNIFE merkt sich Größe, Änderungsdatum und Fingerabdruck (SHA-256) jeder Repository-Datei, die ein Paket benutzt (bei ganzen Paketordnern auch die Dateiliste). Fehlt eine Datei oder ein Ordner oder hat sich die Installationsdatei bzw. die Quelle einer Dateikopie geändert, werden Bereitstellungen, Zeitpläne, Kiosk-Installationen und Wiederholungen **unterbrochen** (kein Computer wird angefasst) und im Protokoll gemeldet – weiter erst nach **„Neuen Stand übernehmen“** im Paket oder einer Anpassung des Pakets. Andere geänderte oder neue Dateien im Paketordner sind eine Abweichung: der Lauf geht weiter, mit Hinweis. Hat sich nur das Datum geändert, entscheidet der Fingerabdruck
- Geprüft wird alle 5 Minuten aus dem Repository-Index im Arbeitsspeicher (nur Name, Größe, Datum; Funde werden vor dem Melden direkt auf der Freigabe bestätigt), vor jedem Lauf direkt auf der Freigabe und mit „Jetzt prüfen“. **Vollprüfung:** jede benutzte Datei einmal pro Woche komplett gelesen, nachts im Zeitfenster (Standard 1–6 Uhr), gedrosselt (Standard 50 MB/s), eine Datei nach der anderen, Fortsetzen in der nächsten Nacht – findet auch einen Austausch mit gleicher Größe und gleichem Datum. Einstellbar unter Einstellungen → Repository & winget → Bruchprüfung der Pakete
- Anzeige: Spalte **Zustand** in der Paketliste (✓ / ! / ✕ / ?), Bereich **Repository-Dateien** im Paket mit jeder Abweichung (bisher → jetzt), Kachel „Pakete mit Bruch“ in der Übersicht. Pakete von vor 2.26 bekommen ihren Stand bei der ersten Prüfung; ein nicht erreichbares Repository ist nie ein Bruch
- OU-Admins sehen Details nur zu Dateien in ihren Mandanten-Ordnern (ein zentrales Paket mit Bruch ist für sie „derzeit nicht verfügbar“), Kiosk-Nutzer nie Pfade; am Bereitstellungsziel steht ein neutraler Text
- Ergebnis des Code-Reviews dazu – vor der Auslieferung behoben: Dateien in einem Ordner, der Quelle einer Dateikopie ist, zählen wie die Installationsdatei; fehlt eine Datei, bleibt ihr bestätigter Stand erhalten (kam sie geändert zurück, wäre sie sonst still übernommen worden); ein nicht erreichbares Repository (Freigabe weg, Zeitüberschreitung, lokaler Ordner fehlt) wurde als „fehlt“ gewertet; die Pfadliste eines Pakets ging an die Oberfläche; ein veralteter Index meldete Fehlalarme und konnte Stände aufräumen (jetzt Bestätigung direkt auf der Freigabe, aufgeräumt wird nur bei direkter Prüfung); der Index wird für die Schnellprüfung selbst aufgefrischt; ein Fehler bei einem Pfad macht nur diesen „nicht prüfbar“; ein einziger gedrosselter Hintergrund-Worker für Fingerabdrücke (große Dateien werden zu Ende gerechnet); Vollprüfung bleibt nicht an einer gesperrten Datei hängen; Prüfungen sperren je Paket statt global; „Jetzt prüfen“ gedrosselt; „Übernehmen“ rechnet den Fingerabdruck immer neu; die Prüfung vor einem Lauf (oft ein Ausschnitt) überschreibt den Zustand des Pakets nicht; Starts beim Neustart von POCKET KNIFE warten nicht auf Fingerabdrücke; lokale Verknüpfungen im Repository werden nicht verfolgt (wie beim Kopieren)
- **Selbsttest:** Stand festhalten, neue/geänderte/fehlende Dateien, nur angefasste Dateien, Unterbrechen sofort und geplant, Übernehmen, Vollprüfung (Austausch bei gleicher Größe/gleichem Datum, gesperrte Datei), Index mit Bestätigung, Dateikopie-Ordner, Verschwinden und geändert Zurückkommen, nicht erreichbares Repository, Aufräumen, OU-Ansicht ohne Pfade

## pocketknife-2.25.7 (23.10.2026)
- **Datei-Explorer: ein Suchfeld statt zwei.** Bisher stand links ein Filterfeld (filterte nur den geöffneten Ordner, sah aber nach Suche aus) und rechts das eigentliche Suchfeld. Jetzt gibt es nur noch das Suchfeld links über der Liste: Enter oder **Suchen** durchsucht den Ordner samt allen Unterordnern auf dem Client; beim Tippen wird nichts gefiltert. In der Ergebnisansicht bleibt der Begriff stehen – ändern und Enter sucht neu; bei einem Fehler führt „Zurück zum Ordner“ zurück

## pocketknife-2.25.6 (23.10.2026)
- **Suche im Datei-Explorer findet jetzt auch tief verschachtelte Dateien:** Bisher wurde Ordner für Ordner mit einem eigenen PowerShell-Aufruf gesucht – unter Windows PowerShell 5.1 reichten die 60 Sekunden ab `C:\` oft nur für die obersten Ebenen (z. B. `C:\Windows\WinSxS`), tiefer liegende Treffer fehlten. Jetzt ein einziger rekursiver Aufruf, bei dem Windows die Namen selbst filtert – im Test rund 40-mal schneller (30 000 Ordner: 1,3 statt 49 Sekunden). Weiterhin im Constrained Language Mode; Treffer hinter Verknüpfungen (z. B. `C:\Users\All Users`) erscheinen nicht doppelt, „Zugriff verweigert“ der Kompatibilitäts-Junctions zählt nicht als Fehler; Obergrenze 5 Minuten
- **Selbsttest:** Datei in 12 Ebenen Tiefe und 300 Nachbarordnern wird gefunden, Ordner mit `[ ]` im Namen, Verhalten wie Windows PowerShell 5.1 (folgt Verknüpfungen) ohne doppelte Treffer

## pocketknife-2.25.5 (23.10.2026)
- **Paketliste: Spalte „Benachrichtigung“** mit grünem ✓ für Pakete, nach denen der angemeldete Benutzer eine Windows-Benachrichtigung bekommt – nach derselben Regel wie am Client (im Paket „immer“, oder „wie in den Einstellungen“ bei eingeschalteter Benachrichtigung und echter Installation/Aktualisierung); der Tooltip nennt den Grund
- **Installationsdateien auf Englisch:** Meldungen und Kommentare von `update.sh`, `docker-compose.yml`, `.env.example`, `Dockerfile`, `docker/*`, `tools/pin-versions.sh` und den requirements-Dateien sind jetzt englisch; Rückfragen mit `[y/N]` (`j` wird weiterhin als „ja“ angenommen). Interne Datei- und Ordnernamen (z. B. `.pk-vorher/`) bleiben gleich, damit bestehende Installationen weiter erkannt werden
- **README auf Englisch:** neue `README.en.md` (vollständige Übersetzung inkl. Installationsanleitung), gegenseitig verlinkt; `update.sh` aktualisiert sie mit

## pocketknife-2.25.4 (23.10.2026)
- **Datei-Explorer durchsuchbar:** im geöffneten Ordner oben einen Namen oder ein Muster eingeben (`setup`, `*.log`) – durchsucht den Ordner und alle Unterordner. Die Suche läuft **auf dem Client selbst** (per WinRM, auch mit Softwareeinschränkung/AppLocker im Constrained Language Mode), zurück kommen nur die Treffer; gespeichert wird nichts. Höchstens 1 Minute und 300 Treffer, Verknüpfungen werden nicht verfolgt, nicht lesbare Ordner übersprungen und gezählt. Klick auf einen Treffer öffnet den Ordner mit der Datei markiert; Rechtsklick: Herunterladen, Pfad kopieren. Ist WinRM nicht erreichbar, wird langsamer über die Freigabe gesucht
- **Repository-Suche sofort:** ein Verzeichnis des Repositorys im Arbeitsspeicher des Servers (nicht in der Datenbank; 10 000 Dateien ≈ 2–5 MB) – Treffer ohne Wartezeit, Auffrischen nach 5 Minuten im Hintergrund, Stand unter dem Suchfeld, **Neu einlesen** für Hauptadmins. Über 200 000 Einträgen wird nicht zwischengespeichert (dann direkt auf der Freigabe wie bisher)
- **Pakete exportieren und importieren:** „Exportieren“ speichert ausgewählte (oder alle angezeigten) Pakete als `*.pkpackages.json`, verschachtelte Pakete kommen mit. „Importieren …“ zeigt zuerst, was passiert (neu, als Kopie, übersprungen, Fehler), und legt erst nach Bestätigung an – mit denselben Prüfungen wie im Editor. Die Datei enthält keine IDs: Sammlungen und Scanprofile werden über den Namen zugeordnet; fehlt eine Sammlung aus einer Bedingung, wird das Paket abgelehnt statt die Bedingung still wegzulassen. Repository-Pfade sind in Paketen ohnehin relativ – ein Umzug des Repositorys auf einen anderen Server braucht nur den neuen Pfad in den Einstellungen; fest in Skripte geschriebene `\\server\…`-Pfade meldet der Export
- Ergebnis des Code-Reviews dazu – behoben:
  - Suchbegriffe mit vielen `*` konnten Server bzw. Client minutenlang auslasten (Regex-Backtracking): Vergleich jetzt ohne Regex (auf dem Client mit `-like`), höchstens 8 `*`
  - der Ersatzweg über die Freigabe griff nie, weil WinRM-Verbindungsfehler nicht als solche erkannt wurden; Fehler auf diesem Weg kommen jetzt als Meldung statt als interner Fehler
  - OneDrive-Ordner („Dateien bei Bedarf“) und deduplizierte Dateien galten als Verknüpfung und wurden nicht durchsucht bzw. ließen sich nicht herunterladen – jetzt zählen nur echte Junctions/Symlinks (auch in der Ordneransicht)
  - ein Eintrag mit zu langem Pfad (über 260 Zeichen) ließ den ganzen Ordner aus – jetzt nur diesen Eintrag (wird gezählt)
  - je Benutzer und je Computer höchstens eine Dateisuche gleichzeitig (wenige Tabs konnten die Suche für alle blockieren); kurzes Warten statt sofortiger Ablehnung
  - Import in einen Mandanten verknüpfte verschachtelte Pakete per Name auch mit Paketen eines **anderen** Mandanten – jetzt nur zentrale und eigene; zwei gleichnamige Pakete in einer Datei werden nicht mehr doppelt angelegt
  - „Neu einlesen“ verwarf den Index sofort (bis zu 10 Minuten suchten dann alle direkt auf der Freigabe) – der alte Stand bleibt, bis der neue fertig ist; ein Klick während des Einlesens wird nachgeholt
  - Import-Dialog: überholte Prüfungen werden verworfen, „Importieren“ erst nach passender Prüfung, Ergebnis (auch Fehler je Paket) bleibt danach sichtbar
  - Repository-Suche: eine langsame Ordnerliste überschrieb sofortige Treffer; bei mehreren Mandanten-Ordnern gemeinsam sortiert und begrenzt; OU-Admins sehen keine Angaben über das ganze Repository und in Fehlermeldungen weder Pfad noch Server noch Konto (auch bei anderer Schreibweise)
  - Export mit einem verschachtelten Paket, das der OU-Admin nicht sieht: verständliche Meldung; Dateisuchen stehen im Protokoll
- **Selbsttest:** Suchskript in echter PowerShell im Constrained Language Mode (wenn vorhanden), Muster ohne Backtracking, Ersatzweg, Sperren je Benutzer/Computer, Repository-Index (Auffrischen, Neu einlesen, Obergrenze, Wechsel des Repositorys, OU-Ordner), Export/Import (ohne IDs, Prüfen ändert nichts, fehlende Sammlung, fremde Mandanten, OU-Regeln)

## pocketknife-2.25.3 (23.10.2026)
- **Repository durchsuchbar:** im Paket-Editor unter „Durchsuchen …“ ein Suchfeld – findet Dateien und Ordner im ganzen Repository samt Unterordnern (Teil des Namens oder Muster wie `7z*.msi`); ein Klick übernimmt Ordner und Datei in den Schritt. OU-Admins suchen nur in den Ordnern ihrer Mandanten. Grenzen gegen Überlastung: 200 Treffer, 15 Sekunden, höchstens 3 Suchen gleichzeitig
- **Wiederholen mit frei gewählten Schritten:** „Schritte wählen …“ in der Bereitstellung – Schritte mit ihrem bisherigen Ergebnis ankreuzen (vorausgewählt: fehlgeschlagene und danach nicht mehr ausgeführte), Ziele wählen (nicht erfolgreiche, alle, einzelne), Neustart für die Wiederholung abwählbar. Es entsteht eine neue Bereitstellung nur mit diesen Schritten in der Reihenfolge des Pakets
- Ergebnis des Code-Reviews dazu: Schrittübersicht und „Fehlgeschlagene Schritte“ zeigen OU-Admins Schritt-Titel (bei Befehlen die erste Codezeile) nur noch bei Bereitstellungen, die ausschließlich ihre Computer betreffen; Fehler im Repository (nicht erreichbar, Anmeldung, Ordner fehlt) werden gemeldet statt „keine Treffer“ (kein unbemerktes Wiederholen fehlgeschlagener Anmeldungen); [ ] im Suchbegriff gelten wörtlich; überholte Suchergebnisse überschreiben die Ordneransicht nicht mehr; „..“ im Repository für OU-Admins korrekt bis zu ihrem Ordner; Fehlermeldungen nennen OU-Admins nicht den Pfad der Freigabe; fehlende oder doppelte Schritt-IDs werden ergänzt bzw. eindeutig gemacht; mehrfache Wiederholungen erzeugen keine verschachtelten Namen mehr

## pocketknife-2.25.2 (23.10.2026)
- **Computer → Benutzerdefinierte Felder** ist jetzt sofort nutzbar: freie **Notizen** je Computer (Standort, Ansprechpartner, Besonderheiten – nur in POCKET KNIFE gespeichert, nie vom Computer überschrieben) und eigene Felder wie Inventarnummer oder Raum direkt dort anlegen („Feld anlegen“, gilt für alle Computer). Bisher stand dort nur ein Verweis auf einen Einstellungsbereich, den es unter diesem Namen nicht gab
- OU-Admins können Notizen und Feldwerte ihrer Computer pflegen; Felder anlegen bleibt Hauptadmins vorbehalten

## pocketknife-2.25.1 (23.10.2026)
- **Änderungen im AD wirken von selbst:** angemeldete Sitzungen werden im Hintergrund alle 2 Minuten gegen das AD geprüft (bisher alle 15 Minuten und nur, wenn der Benutzer etwas anklickte). Aus der Gruppe entfernt oder Konto deaktiviert → abgemeldet; zusätzliche Mandanten-Gruppe oder Zugriffsgruppe → Bereich erweitert; weniger Rechte → abmelden und neu anmelden (beendet auch Fernsitzungen auf jetzt fremden Computern)
- Ergebnis des Code-Reviews dazu:
  - eine kurze Störung des AD meldet niemanden mehr fälschlich ab (bisher konnte ein Fehler bei einer Gruppensuche OU-Admins und Kiosk-Sitzungen abmelden oder den Bereich kurz verkleinern)
  - OU-Admins höchstens 30 Minuten ohne Bestätigung durch das AD (ein AD-Ausfall hält einen Entzug nicht mehr beliebig lange auf); Hinweis im Protokoll, wenn das AD nicht erreichbar ist
  - fehlt die Zugriffsgruppe im AD (gelöscht, umbenannt, mehrdeutig), wirkt der Entzug für OU-Admins trotzdem; bis zur Korrektur ist niemand Hauptadmin (außer dem Notfallzugang), das Protokoll meldet es
  - ein Durchlauf mit einer Verbindung für alle Sitzungen, Gruppen nur einmal aufgelöst; die Anfragen der Benutzer fragen das AD nicht mehr selbst (ein langsames AD bremst die Oberfläche nicht); abgelaufene Sitzungen werden nicht mehr geprüft
  - verständliche Meldung beim Abmelden („Zugriff entzogen“, „Berechtigung geändert“) statt „Sitzung abgelaufen“
- **Selbsttest:** Hintergrundprüfung und AD-Prüfung mit simuliertem AD (Störung, fehlende Zugriffsgruppe, gelöschte Mandanten-Gruppe, deaktiviertes Konto)

## pocketknife-2.25 (22.10.2026)
- **Neu: OU-Admins (Mandanten-Admins).** Beim Mandanten lässt sich eine AD-Gruppe eintragen („OU-Admins“). Ihre Mitglieder – NICHT in POCKETKNIFE-Admins – dürfen sich anmelden, sehen und steuern aber nur die Computer des Mandanten (seine Sammlung, z. B. eine OU): Inventar, Software, Aktionen, Fernsitzung, Tools ausführen, Bereitstellungen, Protokoll. Zentrale Pakete können sie verteilen, aber nicht ändern (nur als eigene Kopie); eigene Pakete und Zeitpläne nur, wenn beim Mandanten erlaubt, Dateien nur aus dessen Repository-Ordner. Einstellungen, Scanprofile, Kiosk-Verwaltung, Berichte, E-Mail und das Anlegen von Tools bleiben den Hauptadmins vorbehalten
- POCKETKNIFE-Admins bleibt die Gruppe der Hauptadmins – an `.env` und `docker-compose.yml` ändert sich nichts. Wer in beiden Gruppen ist, ist Hauptadmin; der Notfallzugang immer
- **Sicherheit:** Für OU-Admins ist jede Schnittstelle gesperrt, die nicht ausdrücklich freigegeben ist (eine neue, vergessene ist zu, nicht offen); fremde Computer gelten als nicht vorhanden (404); Listen und Zahlen (Übersicht, Software, Pakete, Bereitstellungen, Protokoll, Sammlungen) zeigen nur eigene Computer; Bereitstellungen mit fremden Zielen lassen sich nicht abbrechen, wiederholen oder löschen; Fehlerdetails gehen nie an OU-Admins. Mitgliedschaft wird bei der Anmeldung und alle 15 Minuten geprüft, Änderungen am Mandanten wirken sofort
- Zeitpläne eines Mandanten erreichen jetzt auch in Vorschau und Zielverlauf nur die Computer seiner Sammlung (wie schon beim Verteilen)
- Mandanten-Dialog: Hinweis zum Repository-Ordner korrigiert (die Beschränkung wird erzwungen, nicht nur vorgeschlagen)
- **Scharfes Code-Review vor der Auslieferung – behoben:** „Deinstallationsbefehl für alle merken“ (hätte auf allen Computern gewirkt) nur noch für Hauptadmins; Pakete und eigene Befehle von OU-Admins laufen nie als Dienstkonto (stattdessen Lokales System); Zeitpläne von OU-Admins nur auf die Sammlung des eigenen Mandanten; ein Mandant, der je OU-Admins hatte, erreicht ohne Sammlung keinen Computer, und Mandanten mit Zeitplänen lassen sich nicht mehr löschen (die Zeitpläne wurden bisher „zentral“); AD-Gruppen werden beim Speichern eindeutig als DN festgelegt – gleichnamige Gruppen anderswo geben nie Zugang, mehrdeutige Gruppennamen werden abgelehnt (auch bei der Zugriffsgruppe und den Kiosk-Gruppen); ohne Repository-Ordner keine Repository-Dateien für OU-Admins; „..“ im Paketordner abgelehnt; zentrales VNC-Kennwort und frei gewählter Port nicht für OU-Admins; Kiosk nicht über die OU-Admin-Sitzung; Inhalt von Bereitstellungen mit fremden Zielen und Sammlungsregeln verborgen; Änderungen am Mandanten beenden die Sitzungen seiner OU-Admins
- **Gegenprüfung der Korrekturen – ebenfalls behoben:** Deinstallationen mit dem Befehl aus der Registry des Computers (UninstallString) laufen nie als Dienstkonto – für alle, auch Hauptadmins (wer auf dem Computer Administrator ist, bestimmt diesen Befehl); Gruppen werden zuerst über den eindeutigen sAMAccountName gesucht (eine gleichnamige Gruppe anderswo kann die Zugriffsgruppe nicht mehr „mehrdeutig“ machen und so Hauptadmins aussperren) und für OU-Admins mit ihrer SID gespeichert; Mandanten mit unveränderter Gruppe lassen sich auch ohne erreichbares AD speichern
- **Selbsttest:** ruft jede Schnittstelle als OU-Admin auf – nicht freigegebene müssen gesperrt sein, freigegebene mit fremdem Computer 404 liefern; Listen, Pakete, Bereitstellungen, Zeitpläne und Mandanten werden gegen fremde Daten geprüft

## pocketknife-2.24.8 (22.10.2026)
Ergebnis des Code-Reviews:
- **update.sh – Zurücklegen absolut sicher:** wurde das automatische Zurücklegen selbst unterbrochen (volle Platte, Strg+C, Lesefehler), konnte ein zweiter Durchlauf die eben zurückgelegten Programmdateien und `.pk-vorher/` löschen und die `.env` leeren. Jetzt wird jeder Schritt nach dem Erledigen abgehakt, gelöscht wird nur sicher Neues, die `.env` wird nur angefasst, wenn das Update sie geändert hat (und dann über eine neue Datei), Strg+C wird währenddessen ignoriert, und der gesicherte Vorgänger kommt erst zurück, wenn alle Dateien zurück sind
- **update.sh:** ein unterbrochenes Zurücklegen nach „nicht gesund“ wird ebenfalls zu Ende geführt; nach einem Stromausfall mitten im Update wird der vollständige Vorgänger aus `.pk-vorher.alt` wiederhergestellt statt gelöscht; nach einem fehlgeschlagenen ersten Update bleibt kein leeres `.pk-vorher/` zurück
- **Benachrichtigung:** Rückmeldung legt den Registry-Schlüssel nie neu an (gleichzeitige Meldungen überschreiben sich nicht mehr); liegengebliebene Rückmeldungen werden nach 30 Minuten aufgeräumt; alte Absender-Einstellungen werden nach dem Übernehmen entfernt (ein altes „aus“ wirkt nicht ewig nach); geänderte Anzeigeskripte werden auch bei reiner Groß-/Kleinschreibungs-Änderung übernommen; genauere Protokollmeldung, wenn die Aufgabe selbst scheitert oder das Benutzerkonto nicht auflösbar ist
- **Neustart-Meldung:** Paketschritt „Neustart“ mit 0 Sekunden zeigt nicht mehr „60 Sekunden“ an; ein Backslash am Ende des Texts wird entfernt (hätte das Argument für shutdown.exe zerlegt)
- **Selbsttest:** echter Probelauf eines unterbrochenen Zurücklegens, Bau-Fehler beim ersten Update, Auswertung der Rückmeldecodes

## pocketknife-2.24.7 (22.10.2026)
- Systemanforderungen (README, Hilfe, Handbuch): Angabe „Docker Compose v2“ entfernt

## pocketknife-2.24.6 (22.10.2026)
- **Neu: Meldung bei Neustart und Herunterfahren einstellbar** (Einstellungen → Zeitpläne & Leistung): Absender (z. B. „Ihre IT-Abteilung“ statt „POCKET KNIFE“) und je Anlass ein eigener Text – Neustart/Herunterfahren aus dem Computer-Menü, Neustart nach Bereitstellung, Paketschritt „Neustart“, Neustart aus dem Kiosk. Platzhalter {absender}, {sekunden}, {minuten}, {benutzer}; unter jedem Feld ein Beispiel. Leer = bisheriger Text. Der Text geht immer als reiner Text an shutdown.exe, nie als Befehl
- **Behoben: geänderter Absender der Windows-Benachrichtigung wurde nicht übernommen** – Windows merkt sich den Namen eines Absenders. Jeder Name bekommt jetzt eine eigene Absender-ID; alte werden aufgeräumt, und hatte ein Benutzer die Meldungen abgeschaltet, bleiben sie auch unter dem neuen Namen aus
- Kiosk-Neustart nennt den Anzeigenamen des Benutzers statt des Anmeldenamens

## pocketknife-2.24.5 (22.10.2026)
- **Behoben: Windows-Benachrichtigung kam seit 2.24.4 nicht mehr** auf Rechnern mit Softwareeinschränkung (SRP), AppLocker oder WDAC. Der in 2.24.4 eingeführte Start über einen kodierten Befehl läuft dort im eingeschränkten Sprachmodus und scheitert. Jetzt wieder Start per `-File` aus „Programme“ (dort erlaubt); den kodierten Starter gibt es nur noch, wenn eine GPO die Ausführungsrichtlinie auf AllSigned/Restricted festlegt
- **Behoben: Protokoll meldete „gezeigt“, obwohl nichts erschien** – der Exitcode kommt über `conhost --headless` nicht zuverlässig bei der Aufgabe an. Das Anzeigeskript meldet sein Ergebnis jetzt selbst zurück (HKCU\Software\PocketKnife\Toast); kommt keine Antwort, steht im Protokoll „Anzeigeskript ist beim Benutzer nicht gestartet – PowerShell ist dort per Richtlinie eingeschränkt“
- **Selbsttest** prüft beides

## pocketknife-2.24.4 (22.10.2026)
Ergebnis des Code-Reviews:
- **update.sh – nie mehr ein halb leerer Programmordner:** bricht das Update mitten im Tausch der Programmdateien ab (Strg+C, volle Platte, Fehler), legt das Skript die bisherigen Dateien automatisch zurück; die bisherige Version läuft unverändert weiter. Startet die neue Version nicht (`docker compose up` scheitert), wird wie bei „nicht gesund“ die Rückkehr angeboten; startet auch die bisherige nicht, sagt das Skript das deutlich
- **update.sh – `.pk-vorher/` bleibt der echte Vorgänger:** bei derselben Version noch einmal oder nach einem nicht abgeschlossenen Update wird `.pk-vorher/` nicht mehr überschrieben
- **update.sh – Sicherungen nie gelöscht:** alte Versionsordner, in denen noch Dateien in `data/`, `data-backup/`, `repo/` oder eine Datenbankdatei liegen, bleiben immer stehen; der Umzugs-Tipp nennt jetzt Datenbank, Sicherungen und Repository zusammen
- **update.sh:** übernimmt nur bekannte Programmdateien aus dem ZIP (feste Liste, nie etwas, worin ein geschützter Ordner liegt); Zwischendatei beim Ändern der `.env` nur für root lesbar
- **Benachrichtigung:** hat der Benutzer die Benachrichtigungen von POCKET KNIFE in Windows abgeschaltet, wird das respektiert (kein Ausweichen auf „Windows PowerShell“, Protokoll: „vom Benutzer abgeschaltet“); „Im Benachrichtigungscenter anzeigen“ aus führt nicht mehr zu einer doppelten Meldung
- **Benachrichtigung:** Besitzer und Rechte von `C:\Program Files\PocketKnife` werden bei jeder Meldung wieder auf den Standard gesetzt (Administratoren/SYSTEM Vollzugriff, Benutzer nur lesen); hätten normale Benutzer Schreibrecht, unterbleibt die Meldung. Symbolpfad kommt vom Client (`%ProgramFiles%`)
- **Selbsttest** räumt seine temporären Ordner wieder auf; neue Prüfungen für alle Punkte oben (u. a. echter Probelauf von update.sh mit Abbruch mitten im Tausch)

## pocketknife-2.24.3 (22.10.2026)
- **Benachrichtigung am Client zuverlässiger und nachvollziehbar:**
  - nach der Anzeige prüft POCKET KNIFE im Benachrichtigungscenter des Benutzers, ob Windows die Meldung angenommen hat; nimmt Windows den eigenen Absender nicht an, kommt die Meldung automatisch mit dem Absender „Windows PowerShell“ (immer registriert)
  - Benachrichtigungen für den Absender werden beim Benutzer eingeschaltet, sofern er sie nicht bewusst abgeschaltet hat
  - **Ergebnis je Benutzer im Protokoll und beim Test:** „gezeigt“, „gezeigt mit Absender Windows PowerShell“, „von Windows nicht angezeigt (Benachrichtigungen aus, Nicht stören, Richtlinie)“, „Aufgabe nicht gestartet“ … – bisher stand dort nur, dass die Aufgabe gestartet wurde
  - das Anzeigeskript liegt unter `C:\Program Files\PocketKnife` (nur Administratoren dürfen schreiben)
- **PowerShell immer mit vollem Pfad und festen Schaltern:** `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoLogo -NonInteractive -NoProfile -ExecutionPolicy Bypass` – für PowerShell-Schritte, .ps1-Installer, Skript-Pakete, Tools als SYSTEM, Benutzer-/SYSTEM-Aufgaben und die Benachrichtigung
- Selbsttest um 1 Prüfung erweitert (67 insgesamt)
## pocketknife-2.24.2 (22.10.2026)
- **Selbsttest im Container:** zwei Prüfungen lasen Dateien, die nur im ZIP liegen, nicht im Docker-Image (`Dockerfile`, Übersetzungswerkzeuge) – das ergab nach dem Update „64 von 66“, obwohl alles in Ordnung war. Sie werden im Container jetzt übersprungen; vor jeder Version wird der Selbsttest zusätzlich mit genau dem Inhalt des Images geprüft
- Selbsttest ohne Testmeldungen auf der Konsole (die Zeilen mit erfundenen Rechner- und Benutzernamen waren Testdaten der Anonymisierungsprüfung)
## pocketknife-2.24.1 (22.10.2026)
- **Update immer im selben Ordner:** `sh update.sh pocketknife-<Version>.zip` im Programmordner (z. B. `/opt/pocketknife`) ersetzt jetzt die Programmdateien an Ort und Stelle – es entsteht **kein neuer Versionsordner** mehr
  - `.env`, Datenbank (`data/`), Sicherungen (`data-backup/`) und Repository (`repo/`) bleiben unangetastet
  - der vorige Stand liegt bis zum nächsten Update in `.pk-vorher/` (genau einer, wenige MB); schlägt der Bau fehl oder startet die neue Version nicht, geht es dorthin zurück
  - **Umstieg:** läuft POCKET KNIFE noch aus einem Versionsordner von 2.23.3/2.24 (`pocketknife-2.24/` …), übernimmt das Skript dessen `.env` in den Programmordner, startet von dort und bietet an, die Versionsordner zu löschen (nie einen mit Datenbank, Sicherungen oder Repository)
  - `.dockerignore`: Datenbank, Sicherungen, Repository, ZIPs, `.pk-vorher/` und alte Versionsordner wandern nicht mehr in den Docker-Build (schneller, kein unnötiger Speicher)
## pocketknife-2.24 (22.10.2026)
- **Windows-Benachrichtigung für den angemeldeten Benutzer** nach einer erfolgreichen Installation oder Aktualisierung – z. B. „7-Zip wurde aktualisiert. Installierte Version: 26.00“ mit grünem Häkchen, Absender „Softwareverteilung“ (änderbar)
  - gilt für Bereitstellungen, Zeitpläne, winget-Updates und Kiosk; mehrere Programme in einem Lauf: „2 Programme wurden installiert.“ mit den Namen
  - keine Meldung, wenn niemand angemeldet ist, sich nichts geändert hat (schon installiert/aktuell), bei Deinstallationen und reinen Skript-/Registry-Paketen
  - „aktualisiert“ bei winget-Upgrades und Programmen, die vorher schon im Inventar standen; Name und Version aus der winget-Ausgabe (englisch und deutsch)
  - **Einstellungen → Zeitpläne & Leistung → Benachrichtigung am Client:** ein/aus, Absender, Sprache, Kompatibilitätsmodus, **Test-Benachrichtigung senden**. Je Paket: wie Einstellung / immer / nie (gilt auch für verschachtelte Pakete)
  - sicher umgesetzt: kurze geplante Aufgabe als angemeldeter Benutzer (ohne Kennwort, ohne erhöhte Rechte, ohne sichtbares Fenster), Inhalt kodiert in der Aufgabe selbst statt als Datei – kein Benutzer kann sie vorher austauschen; Namen und Versionen werden nur als Daten übergeben
- **update.sh (Code-Review):**
  - Repository unter dem Standardpfad `./repo` und Datenbank unter `./data` im alten Versionsordner werden in die neue `.env` übernommen und beim Aufräumen nie gelöscht – zusätzlich geschützt sind alle tatsächlich in die Container eingebundenen Ordner
  - war der Container gestoppt und die neue Version schon eingerichtet, wird diese weiterverwendet (vorher konnte ihre Datenbank zur Seite geschoben und gelöscht werden)
  - Pfade werden vollständig aufgelöst (Symlinks, „..“), Leerzeichen und Zeichen wie `&` oder `|` im Pfad sind kein Problem mehr; `.env`-Werte in Anführungszeichen und mit Kommentar werden richtig gelesen
  - Strg+C bricht das Skript wirklich ab
- Selbsttest um 1 Prüfung erweitert (66 insgesamt)
## pocketknife-2.23.4 (22.10.2026)
- **Neue Oberfläche nach einem Update garantiert:** Skripte und Stylesheets werden jetzt mit der Versionsnummer geladen (`app.js?v=2.23.4`). Ein Browser, der noch die alte Datei im Zwischenspeicher hat, holt nach jedem Update zwingend die neue – bisher konnten z. B. die alten Menüeinträge („Anwendungen: 7-Zip“) stehen bleiben, obwohl unten links schon die neue Version stand
- Selbsttest um 1 Prüfung erweitert (65 insgesamt)
## pocketknife-2.23.3 (22.10.2026)
- **Update mit einem Befehl:** `sh update.sh pocketknife-<Version>.zip` im Ordner mit den Versionen (z. B. `/opt/pocketknife`)
  - zeigt bisherige und neue Version (warnt bei einem Rückschritt) und fragt „Update jetzt durchführen? [j/N]“
  - entpackt nach `pocketknife-<Version>`, übernimmt `.env` (und `docker-compose.override.yml`) der **laufenden** Version; ein relativer Datenpfad im alten Ordner wird fest eingetragen, damit die neue Version nicht mit leerer Datenbank startet
  - baut (Fehler → alte Version läuft weiter), startet, wartet auf die Gesundheitsprüfung, führt den Selbsttest aus; startet die neue Version nicht sauber, bietet es die Rückkehr zur bisherigen an
  - räumt auf, jeweils mit Nachfrage: ältere Versionsordner, das ZIP, ungenutzte Docker-Images. Die zuletzt laufende Version bleibt als Rückfall (`--keep <n>`), Ordner mit Datenbank, Sicherungen oder Repository werden nie gelöscht
  - hält sich selbst neben den Versionen aktuell; `-y` für ohne Nachfrage. Der bisherige Aufruf `./update.sh <alter Ordner>` funktioniert weiter
- Die Pakete 2.21 bis 2.23.2 enthielten versehentlich eine Datei `--help` (Wörterliste der Übersetzung, harmlos) – entfernt; das Werkzeug legt sie nicht mehr an
- Selbsttest um 1 Prüfung erweitert (64 insgesamt)
## pocketknife-2.23.2 (22.10.2026)
- **Nach einem Update sofort die neue Oberfläche:** Der Browser durfte `app.js`/`i18n.js` bisher nach eigenem Ermessen aus dem Zwischenspeicher nehmen – nach einem Update erschienen dann z. B. noch die alten Menüeinträge, bis man Strg+F5 drückte. Jetzt fragt er bei jedem Laden kurz nach (meist nur „unverändert“, also kaum Datenverkehr)
- Selbsttest um 1 Prüfung erweitert (63 insgesamt)
## pocketknife-2.23.1 (22.10.2026)
- **Sammlungsauswahl übersichtlich** (Ziele von Zeitplänen und Scanprofilen): Baum wie in der Seitenleiste – nach Ordnern gegliedert und **alphabetisch** (Standard, AD, eigene Ordner), mit **Suche** über alle Ordner, „nur ausgewählte“, gewählte Sammlungen oben als Liste zum Entfernen und Anzahl je zugeklapptem Ordner; Mitgliederzahl je Sammlung. Auch die einfachen Auswahlfelder (Paket-Bedingung „Sammlung“, Kiosk) sind alphabetisch mit Ordnerpfad
- **Software → Dynamische Sammlung erstellen:** ganz oben **„X“ installiert** und **„X“ nicht installiert** – die Sammlung heißt gleich „X installiert“ / „X nicht installiert“, ohne Präfix „Anwendungen:“. Bei mehreren markierten Programmen: alle / mindestens eines / keines installiert
- **Code-Review, behoben:**
  - Diagnosepaket: Namen mit Akzenten oder Sonderzeichen (josé, PC-ZOË, o'brien, SRV&DB) blieben seit 2.23 im Freitext stehen – werden wieder ersetzt; IPv4-in-IPv6-Adressen (`::ffff:10.1.2.3`) werden nicht mehr verstümmelt
  - „jede Version“ wurde bei Zahlen im Produktnamen viel zu breit (aus „Microsoft 365 Apps“ wurde `Microsoft*`) – als Version zählt nur noch eine Zahl mit Punkt (24.08, 3.12.1). Gilt auch für das Muster von Deinstallationspaketen
  - Software-Menü: Bedingungen zur angeklickten Spalte (Architektur, Installationsort, winget-ID …) wieder vorhanden
  - Englische Oberfläche: Sammlungsnamen in Auswahlfeldern nicht mehr halb übersetzt; Suche findet auch die übersetzten Namen eingebauter Sammlungen
  - Gelöschte Sammlungen in einem Zeitplan/Scanprofil erscheinen als „Sammlung #… (nicht mehr vorhanden)“ und lassen sich entfernen; neue Sammlungen sind ohne Neuladen der Seite wählbar
- Selbsttest um 1 Prüfung erweitert (62 insgesamt)
## pocketknife-2.23 (21.10.2026)
- **Proxyserver (Einstellungen → Repository & winget → Proxyserver), optional:**
  - POCKET KNIFE lädt den winget-Katalog über den Proxy – ohne oder mit Anmeldung (Benutzer/Kennwort, Kennwort verschlüsselt im Tresor)
  - wahlweise auch die Clients: winget-Einstellung `DefaultProxy` wird bei der nächsten winget-Bereitstellung gesetzt (und wieder entfernt, wenn der Haken herausgenommen wird – nur, wenn POCKET KNIFE sie selbst gesetzt hat); auch das Nachinstallieren von winget nutzt den Proxy. Anmeldedaten gehen nie an die Clients – sie melden sich mit ihrem Computerkonto am Proxy an
  - **Speichern & testen** mit Hinweisen (Anmeldung verlangt/407, Name nicht auflösbar, keine Antwort)
- **Code-Review, behoben:**
  - Diagnosepaket bei vielen Rechnern um ein Vielfaches schneller (3000 Rechner, 20 000 Protokollzeilen: 11 statt über 240 Sekunden) – Namen werden wortweise nachgeschlagen statt über eine riesige, ständig neu übersetzte Regex
  - Diagnosepaket: **Kennwörter in Befehlszeilen** (Schritttitel, Ausgaben: `PASSWORD=…`, `net use … /user:x kennwort`, `-Password …`, `ConvertTo-SecureString '…'`) werden immer durch `***` ersetzt – auch ohne Anonymisierung. Schritttitel in `fehlgeschlagen.json` und Scanprofil-Namen werden jetzt ebenfalls anonymisiert
  - Diagnosepaket: Reste echter Namen entfernt – IP- und MAC-Adressen am Satzende, IPv6-Adressen, Name vor `_` (z. B. in Protokolldateinamen), Teil-Domänen (`DC=sub`), Mailadressen mit Umlaut, `DOMÄNE\\benutzer` in JSON, eingetippte Namen bei fehlgeschlagener Anmeldung, Repository-Konto, LDAP-Zertifikat- und AD-Abgleich-Status in den Einstellungen
  - Diagnosepaket: zwei gleichzeitig erstellte Pakete konnten zwei Rechnern denselben Platzhalter geben – Pakete entstehen jetzt nacheinander, Platzhalter sind eindeutig
  - **Identitätsprüfung:** Ein falsches Kennwort des Dienstkontos oder ein nicht erreichbarer Domänencontroller ließ bisher alle geprüften Rechner als „nicht bestätigt“ erscheinen – und die wiederholten Anmeldeversuche konnten das Dienstkonto sperren. Jetzt greift der Dienstkonto-Schutz, und KDC-/Uhrzeitprobleme gelten nicht als Problem des Geräts
  - winget-Wiederholung mit tar: Einstellungsdateien mit Kommentarzeilen (`// …`) werden unter Windows PowerShell 5.1 jetzt gelesen statt übersprungen
  - „Neustart erforderlich“ stand bei winget-Code -1978334967 doppelt in der Meldung
- Selbsttest um 2 Prüfungen erweitert (61 insgesamt)
## pocketknife-2.22.2 (21.10.2026)
- **winget-Archive (TeamViewer, GameInput): Einstellung landet jetzt in der richtigen Datei.** Ein Test auf einem echten Client (`winget settings export` als Lokales System) zeigte: winget liest dort `…\systemprofile\AppData\Local\Microsoft\WinGet\Settings\defaultState\settings.json`. 2.22.1 schrieb `archiveExtractionMethod = tar` eine Ebene höher – die Wiederholung lief deshalb unverändert. Vorhandene Einstellungen in der Datei (z. B. `preferences.scope`) bleiben erhalten
## pocketknife-2.22.1 (21.10.2026)
Erkenntnisse aus dem ersten Diagnosepaket einer echten Domäne:
- **winget: „Archiv konnte nicht entpackt werden“ (Exitcode -1978335140)** – betraf TeamViewer und Microsoft GameInput (beide kommen als ZIP). Als Lokales System scheitert das Entpacken über die Windows-Shell; POCKET KNIFE wiederholt den Schritt jetzt einmal mit `tar.exe` (winget-Einstellung `archiveExtractionMethod` für Lokales System). Eigene, nicht lesbare Einstellungsdateien bleiben unverändert
- **winget-Fehler im Klartext und mit Code**, z. B. „Prüfsumme des Installers passt nicht – … meist nach ein bis zwei Tagen behoben“ (Google Chrome) oder „Eine andere Installation läuft gerade (1618)“. Bekannte Meldungen nennen jetzt auch den Exitcode
- **Kerberos „Message stream modified“ erklärt:** Das Gerät unter der Adresse kann das Ticket nicht lesen – DNS zeigt auf ein anderes Gerät oder die Vertrauensstellung des Computers ist defekt (`Test-ComputerSecureChannel -Repair`). Bisher erschien eine lange GSS-Meldung bzw. „gehört vermutlich nicht zur Domäne“
- **Diagnosepaket genauer anonymisiert:**
  - Wörter von POCKET KNIFE (z. B. „Anmeldung“) werden nie mehr als Name ersetzt – vorher stand „USER-004 fehlgeschlagen“ statt „Anmeldung fehlgeschlagen“
  - Datum und Uhrzeit in Dateinamen (z. B. Installer-Protokolle) werden nicht mehr für eine MAC-Adresse gehalten
  - AD-Gruppen aus den Kiosk-Freigaben (Einstellung und je Paket) werden durch Platzhalter ersetzt
- Selbsttest um 1 Prüfung erweitert (59 insgesamt)
## pocketknife-2.22 (21.10.2026)
- **Diagnosepaket mit Versionen:** alte Meldungen werden nicht mehr mit aktuellen verwechselt
  - jede Zeile in Protokoll, Fehlerliste, Bereitstellungen, fehlgeschlagenen Zielen und Scans trägt die Version, unter der sie entstanden ist
  - die Statistik ist getrennt: ganzer Zeitraum, **nur die laufende Version** und eine Übersicht je Version (Warnungen, Fehler, erfolgreiche und fehlgeschlagene Ziele)
  - neue Dateien: `versionen.json` (ab wann welche Version lief), `aenderungen.md` (Änderungen von der ältesten Version im Zeitraum bis heute), `behobene_fehler.txt` (alle Fehler, die der Selbsttest bei jeder Version erneut prüft)
  - neuer Zeitraum **„Seit dem letzten Update“**; die Karte zeigt, seit wann die laufende Version läuft
  - die Versionsgeschichte wird ab jetzt bei jedem Start geführt; frühere Updates werden einmalig aus den Startmeldungen im Protokoll nachgetragen, soweit es zurückreicht. Ältere Einträge heißen „vor 2.x“
  - Dateiname enthält die Version, z. B. `pocketknife-diagnose-2.22-2026-10-21-7T-anonym.zip`
- Selbsttest um 1 Prüfung erweitert (58 insgesamt)
## pocketknife-2.21 (21.10.2026)
- **Diagnosepaket (Einstellungen → Wartung):** ein ZIP zur Fehlersuche mit Protokoll, Warnungen und Fehlern, Statistik zu Scans und Bereitstellungen (Erfolgsquote je Paket, Exitcodes, häufigste Fehler, Dauer), fehlgeschlagenen Zielen mit Schrittergebnissen, Umgebung und Einstellungen – Zeitraum 1 bis 30 Tage. Nichts wird automatisch verschickt
  - **anonymisiert (Standard):** Rechner, Benutzer, Server, Personen, Gruppen, OUs, Domäne, IP-, MAC- und Mail-Adressen werden zu festen Platzhaltern (PC-001, USER-001, 10.99.x.y …), in jedem Paket gleich – so bleiben mehrere Pakete vergleichbar. Merkmale wie überlange Namen, Umlaute oder Sonderzeichen bleiben als Zusatz erkennbar (-LANG, -UML, -SZ)
  - Kennwörter, Tresor, Sitzungen, Keytab und Schlüssel sind nie enthalten – auch nicht ohne Anonymisierung
  - **Platzhalter nachschlagen:** aus „PC-017“ wird wieder der echte Name; die Zuordnung bleibt in der eigenen Datenbank und lässt sich als CSV herunterladen (wird protokolliert)
- Selbsttest um 1 Prüfung erweitert (57 insgesamt)
## pocketknife-2.20.2 (21.10.2026)
- **Standardsammlungen im Baum fest** – wie schon der AD-Bereich lässt sich der Ordner „Standard“ mit seinen Sammlungen nur noch auf- und zuklappen, nicht mehr ziehen. Eine änderbare Kopie gibt es weiter per Rechtsklick → Duplizieren bzw. Kopieren/Einfügen
## pocketknife-2.20.1 (21.10.2026)
- **Kiosk: nach einer Deinstallation steht wieder „Installieren“** statt „Neu installieren“. Ob eine Software installiert ist, entscheidet jetzt das Neuere von beidem: das Ergebnis der letzten Kiosk-Anfrage oder der letzte Scan. Bisher galt drei Stunden lang die letzte Anfrage – auch wenn die Software inzwischen (z. B. über die Verwaltung) entfernt war
- **Englische Oberfläche vervollständigt:**
  - Texte von POCKET KNIFE in Tabellen wurden seit 2.16.1 nicht mehr übersetzt (z. B. „Dienstkonto (Deploy-Benutzer)“, „(neueste)“) – nur echte Daten (Namen, Pfade) bleiben unübersetzt
  - Hinweise, die mit „– “ beginnen, wurden nie gefunden; einzelne Wörter („nie“, „veraltet“, „aktiv“ …) und Texte mit PK_-Variablen fehlten im Wörterbuch; dazu rund 100 weitere Meldungen (Kiosk-, Kerberos-, Repository- und Mail-Protokoll)
  - Begründungen zu „Adresse nicht bestätigt“ werden ebenfalls übersetzt
- Selbsttest um 1 Prüfung erweitert (56 insgesamt)
## pocketknife-2.20 (21.10.2026)
- **„Online“ nur, wenn wirklich dieser Computer antwortet** – bisher reichte es, dass unter der Adresse irgendein Gerät antwortete. Bei einer veralteten DNS-Adresse (DHCP hat sie längst einem Drucker, Handy oder anderen PC gegeben) erschien ein ausgeschalteter Computer als online
  - bei neuer Adresse (und spätestens nach 24 Stunden, einstellbar) fragt POCKET KNIFE den Computer über WinRM nach Name und Domäne
  - antwortet ein anderes Gerät, ein Gerät außerhalb der Domäne oder – unter einer zuvor anderen Adresse – eines ohne WinRM: offline, **gelber Punkt** mit Begründung, Hinweis in der Computeransicht mit „Jetzt erneut prüfen“, neue Standardsammlung **„Falsche Adresse“**, Spalte „DNS-Adresse“
  - Scans und Bereitstellungen prüfen das vorher ebenfalls – sie laufen nie unter einer veralteten Adresse auf einem fremden Gerät
  - Problemfälle werden höchstens stündlich erneut gefragt; eine abgelehnte Anmeldung bei einem fremden Gerät löst keinen Dienstkonto-Schutz aus
- **Namensauflösung einstellbar (Einstellungen → Namensauflösung):** DNS-Cache mit einstellbarer Zeit und Knopf **„DNS-Cache leeren“**, wahlweise die DNS-Server der Domäne direkt fragen (an Zwischenspeichern des Docker-Hosts vorbei), „Auflösung testen“
- **AD-Sammlungen im Baum nicht mehr verschiebbar** – nur noch auf- und zuklappen (vorher entstand beim Ziehen eine Kopie)
- Selbsttest um 1 Prüfung erweitert (55 insgesamt)
## pocketknife-2.19 (21.10.2026)
- **Sammlungen: Tiefenbegrenzung getrennt gezählt** – Untergruppen im Filter (bis 10 Ebenen je Sammlung) und Verweise „Mitglied von“/übergeordnete Sammlungen (bis 12 hintereinander) haben jetzt eigene Grenzen. Bisher teilten sich beide einen Zähler: eine Kette aus vier, fünf aufeinander aufbauenden Sammlungen mit einer Untergruppe am Ende galt schon als „zu tief verschachtelt“
- **Speichern bricht keine abhängige Sammlung mehr still:** Beim Speichern oder Verschieben prüft POCKET KNIFE auch alle Sammlungen, die auf der geänderten aufbauen. Würde eine davon nicht mehr funktionieren, wird nicht gespeichert und die Meldung nennt sie. Bisher zeigte die abhängige Sammlung danach 0 Computer, und Scanprofile oder Zeitpläne mit diesem Ziel versorgten ohne Meldung niemanden mehr
- Selbsttest um 1 Prüfung erweitert (54 insgesamt)
## pocketknife-2.18 (21.10.2026)
- **Aufräumen nach einem Neustart von POCKET KNIFE** (Update, Neustart des Docker-Hosts) während laufender Bereitstellungen – bisher blieben auf dem Client Installer, geplante Aufgaben und Installationsdateien liegen
  - unterbrochene Ziele werden gemerkt und auf dem Computer aufgeräumt, sobald er erreichbar ist (alle 5 Minuten): Arbeitsordner `C:\Windows\Temp\PocketKnife\…`, Aufgaben `\POCKETKNIFE\PK_…`
  - ein noch laufender Installer wird **abgewartet**, nicht abgebrochen – erst nach Ablauf der Zeitgrenze des Schritts beendet (wie eine normale Zeitüberschreitung)
  - eine neue Bereitstellung auf denselben Computer räumt vorher auf und wartet, bis der alte Installer fertig ist (kein Fehler 1618, keine zwei Installationen gleichzeitig); Abbrechen bleibt möglich
  - Ergebnis in der Meldung des Ziels (z. B. „der unterbrochene Schritt lief noch zu Ende, Exitcode 0“) und im Protokoll; nach 14 Tagen ohne Erreichbarkeit wird aufgegeben (Reste > 2 Tage entfernt ohnehin der Inventar-Scan)
- Hilfe/Handbuch: neuer Abschnitt „Neustart von POCKET KNIFE während einer Bereitstellung“
- Selbsttest um 1 Prüfung erweitert (53 insgesamt)
## pocketknife-2.17 (21.10.2026)
- **Kiosk: Software nur für bestimmte Gruppen freigeben** – im Katalog-Dialog „Nur für Gruppen“: eine oder mehrere AD-Gruppen (Name oder DN, auch verschachtelt), bequem per „Aus dem AD wählen …“. Nur deren Mitglieder sehen und bestellen die Software; leer = alle Kiosk-Nutzer. Gilt zusätzlich zu „Nur für“ (Sammlung von Computern)
  - geprüft beim Anzeigen des Katalogs und beim Bestellen (auch über die Schnittstelle), mit dem Dienstkonto; Ergebnis 5 Minuten zwischengespeichert, nach dem Speichern sofort neu
  - ist das AD gerade nicht erreichbar, bleibt eingeschränkte Software ausgeblendet
  - neue Spalte „Nur für Gruppen“ in der Katalogliste
- Selbsttest um 1 Prüfung erweitert (52 insgesamt)
## pocketknife-2.16.1 (21.10.2026)
Code-Review über das ganze Programm – behobene Fehler:
- **Anmeldung**
  - Die Sitzung gehört jetzt immer dem Konto, dessen Kennwort geprüft wurde (LDAP „Who am I?“). Vorher konnte ein Name, der als UPN auf ein anderes Konto passte, die Sitzung eines gleichnamigen Kontos ergeben
  - Strikte Zertifikatsprüfung: ließ sich das LDAPS-Zertifikat nicht lesen, ging das Kennwort trotzdem hinaus – jetzt wird abgelehnt
  - Fehlermeldungen, die nur nach richtigem Kennwort möglich sind (Zugriffsgruppe nicht gefunden, LDAP-Suche unvollständig), verraten das nicht mehr
  - Der Notfallzugang hat einen eigenen Fehlversuchs-Zähler – Fehlversuche anderer hinter derselben Adresse (NAT, Terminalserver) sperren ihn nicht mehr aus
  - Netzwerkfehler während der Anmeldung ergeben eine verständliche Meldung statt „Interner Fehler“; Fehlerdetails sieht nur noch ein angemeldeter Administrator
- **Windows-Anmeldung (SSO):** nur Tickets für den HTTP-Namen des Kiosks; nur genau das Konto aus dem Ticket; fehlende Kiosk-Berechtigung zählt nicht als Fehlversuch
- **Kiosk**
  - Doppelklick oder zwei Tabs konnten zwei Installationen bzw. Neustarts starten und das Tageslimit umgehen
  - Zwei gleichzeitige Freigaben – oder Zurückziehen während der Freigabe – starteten doppelt bzw. trotzdem
  - Nach einem Neustart aus dem Kiosk erschien sofort wieder „Neustart erforderlich“
  - Ein lokales Konto (PC\max) oder ein Konto einer anderen Domäne galt als der Kiosk-Nutzer „max“
  - Der Rechner, vor dem der Nutzer sitzt, konnte bei vielen alten Anmeldungen aus der Liste fallen
  - „Anfrage zurückziehen“ nur noch bei eigenen Anfragen; Windows-Anmeldung bleibt bei einem Fehler nicht mehr beim Ladekreis hängen; Namen werden nicht mehr übersetzt
- **Softwareverteilung**
  - Mehrere Pakete (Mehrfachauswahl, Zeitplan) mit „nach Fehler weiter“: ein fehlgeschlagenes Paket ergab trotzdem „Erfolgreich“ – der Computer wurde dann nie erneut versorgt
  - Die Neustart-Einstellung der einzelnen Pakete galt in Zeitplänen und bei mehreren Paketen nicht
  - „Abbrechen“ und sofort „Löschen“ ließ den laufenden Computer weiterarbeiten; Löschen wartet jetzt, bis der Abbruch fertig ist
  - Mehrere Bereitstellungen für denselben Computer (z. B. mehrere Deinstallationen, winget-Updates) liefen gleichzeitig – jetzt nacheinander (kein Fehler 1618 mehr)
  - Zeitpläne mit Einrichtungsfehler (z. B. kein Ziel im Mandanten) wurden endlos alle 15 Minuten wiederholt; Abbrüche zählten als Fehlversuch
  - Abbruch genau beim Start einer geplanten Bereitstellung ließ sie für immer auf „läuft“; Dateinamen mit [ ] wurden nicht gefunden
- **Daten und Benachrichtigungen**
  - Die RegEx-Prüfung ließ Muster wie ((a+))+ oder (.*a){12} durch, die den Server lahmlegen können
  - „Sicherung fehlgeschlagen“ kam alle 10 Minuten per Mail (jetzt einmal am Tag); „Scan fehlgeschlagen“ meldete während jedes erneuten Versuchs „Behoben“
  - Zahlenfilter werteten leere und nicht numerische Werte als 0 und „2,5“ als 2
  - Löschen einer Sammlung: Verweise aus gleichnamigen Kopien wurden übersehen
  - Berichte mit Dezimalzahl bei „Tage“ schlugen beim Versand fehl; „nan“ als Grenzwert wurde angenommen
- **Oberfläche und Tools**
  - Arbeiten nur im Fernsitzungs-Tab führte zur Abmeldung wegen Inaktivität – und damit zum Abbruch der Sitzung
  - Tools: Werte vom Client (z. B. ein Benutzername wie „x&calc“) wurden ungeprüft in PowerShell/CMD eingesetzt – jetzt sicher als Variable bzw. abgelehnt
  - In Englisch wurden Daten übersetzt (eine Sammlung „Drucker“ hieß „Printers“, der Ordner „Daten“ „Data“)
  - Hochladen im Datei-Explorer fragt jetzt, bevor eine vorhandene Datei überschrieben wird
  - „null“ bzw. „undefined“ in der Anzeige (BitLocker-Prozent, Paketquelle); Exitcode bei Fehlern in Tool-Läufen
- **Neu:** Link zur Webseite [helfi.space](https://helfi.space) in der Navigation, auf der Anmeldeseite, in der Hilfe und im Handbuch
- Selbsttest um 6 Prüfungen erweitert (51 insgesamt)
## pocketknife-2.16 (21.10.2026)
- **Neu: Anmeldung mit Windows (SSO) am Kiosk** – optional, **standardmäßig aus**. Eingeschaltet meldet der Kiosk Nutzer an Domänen-PCs automatisch mit ihrem Windows-Konto an (Kerberos/SPNEGO), ohne Benutzername und Passwort; das Formular bleibt als Rückfall
  - Einrichten unter **Kiosk → Einstellungen → Windows-Anmeldung**: Keytab hochladen, einschalten, **Testen** (prüft Name, Realm, Verschlüsselung, Schlüssel und DNS)
  - Ergibt **immer nur eine Kiosk-Sitzung** – auch für Mitglieder der Zugriffsgruppe; die Verwaltung verlangt weiterhin das Kennwort. Berechtigung wie bisher über Kiosk-Gruppen/-OUs (mit dem Dienstkonto geprüft), deaktivierte Konten werden abgelehnt
  - Keytab **verschlüsselt** in der Datenbank (wie die Kennwörter), zur Prüfung nur im Arbeitsspeicher entschlüsselt; nie über die Einstellungs-Schnittstelle abrufbar
  - Ein automatischer Versuch je Tab; nach Abmelden oder Inaktivität erst wieder per Knopf „Mit Windows anmelden“. NTLM-Antworten (Aufruf per IP) werden erkannt und mit der richtigen Adresse erklärt – ohne Anmeldefenster-Schleife
  - **Anleitung** in der Hilfe und im Handbuch: DNS, SPN-Konto, `setspn`, `ktpass`, GPO für Edge/Chrome/Firefox, Fehlersuche
- **Behoben:** In **Berichte** stand in der Werkzeugleiste hinter „Per E-Mail …“ das Wort „null“
- Hilfe: Unterpunkte und Codeblöcke innerhalb nummerierter Schritte werden jetzt richtig dargestellt (vorher als weitere Nummer bzw. als Fließtext)
- Selbsttest um 2 Prüfungen erweitert (45 insgesamt)
## pocketknife-2.15.4 (21.10.2026)
- **Behoben: „Aus dem AD wählen …“ brach ab** mit „AD nicht erreichbar: 'list' object has no attribute 'lower'“. Das AD liefert jedes Attribut als Liste (POCKET KNIFE fragt das Schema nicht ab); Baum und Gruppensuche lesen die Werte jetzt wie der AD-Abgleich
- Selbsttest prüft das mit AD-typischen Antworten
## pocketknife-2.15.3 (21.10.2026)
- **Pakete: Mehrfachauswahl und „Bereitstellen …“ oben rechts** (neben „Neues Paket“) – mehrere Pakete anhaken bzw. mit Strg/Umschalt markieren, dann „N Pakete bereitstellen …“ (auch per Rechtsklick, dort zusätzlich „Zeitplan anlegen …“ mit allen gewählten Paketen)
- **Bereitstellen-Dialog mit mehreren Paketen:** Suche, Häkchen, gewählte Pakete als nummerierte Einträge – Reihenfolge per Ziehen änderbar. Mehrere Pakete laufen als **eine** Bereitstellung, je Computer nacheinander (nie zwei Installationen gleichzeitig, kein Fehler 1618); optional nach einem Fehler die weiteren Pakete auslassen
- Selbsttest prüft Reihenfolge, doppelte Pakete und Mandantentrennung
## pocketknife-2.15.2 (21.10.2026)
- **Kiosk-Zugang aus dem AD-Baum wählen** – neben „Gruppen“ und „OUs“ gibt es „Aus dem AD wählen …“: aufklappbarer Baum der Domäne (OUs und Container, bei Gruppen auch die Gruppen darin, mit Typ und Beschreibung), Häkchen setzen, „Übernehmen“. Gruppen zusätzlich per Suche (Name oder Beschreibung). Übernommen werden Gruppen als Kontoname, OUs als DN – ohne Doppelte; das Textfeld bleibt zum Nachbearbeiten
- Durchsucht wird nur innerhalb der eigenen Domäne (Basis-DN), mit dem Dienstkonto
## pocketknife-2.15.1 (21.10.2026)
- **Kiosk: Symbol je Software** – im Katalog-Dialog ein Bild hinterlegen (PNG, JPEG, GIF, WebP oder ICO, höchstens 512 KB, am besten 128 × 128 Pixel). Es erscheint auf der Kachel im Kiosk und in der Katalogliste; ohne Symbol bleiben die Anfangsbuchstaben
  - SVG wird aus Sicherheitsgründen abgelehnt (kann Skripte enthalten); das Bildformat wird am Inhalt geprüft, nicht an der Dateiendung
- Selbsttest um diese Prüfung erweitert (42 insgesamt)
## pocketknife-2.15 (21.10.2026)
- **Neu: Software-Kiosk** – Nutzer installieren freigegebene Software selbst unter `https://<server>:8443/kiosk`, angemeldet mit ihrem Windows-Konto
  - **Zugang** frei einstellbar über AD-Gruppen (auch verschachtelt) und/oder OUs (mit Unter-OUs); Kiosk-Anmeldungen haben keinerlei Zugriff auf die Verwaltung oder Fernsitzungen (jede Verwaltungs-Schnittstelle verlangt die Rolle „admin“ – prüft der Selbsttest)
  - **Nur der eigene Rechner:** POCKET KNIFE prüft live per WinRM, an welchem Computer der Nutzer gerade angemeldet ist – auch beim Absenden jeder Anfrage
  - **Katalog** aus vorhandenen Paketen: Kategorie, Beschreibung, je Paket **sofort** oder **nach Freigabe** (Mail an die IT), optional Deinstallationspaket und „nur für Sammlung“
  - **Für Nutzer:** Installieren/Anfordern, Deinstallieren, Status live, Anfragen zurückziehen, **Neustart planen** (jetzt, in 1 Stunde, zu einer Uhrzeit – absagbar); Suche und Kategorien; hell/dunkel, Deutsch/Englisch, auch auf dem Handy bedienbar
  - **Verwaltung** unter **Kiosk**: Anfragen freigeben/ablehnen (mit Begründung), Katalog, Einstellungen; höchstens N Anfragen je Nutzer in 24 Stunden
  - Installationen aus dem Kiosk sind normale Bereitstellungen („Kiosk: …“) mit Protokoll und Scan danach; Protokoll-Kategorie „Kiosk“
- Selbsttest um 3 Prüfungen erweitert (41 insgesamt)
## pocketknife-2.14.4 (20.10.2026)
- **Sammlungsbaum (und Berichtsliste) in der Breite änderbar** – den Rand rechts daneben ziehen, Doppelklick = Standardbreite; wird im Browser gemerkt
- **Schmale Bildlaufleisten** statt der breiten grauen Standardleiste
- **Spaltenköpfe mit sichtbaren Trennlinien**; der Rand zum Ändern der Breite leuchtet beim Darüberfahren auf
- **Hell/Dunkel mit einem Klick** – Knopf ☀ / ☾ unten links neben DE/EN („Wie System“ weiter unter Einstellungen → Wartung)
## pocketknife-2.14.3 (20.10.2026)
- **Tabellen: mehr Überblick auf kleinen Bildschirmen**
  - **Spaltenbreite** ändern: am rechten Rand des Spaltenkopfs ziehen; zu lange Inhalte werden mit … gekürzt (voller Text als Tooltip); Doppelklick auf den Rand = automatische Breite
  - **Spalten verschieben** auch ans Ende: auf die rechte Hälfte einer Spalte ziehen = dahinter (bisher nur davor)
  - Knopf **Spalten** öffnet ein Fenster, das offen bleibt: ein-/ausblenden, Reihenfolge per Ziehen (⠿) oder ▲▼ – ohne seitliches Scrollen der Tabelle; „Alle einblenden“, „Zurücksetzen“ (auch Breiten)
  - Breiten werden wie Reihenfolge und Sortierung je Ansicht im Browser gemerkt
## pocketknife-2.14.2 (20.10.2026)
- **Alle Versionen festgeschrieben** – genau der Stand, der in der laufenden Installation geprüft funktioniert:
  - Basis-Image `python:3.12.15-slim-trixie@sha256:a6e34c59…` (Python 3.12.15, Debian 13.7)
  - Debian-Pakete über den Stand `20261020T150000Z` von snapshot.debian.org, Laufzeitpakete zusätzlich mit Version (tzdata 2026c-0+deb13u1, krb5 1.21.3-5+deb13u1, iputils-ping 3:20240905-3)
  - alle 30 Python-Pakete samt Abhängigkeiten in `requirements.lock` (u. a. fastapi 0.155.0, uvicorn 0.54.0, starlette 1.9.0, pydantic 2.14.1, cryptography 50.0.2, pypsrp 0.9.1, smbprotocol 1.17.0, pyspnego 0.12.4, gssapi 1.12.0, krb5 0.10.0, ldap3 2.9.1, websockets 17.3)
  - guacd `1.6.0@sha256:8974eaa9…`
- Ist snapshot.debian.org vom Docker-Host nicht erreichbar, baut das Image mit Warnung gegen die normalen Paketquellen weiter (statt abzubrechen)
- Selbsttest prüft, dass alles festgeschrieben bleibt
## pocketknife-2.14.1 (20.10.2026)
- **Behoben: Container startete nicht** („failed to write sysctl net.ipv4.ping_group_range … invalid argument“). Die in 2.14 gesetzten sysctls sind entfernt – Docker ab 20.10 erlaubt Ports unter 1024 und ping ohne root ohnehin; je nach Docker-Konfiguration (z. B. mit User-Namespaces) war der feste Wert ungültig
- Selbsttest prüft das mit
## pocketknife-2.14 (20.10.2026)
- **Dienstkonto-Schritte ohne geplante Aufgabe und ohne Kennwort auf dem Client** – bisher legte POCKET KNIFE für „Ausführen als Dienstkonto“ eine geplante Aufgabe mit dem Kennwort des Dienstkontos an. Jetzt startet es den Schritt über WinRM direkt als Prozess des Dienstkontos (WMI `Win32_Process`): volle Administratorrechte, übersteht Verbindungsabbrüche, wird bei Abbruch/Zeitüberschreitung samt Unterprozessen beendet
  - „Lokales System“ und „Angemeldeter Benutzer“ nutzen weiter eine temporäre geplante Aufgabe – nie mit Kennwort
  - Reste älterer Versionen (Aufgaben unter `\POCKETKNIFE\` mit gespeichertem Kennwort) entfernt der nächste Scan sofort
  - Hinweis: Ein Schritt als Dienstkonto erreicht dadurch keine Netzwerkfreigaben mehr; Repository-Dateien kopiert POCKET KNIFE wie bisher vorher selbst
- **Container läuft nicht mehr als root** – Benutzer `pocketknife` (UID/GID 10001, `PK_UID`/`PK_GID`), schreibgeschütztes Programmdateisystem, keine Capabilities, `no-new-privileges`; `/data` und `/backup` werden beim ersten Start automatisch übergeben. Auch guacd ohne Rechteausweitung
- **Feste Versionen für reproduzierbare Builds** – Basis-Image mit exakter Python-Version und Digest, Debian-Pakete über einen festen Stand von snapshot.debian.org, alle Python-Pakete samt Abhängigkeiten (`requirements.lock`, `--no-deps` + `pip check`), Build-Werkzeuge (Cython, setuptools) auch in den isolierten Build-Umgebungen. Neu `tools/pin-versions.sh` zum Festschreiben und bewussten Aktualisieren (mit Kontroll-Neubau und Selbsttest)
- **Abmeldung bei Inaktivität** – Standard 30 Minuten ohne Maus/Tastatur (5–720, Einstellungen → Verbindung & Konten, immer aktiv); automatische Aktualisierungen zählen nicht, Eingaben in Fernsitzungen schon; Hinweis kurz vorher; offene Fernsitzungen werden mit beendet
- **Repository: neuer Server/neue Freigabe nur mit Kennwort-Bestätigung** – die gespeicherte Anmeldung geht nie an einen vertippten oder fremden Server (Unterordner derselben Freigabe und `/repo` ohne Bestätigung)
- **Zeitpläne holen Fehlschläge nach** – neue Option „Fehlgeschlagene nachholen“ (Standard an, auch für bestehende Zeitpläne): fehlgeschlagene Computer werden nach der Wartezeit (mind. 1 Stunde) automatisch erneut versorgt, höchstens „Max. Fehlversuche“ Mal (bei 0 dreimal), im Zeitfenster. Ein Lauf, der gar nicht starten konnte, geht nicht mehr verloren, sondern wird alle 15 Minuten wiederholt
- Selbsttest um 5 Prüfungen erweitert (35 insgesamt)
## pocketknife-2.13 (20.10.2026)
Gesamtprüfung auf Sicherheit, Fehler und Logikfehler.

**Sicherheit**
- Anmeldung: Fehlversuchs-Sperre jetzt ohne Lücke bei gleichzeitigen Anfragen; Meldung verrät nicht mehr, ob Kennwort oder Gruppenmitgliedschaft das Problem war; ein AD-Konto mit dem Namen des Notfall-Kontos wird abgelehnt; nur echte Notfall-Sitzungen überspringen die AD-Prüfung
- Größenbegrenzung für Anfragen (Standard 2 MB, Uploads 2 GB, winget 310 MB) – übergroße Anfragen werden mit 413 abgewiesen
- Interne Fehler zeigen vor der Anmeldung keine Details mehr
- Tools: URL-Werte werden kodiert, `javascript:`/`data:`-Adressen abgelehnt; PowerShell-Tools erhalten `$PK_Computer`, `$PK_User` … sicher zitiert
- Verbindungstest nur noch zu bekannten Computern bzw. Namen der Domäne
- Arbeitsordner auf den Clients wird gleich mit geschützten Rechten angelegt; Arbeitsordner je Lauf mit Zufallsanteil
- Benutzer-Deinstallationen (Einträge aus dem Benutzerprofil) laufen zwingend als angemeldeter Benutzer statt mit Administratorrechten
- Reguläre Ausdrücke in Filtern und Einstellungen: Schutz gegen Muster mit extremer Laufzeit
- E-Mail: Anmeldung am SMTP-Server nur verschlüsselt; nach Änderung von Server/Port/Verschlüsselung/Benutzer muss das Kennwort neu eingegeben werden (es geht nie an einen anderen Server)
- LDAPS „strikt prüfen“ ist für neue Installationen Standard; neu `PK_WINRM_CA_FILE` zur Zertifikatsprüfung bei WinRM über HTTPS
- Zertifikats- und Schlüsseldateien werden nur noch mit Rechten 600 angelegt; Dateinamen im Explorer strenger geprüft; CSV-Kopfzeilen gegen Formel-Injektion geschützt

**Fehler**
- AD-Abgleich: ohne eingeschlossenen Container wird nichts mehr gelöscht; Computer werden erst nach 7 Tagen Abwesenheit im AD entfernt; die Schutzgrenze zählt nur vorhandene Computer
- Scan: Schlägt die Softwareabfrage fehl, bleibt die bisherige Softwareliste erhalten (vorher konnte sie leer überschrieben werden und falsche „deinstalliert“-Einträge erzeugen)
- Bereitstellungen: ein Ziel kann nicht mehr doppelt starten; ein Absturz in einem Ziel blockiert nicht mehr die Bereitstellung
- Zeitpläne: hängengebliebene Ziele alter Läufe blockieren nicht mehr; Wöchentlich startet nie vor dem Startdatum; winget-Updates beachten die maximale Versuchszahl
- Ein fehlerhaftes Scanprofil oder ein fehlerhafter eigener Scanner stört die anderen nicht mehr
- Nächtliche Wartung und Sicherung laufen nach einem Neustart auch verspätet noch; Sicherung behält immer die letzte „vor Update“-Sicherung
- Aufräumen: alte Temp-Dateien im Container und vergessene Aufgaben/Ordner auf den Clients werden entfernt; ungenutzte SMB-Verbindungen werden geschlossen
- Oberfläche: schnelles Klicken im Explorer/in der Ereignisanzeige zeigt keine veralteten Ergebnisse mehr; Fernsitzung „Abbrechen“ funktioniert beim Verbinden; doppelte Sammlung beim erneuten Speichern behoben; leere Werte immer am Ende sortiert; Dateinamen, Kennwörter und Meldungen der Clients werden nicht mehr „übersetzt“

**Logik**
- Sammlungen: Löschen wird verweigert, solange die Sammlung noch verwendet wird (Filter, Zeitplan, Bericht, Benachrichtigung …); Zirkelbezüge werden beim Speichern abgelehnt; benutzerdefinierte Felder ebenso geschützt
- Zahlenvergleiche verstehen Einheiten („16 GB“); Versionsvergleich ignoriert Zusätze wie „(x64)“
- Benachrichtigungen: Prüfungen laufen nie doppelt, Fehler mit wachsendem Abstand und nur einmal protokolliert; eigene Berichte mit sehr vielen Zeilen melden nichts fälschlich als „behoben“; nach Änderung eines Berichts wird neu eingelesen statt alles als neu zu melden; „Festplatte voll“ meldet Laufwerke außerhalb des Bereichs als behoben
- Empfänger im Format `Name <adresse>` werden angenommen

- Selbsttest um 15 Prüfungen erweitert (30 insgesamt; 14 davon schlagen mit 2.12.3 fehl)
## pocketknife-2.12.3 (20.10.2026)
- **Behoben: Dateien (Explorer) – Ordner mit Verknüpfungen ließen sich nicht öffnen.** `C:\Users` brach mit „Interner Fehler (SMBLinkRedirectionError … All Users …)“ ab: Windows legt dort Verknüpfungen an (z. B. „All Users“ → `C:\ProgramData`, „Dokumente und Einstellungen“ → `C:\Users`), denen der SMB-Server nicht folgt
  - Die Liste folgt Verknüpfungen nicht mehr und zeigt sie mit ↗; ein nicht lesbarer Eintrag bricht nie mehr die ganze Liste ab
  - Öffnen einer Ordner-Verknüpfung springt wie im Windows-Explorer zum Ziel (Hinweis „Verknüpfung: … → …“); bei Datei-Verknüpfungen wird das Ziel heruntergeladen
  - **Löschen** einer Verknüpfung entfernt nur die Verknüpfung, nie ihr Ziel; Kopieren von Verknüpfungen wird mit Hinweis abgelehnt
  - Fehler beim Herunterladen (gesperrt, nicht gefunden, zu groß) erscheinen als Meldung, statt den Browser auf eine Fehlerseite zu schicken
  - Gleiche Absicherung für das Durchsuchen des Repositorys (Paketordner)
- Selbsttest um diesen Fall erweitert (schlägt mit der Vorversion fehl)
## pocketknife-2.12.2 (20.10.2026)
- **Auswahllisten zeigen immer alle Einträge** – Eingabefelder mit Vorschlägen (Spalten und Werte im Filter-Editor von Sammlungen und Berichten, Größen, Tool-Ordner, Pfade der Speicherplatz-Analyse) zeigten nur Einträge passend zum schon eingetragenen Wert; man musste ihn erst löschen. Jetzt: Klick ins Feld oder auf ▾ zeigt alle, beim Tippen wird gefiltert, Auswahl mit Maus oder ↑/↓ + Enter, Esc schließt nur die Liste
- **Pakete: Schritte beschriften** – jeder Schritt hat oben das Feld **Bezeichnung** (z. B. „Wazuh-Agent installieren“); sie erscheint in der Schrittliste links und im Bereitstellungsprotokoll. Leer = automatische Bezeichnung wie bisher
- **Pakete: Neues Paket beginnt bei den Paket-Eigenschaften** mit dem Cursor im Feld **Name** (bisher sprang der Editor gleich in den ersten Schritt)
- Selbsttest prüft beides mit
## pocketknife-2.12.1 (20.10.2026)
- **Größenangaben eigener Scanner überall verständlich** – bisher kannte nur die Anzeige und der Rechtsklick „Sammlung erstellen“ (2.6.2) die Größenspalten; im Filter-Editor von Sammlungen und Berichten erschienen weiter „enthält“ und Bytewerte
  - Größenspalten (automatisch erkannt oder fest eingestellt beim Scanner unter „Größenangaben“) haben jetzt im Filter **≥, ≤, >, <, =, ≠** und nehmen Werte **mit Einheit** an (`500 MB`, `10 GB`, `1,5 TB`); Vorschläge lesbar (`1 GB`, `5 GB`, `10 GB` …) statt `1009491769`
  - Nach Eingabe des Spaltennamens wechseln Operatoren und Eingabefeld sofort passend zur Spalte
  - Bestehende Bedingungen (z. B. „Size enthält …“) bleiben unverändert gültig
  - **Eigene Berichte**: Größenspalten in Tabelle, Vorschau und Mail als KB/MB/GB/TB (genauer Bytewert als Tooltip), **numerisch sortiert**; der CSV-Export enthält wie beim Scanner weiterhin den Bytewert. Bei Benachrichtigungen gilt eine geänderte Größe als derselbe Fall
- **Regressionstests** `tools/selftest.py` (auch im Image: `docker compose exec pocketknife python3 tools/selftest.py`): prüft bereits behobene Fehler automatisch – E-Mail mit STARTTLS/SSL und Zertifikatsprüfung (2.11.3), HELO-Name und Kodierung (2.11.2), Muster mit eckigen Klammern (2.7), Anmeldesperre (2.6.3), Einheiten in Zahlenvergleichen (2.6.2), Pfadprüfung im Explorer (2.9), CSV-Formel-Injektion, eigene Berichte, Benachrichtigungen. Läuft vor jeder neuen Version
## pocketknife-2.12 (20.10.2026)
- **Neu: Eigene Berichte** – unter **Berichte → Eigene Berichte → Neuer Bericht …** aus allen Inventardaten selbst zusammenstellen
  - **Datenquelle** bestimmt, wofür eine Zeile steht: Computer, Anwendungen, Laufwerke, Hotfixes, Dienste, lokale Administratoren/Benutzer/Gruppen, Drucker, Bildschirme, Netzwerkkarten, Speichermodule, Freigaben, geplante Aufgaben, Windows-Features, Bereitstellungen, Softwareänderungen, **eigene Scanner** …
  - **Spalten** frei aus Computer + Datenquelle (inkl. benutzerdefinierter Felder), Reihenfolge und Bezeichnung änderbar
  - **Filter**: welche Zeilen (Bedingungen an die einzelne Zeile) und welche Computer (wie dynamische Sammlungen, mit verschachtelten Gruppen), optional eine Sammlung
  - **Zusammenfassen** (gleiche Zeilen zählen, z. B. „wie viele Computer haben welche Version“), Sortierung, Höchstzahl Zeilen; **Vorlagen** als Startpunkt; **Vorschau** ohne Speichern
  - Wie vorgegebene Berichte nutzbar: Sammlungsauswahl, CSV, Drucken, **Per E-Mail …** und im **Berichtsversand**; Duplizieren
- **Eigene Berichte als Benachrichtigung** – je eigenem Bericht eine Benachrichtigung „Mail, sobald der Bericht Zeilen liefert“ (Einstellungen → E-Mail & Benachrichtigungen oder **Benachrichtigung …** im Bericht). Jede Zeile wird einmal gemeldet (Änderungen an Zahlen/Datumswerten gelten als derselbe Fall), mit Erinnerung/Entwarnung wie gewohnt; Prüfabstand einstellbar (Standard 15 Minuten)
- Berichteliste gegliedert in **Eigene Berichte** und **Vorgegeben**; Datums- und Zahlenspalten werden in Berichten passend dargestellt
## pocketknife-2.11.3 (20.10.2026)
- **Behoben: „check_hostname requires server_hostname“** – mit STARTTLS oder SSL/TLS **und** eingeschalteter Zertifikatsprüfung schlug der Versand im Container (Python 3.12) fehl; Fehler aus 2.11.2. Jetzt mit der Python-Version des Containers gegen einen TLS-Testserver geprüft (STARTTLS und SSL/TLS, Zertifikatsprüfung an)
- **Testmail – Hinweise zu riskanten Einstellungen:** Absender weicht vom Anmeldekonto ab (Microsoft 365/Exchange lehnen ab oder stufen als Spam ein), Versand ohne Anmeldung an fremde Server, unverschlüsselt, Zertifikatsprüfung aus
- Beispieltexte in den leeren E-Mail-Feldern beginnen mit „z. B.“ – nicht mehr mit eingetragenen Werten zu verwechseln
## pocketknife-2.11.2 (20.10.2026)
- **E-Mail – Fehlersuche, wenn nichts ankommt:** Die Testmail zeigt jetzt den vollständigen **SMTP-Dialog** und die Antwort des Servers mit **Queue-ID** sowie die Message-ID – damit lässt sich die Mail im Protokoll des Mailservers verfolgen (Exchange: Nachrichtenablaufverfolgung). Dazu eine Checkliste der häufigsten Ursachen (Quarantäne, nicht existierender Absender, Relay nach außen, SPF/DMARC). Auch Protokoll und „Jetzt senden“ nennen die Serverantwort; bisher stand dort nur „gesendet“
- **E-Mail – zuverlässigere Zustellung:**
  - Anmeldung beim Mailserver (EHLO) mit vollständigem Namen `pocketknife.<domäne>` statt der zufälligen Container-ID – solche Namen werten viele Spamfilter ab oder verwerfen die Mail stillschweigend
  - Texte immer quoted-printable kodiert (auch über Server ohne 8BITMIME korrekt)
  - Empfänger werden einzeln übergeben; abgelehnte Empfänger erscheinen mit der Begründung des Servers
## pocketknife-2.11.1 (20.10.2026)
- **Speicherplatz → Explorer:** aus der Speicherplatz-Analyse direkt in **Dateien** springen – Ordnersymbol in jeder Zeile, Knopf „Im Explorer öffnen“ über dem Diagramm oder Rechtsklick. Bei Dateien öffnet sich der Ordner mit der Datei markiert; „Zurück“ im Browser führt wieder zur Analyse
- **Dateien** und **Ereignisanzeige** nutzen jetzt die volle Fensterhöhe (vorher war die Liste auf wenige Zeilen begrenzt); nur die Liste scrollt, Werkzeugleiste und Pfad bleiben stehen
## pocketknife-2.11 (20.10.2026)
- **Neu: E-Mail (SMTP)** – unter **Einstellungen → E-Mail & Benachrichtigungen**: SMTP-Server, Port, STARTTLS/SSL/unverschlüsselt, Zertifikatsprüfung, optional Anmeldung (Kennwort verschlüsselt im Tresor), Absender, Standard-Empfänger, Betreff-Präfix; **Testmail senden** mit verständlichen Fehlermeldungen. Keine neue Abhängigkeit (Python-Standardbibliothek)
- **Neu: Berichte per E-Mail** – unter **Berichte → E-Mail-Versand** oder direkt mit **Per E-Mail …** in einem Bericht
  - Einen oder mehrere vorhandene Berichte mit ihren Parametern zu einer Mail zusammenstellen (Reihenfolge änderbar)
  - Zeitplan wie bei Scanprofilen: einmalig, Intervall, täglich, wöchentlich, monatlich (mit Ende); „Nächster Versand“ in der Liste
  - Mail mit Tabelle je Bericht (Zeilenzahl einstellbar) und **CSV-Anhang** (Excel-kompatibel); optional **nicht senden, wenn alle Berichte leer sind**
  - **Vorschau** der fertigen Mail, **Jetzt senden …** (auch an eine einzelne Testadresse), Ergebnis in Liste und Protokoll
- **Neu: Benachrichtigungen bei Ereignissen** – jede Art einzeln einschaltbar (Standard: aus), eigene Empfänger möglich
  - **Wenig Speicherplatz** (GB- und/oder %-Grenze, nur bestimmte Laufwerke, Sammlung), **Computer lange offline**, **Scan fehlgeschlagen**, **Bereitstellung fehlgeschlagen** (optional auch teilweise), **Dienstkonto-Schutz ausgelöst**, **Sicherung fehlgeschlagen**
  - Jeder Fall wird nur einmal gemeldet, neue Fälle gesammelt in einer Mail; optional **Erinnerung** nach N Stunden und **Entwarnung** bei Behebung (Speicherplatz mit 10 % Hysterese gegen Hin und Her)
  - **Was würde jetzt gemeldet?** zeigt die aktuell zutreffenden Fälle, ohne zu senden; „Jetzt prüfen“ stößt die Prüfung sofort an
  - Geprüft wird jede Minute – auch während der Dienstkonto-Schutz aktiv ist; Schutz gegen Mailfluten (höchstens 120 Mails/Stunde)
- Protokoll: neue Kategorie **E-Mail**
## pocketknife-2.10 (20.10.2026)
- **Speicherplatz als eigener Scanner** – das Tool „Speicherplatz analysieren“ gibt es jetzt auch als festen, optionalen Scanner „Speicherplatz“ in den Scanprofilen
  - **Nicht im Standardscan** (belastet den Client stärker); wie die anderen Scanner frei in Profile aufnehmbar und über Auslöser/Sammlungen/Zeitfenster planbar – so entscheiden Sie, wann er läuft
  - Analysiert beim Lauf alle festen lokalen Laufwerke des Computers in einem Durchlauf; Tiefe einstellbar unter **Einstellungen → Zeitpläne & Leistung** (Standard 4)
  - Ergebnisse erscheinen beim Computer unter der neuen Kategorie **Speicherplatz** (Liste der Analysen) und im gewohnten Ringdiagramm; dort lässt sich jederzeit gezielt tiefer nachanalysieren
## pocketknife-2.9 (20.10.2026)
- **Neu: Dateien (Explorer)** – Dateizugriff auf den Client direkt im Browser, ohne etwas herunterzuladen oder auf dem Client zu installieren. Über die administrativen Freigaben (`\\PC\C$`, Dienstkonto = lokaler Administrator):
  - Laufwerke und Ordner durchsuchen, **herunterladen** und **hochladen**, **kopieren**/**verschieben** (auch ganze Ordner, Mehrfachauswahl), **umbenennen**, **löschen**, **neue Ordner**
  - Verknüpfungen (Junctions/Symlinks), versteckte und gesperrte Einträge gekennzeichnet; alle Änderungen werden protokolliert
  - Aufruf über **Tools ▾** oder beim Computer unter **Laufwerke**
- **Neu: Ereignisanzeige** – Windows-Ereignisprotokolle (System, Anwendung, Sicherheit, Setup, Defender) live über WinRM im Browser, mit Filter nach Stufe und Suche nach Text/Ereignis-ID/Quelle
- **Entfernt: „Am Arbeitsplatz öffnen“** (Ereignisanzeige/Computerverwaltung/Dienste/C$/PowerShell-Sitzung als `.cmd`-Download). Diese Programme laufen nur auf dem Rechner des Bedieners und passen nicht zu einer Weboberfläche; ihre Aufgaben übernehmen jetzt die beiden neuen browserbasierten Tools sowie die vorhandenen Ansichten **Dienste** und **Prozesse** beim Computer
## pocketknife-2.8 (20.10.2026)
- **Neues Tool „Speicherplatz analysieren“** – wo liegen die Speicherfresser? (Tools ▾ und beim Computer unter „Laufwerke“)
  - Ordnergrößen eines Laufwerks oder Ordners (auch `%SystemDrive%\Users` u. ä.) als **Ringdiagramm**: Mitte = aktueller Ordner, innerer Ring = Inhalt, äußerer Ring = eine Ebene tiefer; daneben eine Liste mit Größe, Anteil (Balken) und Dateizahl
  - **Zoomen** per Klick auf Ring oder Zeile, zurück über die Mitte, „Eine Ebene zurück“ oder den Pfad; Ordner unterhalb der gewählten Tiefe (3–6 Ebenen) werden per Klick gezielt nachanalysiert
  - Auf dem Client zählt ein kleiner C#-Helfer über WinRM (keine Installation, schnell auch bei vielen Dateien); Junctions/Symlinks werden nicht verfolgt, nicht lesbare Ordner gekennzeichnet, kleine Einträge zusammengefasst
  - Läuft auf dem Server weiter, auch wenn die Seite verlassen wird; die letzten 8 Ergebnisse je Computer werden gespeichert
  - Farben aus einer geprüften, farbenblind-tauglichen Palette, eigene Stufen für den dunklen Modus
## pocketknife-2.7 (20.10.2026)
- **Ausnahmen der Aktualitätsprüfung** – eigene Liste von Programmen, die nie als „veraltet“ gelten (Blacklist), z. B. Laufzeitbibliotheken oder Fachanwendungen mit vorgegebener Version
  - Muster mit `*` und `?` nach **Name**, **Hersteller** oder **winget-ID**, mit Notiz; Vorschau der betroffenen Programme beim Anlegen, Trefferzahl je Regel
  - Zu finden unter **Software → Ausnahmen …** und **Einstellungen → Repository & winget**; schnell per Rechtsklick auf ein oder mehrere Programme → „Von der Aktualitätsprüfung ausnehmen“; in den Programmdetails sichtbar, welche Regel greift
  - Ausgenommene Programme: Anzeige „ausgenommen“, zählen nicht als veraltet (Übersicht, Sammlung „Veraltete Software“), keine winget-Updates durch Zeitpläne; winget-Zuordnung und gezieltes Bereitstellen bleiben erhalten
  - Die bisher einzeln „ignorierten“ Programme werden in derselben Liste angezeigt und lassen sich dort zurücknehmen
## pocketknife-2.6.3 (20.10.2026) – Durchsicht & Politur
**Sicherheit**
- Anmeldesperre ließ sich mit `x\admin`, `y\admin` … umgehen: Benutzername wird jetzt normalisiert; zusätzlich höchstens 30 Fehlversuche je IP in 15 Minuten; alte Einträge werden aufgeräumt
- LDAPS: Zertifikat wird jetzt **vor** dem Senden des Kennworts geprüft, je Domänencontroller gemerkt; optional **strikt** (Änderung muss bestätigt werden, Knopf „Neuem Zertifikat vertrauen“) oder vollständige CA-Prüfung mit `PK_LDAP_CA_FILE`
- Laufende Sitzungen werden alle 15 Minuten gegen das AD geprüft (deaktiviert / aus der Zugriffsgruppe entfernt → abgemeldet)
- Abmelden und abgelaufene Sitzungen beenden auch offene RDP/VNC-Fernsitzungen
- Arbeitsordner `C:\Windows\Temp\PocketKnife` auf den Clients: Rechte nur SYSTEM/Administratoren; ein vorab von einem Benutzer angelegter Ordner wird beiseitegeschoben (verhindert das Austauschen von Skripten vor dem Start als SYSTEM). Bei „Angemeldeter Benutzer“ bekommt nur dieser Benutzer Schreibrechte auf den Laufordner, nicht mehr alle Benutzer
- Kerberos: falsches Kennwort/gesperrtes Konto löst jetzt auch bei WinRM und SMB den Dienstkonto-Schutz aus
- Sicherungen zum Herunterladen ohne `pocketknife.key` und HTTPS-Schlüssel (vollständige Sicherung bleibt im Sicherungsordner)
- CSV-Exporte (Berichte, Tabellen) gegen Formel-Injektion in Excel geschützt
- Mandanten: Pakete dürfen nur Dateien aus dem Repository-Ordner ihres Mandanten verwenden
- Einstellungen: interne und AD-Werte nicht mehr über die allgemeine Einstellungs-Schnittstelle änderbar, Grenzen für Zahlenwerte; Repository als Ordner nur unter `/repo`
- winget-Index-Upload blockiert den Server nicht mehr (Fernsitzungen froren ein) und ist auf 300 MB begrenzt
- „Angemeldete Benutzer abfragen“ als POST statt GET; Eingabefehler liefern 400 statt „Interner Fehler“; negative `limit`-Werte abgefangen

**Zuverlässigkeit**
- AD-Abgleich: eine fehlerhafte/unvollständige LDAP-Suche bricht jetzt ab, statt alle Computer als „verschwunden“ zu markieren (oder zu löschen); zusätzlich Schutz, wenn auf einmal mehr als 25 % fehlen würden
- Deaktivierte Computer (bei „deaktivierte nicht abgleichen“) gelten nicht mehr als verschwunden, sondern werden als „im AD deaktiviert“ markiert
- AD-Sammlungen, die von Zeitplänen, Scanprofilen oder Mandanten verwendet werden, bleiben erhalten, auch wenn die OU/Gruppe vorübergehend leer ist
- AD-Datumswerte (letzte Anmeldung, erstellt) bei Sommer-/Winterzeit nicht mehr um eine Stunde verschoben
- Automatische Sicherung konnte bei laufenden Schreibzugriffen endlos neu beginnen und danach alle weiteren Sicherungen blockieren
- „Protokoll aufbewahren: 0“ löschte stündlich alle Protokolle – jetzt „0 = nie“; Stunde 0 für Sicherung/Datenbankwartung einstellbar
- Zeitpläne: kein doppelter Lauf mehr, wenn Heartbeat und Minutentakt gleichzeitig dasselbe Ziel versorgen; geänderte oder wieder aktivierte Auslöser lösen nicht sofort aus (auch bei Scanprofilen); ein fehlerhafter Zeitplan hält die anderen nicht mehr auf
- Sammlungs-Mitgliedschaften werden kurz zwischengespeichert (spürbar weniger Last bei vielen Zeitplänen), Index für eigene Scanner
- VACUUM wird verschoben, solange Bereitstellungen oder Scans laufen
- WinRM-Aufrufe haben eine Obergrenze (Scan 20 Min., sonst 30 Min.) – ein hängender Client blockiert keine Scans mehr dauerhaft
- Befehl-Schritte und CMD-Tools: Umlaute in Pfaden/Befehlen werden richtig gelesen (Kodierung passend zur Konsole)
- winget: zusätzliche Argumente mit Anführungszeichen wurden doppelt gequotet
- Deinstallieren mit „Registry-Eintrag aufräumen“ meldete bei fehlgeschlagenem Uninstaller trotzdem Erfolg; „Ausführen als Dienstkonto“ prüfte bei Benutzer-Installationen den falschen Registry-Zweig
- Dateikopie nach `C:\` oder einen Ordner mit abschließendem `\` schlug fehl (robocopy)
- PowerShell-Tools meldeten Fehler, obwohl sie mit `-ErrorAction SilentlyContinue` unterdrückt waren
- Nachricht-Schritt und Neustart nach der Bereitstellung werten den Exitcode aus („Neustart war bereits geplant“)
- Scheitert die Registrierung der geplanten Aufgabe, werden Aufgabe und Arbeitsordner trotzdem entfernt; abgebrochene/halbe Paketkopien bleiben nicht mehr auf dem Ziel liegen; ein später Fehler überschreibt kein bereits gespeichertes Ergebnis mehr
- HTTPS: passen Zertifikat und Schlüssel nach einem Absturz nicht zusammen, wird beim Start die letzte gute Sicherung genommen

**Oberfläche**
- Übersicht: Aktualisierungs-Timer verdoppelten sich alle 30 Sekunden
- Schnelles Weiterklicken: eine langsam ladende Seite überschreibt nicht mehr die neue, alte Timer werden verworfen
- Esc schließt im Editor (Paket, Zeitplan, Scanprofil) nicht mehr ungefragt den ganzen Dialog; Esc im Kontext-, Tools- und Scan-Menü schließt nur das Menü
- Knöpfe sind während ihrer Aktion gesperrt (kein doppeltes Auslösen per Doppelklick); „Fehlgeschlagene/Alle erneut“ mit Rückfrage
- Pakete-Seite verliert beim Auffrischen nicht mehr Filter und Auswahl
- „Vorschau“/„Jetzt ausführen“ im Zeitplan-Dialog heißen jetzt „Speichern & …“ (sie speichern vorher), die Liste wird danach aktualisiert
- Deinstallation von mehr als 60 Einträgen wird ehrlich abgelehnt statt still gekürzt; Löschen/Aktualisieren meldet Teilerfolge richtig
- „1 Eintrag“ statt „1 Einträge“ u. ä.; einheitliche Zahlenformate; CSV-Dateiname mit lokalem Datum
- Eingabe beim Anlegen eines Feldes bleibt bei einem Fehler erhalten
## pocketknife-2.6.2 (20.10.2026)
- **Dynamische Sammlung aus Größenangaben**: Rechtsklick auf eine als GB/MB angezeigte Spalte eines eigenen Scanners bietet jetzt „Size ≥ 27,8 GB“ / „Size ≤ 27,9 GB“ (und kombiniert mit dem Eintrag) an – statt „= 29846718224“
- Zahlenvergleiche in Sammlungen verstehen Einheiten: `500 MB`, `20 GB`, `1,5 TB` (1 GB = 1024³ Bytes); bei Spalten eigener Scanner zusätzlich „≥“ und „≤“
## pocketknife-2.6.1 (19.10.2026)
- Kerberos-Test: unter dem Ergebnis stand fälschlich „null“
- Repository-Schritt: Hinweis auf Kurznamen nur noch, wenn die Anmeldung tatsächlich scheitert (Kurznamen werden für Kerberos über die DNS-Suchdomäne aufgelöst und funktionieren meist)
## pocketknife-2.6 (19.10.2026)
- **Kerberos statt NTLM** für WinRM und SMB (Clients und Repository) – NTLM ist von Microsoft abgekündigt
- Image enthält jetzt MIT-Kerberos und `gssapi` (`pyspnego[kerberos]`, mehrstufiger Docker-Build); der erste Build dauert etwas länger
- **Einstellungen → Verbindung & Konten → Anmeldeverfahren**: *NTLM (wie bisher)*, *Negotiate* (Kerberos, sonst NTLM) oder *Nur Kerberos* – sofort wirksam, ohne Neustart; Vorgabe bleibt `PK_WINRM_AUTH` aus der `.env` (Standard `ntlm`, also keine Änderung beim Update)
- **Kerberos-Test** Schritt für Schritt: Bibliotheken, Realm/krb5.conf, Domänencontroller (Port 88), Ticket für das Dienstkonto, Diensttickets `HTTP/` und `cifs/` für den gewählten Computer, WinRM- und SMB-Anmeldung mit erzwungenem Verfahren, Repository-Freigabe. Der Client meldet zurück, ob wirklich Kerberos verwendet wurde, dazu die Zeitabweichung. Verständliche Hinweise bei typischen Fehlern (KDC nicht gefunden, Uhrzeit, SPN, Kennwort, AES)
- `krb5.conf` wird aus `PK_DOMAIN` erzeugt (KDC per DNS-SRV); neue `.env`-Variablen `PK_KDC` und `PK_KERBEROS_REALM`; eigene `/data/krb5.conf` hat Vorrang
- Bei Kerberos werden Kurznamen automatisch zum vollständigen DNS-Namen ergänzt und das Dienstkonto als `benutzer@REALM` verwendet
- Falsches Kennwort bei Kerberos löst den Dienstkonto-Schutz aus (keine Kontosperre durch Wiederholungen)
- SMB verwendet jetzt ausdrücklich das gewählte Verfahren (bei NTLM wie bisher NTLM)
- Hilfe/Handbuch/README: Abschnitt „Kerberos statt NTLM“ mit Voraussetzungen, Umstellung Schritt für Schritt und Fehlertabelle
## pocketknife-2.5.16 (19.10.2026)
- **Eigene Scanner beim Computer: Auswahlliste** – bei mehreren Scannern einer Kategorie (z. B. PowerShell) zeigt die Ansicht einen Scanner auf einmal; Auswahl per Dropdown (mit Anzahl der Einträge) oder ‹ › zum Blättern, „Alle Scanner“ zeigt wie bisher alle untereinander
- Die Auswahl wird je Kategorie im Browser gemerkt
## pocketknife-2.5.15 (19.10.2026)
- **Scanner „Dateien & Ordner“ versteht Umgebungsvariablen**: `%SYSTEMDRIVE%`, `%ProgramData%`, `%ProgramFiles%`, `%windir%` … sowie `$env:SystemDrive` werden auf dem Client aufgelöst (vorher wurde der Pfad wörtlich gesucht und nichts gefunden)
- **`**` = beliebig viele Ordnerebenen**, z. B. `%ProgramData%\Firma\**\*.log` (vorher wirkte `**` wie `*`, also nur eine Ebene)
- Hinweis im Scanner-Dialog und in Hilfe/Handbuch: `%USERPROFILE%`/`%APPDATA%` gehören beim Scan dem Dienstkonto bzw. SYSTEM; für alle Benutzer `%SYSTEMDRIVE%\Users\*\…`; `*` ist bei bekannter Tiefe deutlich schneller als `**`
## pocketknife-2.5.14 (19.10.2026)
- **Eigene Scanner: Größen lesbar anzeigen** – Werte in Bytes erscheinen als KB/MB/GB/TB (rechtsbündig, Tooltip mit genauem Bytewert); sortiert wird weiterhin nach dem Zahlenwert
- Neue Einstellung je Scanner **„Größenangaben“**: *automatisch erkennen* (Standard – Spaltennamen wie Size, Length, FreeSpace, Capacity, Größe mit reinen Zahlen; `SizeGB`, `Count`, `Percent` u. ä. bleiben unverändert), *nur diese Spalten* (kommagetrennte Liste) oder *nie umrechnen*
- Gilt für die Ansicht beim Computer und für „Testen“ im Scanner-Dialog – auch die Spalte `size` des Scanners „Dateien & Ordner“ wird jetzt lesbar angezeigt
- Gespeichert, exportiert (CSV) und in dynamischen Sammlungen gefiltert wird weiterhin der Bytewert
- Hilfe/Handbuch/README: Abschnitt „Größenangaben“ mit Beispiel-Skript für Benutzerprofil-Größen
## pocketknife-2.5.13 (19.10.2026)
- **Scanprofile: Reiter „Ziele“** (vorher „Sammlungen“) – neben „Alle Computer“ und Sammlungen jetzt auch **einzelne Computer** wählbar (Suche, „nur online“, Auswahl als Chips wie bei Zeitplänen). Ziel-Computer = Mitglieder der Sammlungen plus einzeln gewählte.
- **Neue Scanprofile zielen standardmäßig auf „Alle Computer“** – wie die festen Profile. Bestehende Profile ohne Ziel bleiben unverändert („nur manuell“).
- Liste der Scanprofile: Spalte „Ziele“ zeigt Sammlungen und die Anzahl einzelner Computer
- Hilfe/Handbuch/README ergänzt
## pocketknife-2.5.12 (19.10.2026)
- Hilfe/Handbuch/README: **Tabelle der Tool-Variablen** (`$(Computer)`, `$(Host)`, `$(IP)`, `$(MAC)`, `$(User)`, `$(Domain)`) mit Bedeutung, Herkunft und Beispiel
- Neuer Hinweis bei den Paket-Schritttypen: In Paketen gelten die Windows-Umgebungsvariablen (`%COMPUTERNAME%`, `$env:COMPUTERNAME` …); `%USERNAME%` ist das ausführende Konto, nicht der angemeldete Benutzer
## pocketknife-2.5.11 (19.10.2026)
- **Heartbeat-Auslöser versorgt jetzt auch Ziele, die schon eingeschaltet sind**: Nach jedem Heartbeat (automatisch oder von Hand) bekommt jedes erreichbare Ziel, das noch nicht versorgt wurde, seine Bereitstellung – z. B. direkt nach dem Anlegen eines Zeitplans oder wenn ein Computer neu in eine dynamische Sammlung fällt. Vorher reagierte der Auslöser nur auf den Moment des Einschaltens.
- Vorgemerkte Ziele (offline bei einem früheren Lauf), die inzwischen erreichbar sind, werden beim Heartbeat ebenfalls versorgt – auch ohne Heartbeat-Auslöser und ohne Zeitfenster
- Bereits versuchte Ziele (erfolgreich oder fehlgeschlagen) wiederholt der Heartbeat nicht laufend; dafür gibt es weiterhin Wartezeit nach Fehlschlag, „Erneut versorgen nach“, weitere Auslöser und „Auswahl erneut versorgen“
- Manueller Heartbeat: „gerade erreichbar geworden“ und „noch nicht versorgt“ laufen nacheinander, damit keine doppelten Bereitstellungen entstehen
## pocketknife-2.5.10 (19.10.2026)
- **Behoben: Heartbeat-Auslöser liefen nach dem Einschalten (z. B. Wake-on-LAN) oft nicht an.** Windows öffnet beim Hochfahren SMB meist vor WinRM; der Heartbeat hielt den Computer dann schon für „online“ und hat den Moment, in dem WinRM erreichbar wurde, nicht mehr als „online gekommen“ gewertet. Maßgeblich ist jetzt der Wechsel von „WinRM nicht erreichbar“ zu „erreichbar“ – für Zeitpläne und Scanprofile.
- Derselbe Wechsel wird auch erkannt, wenn ihn ein Scan oder ein manueller Heartbeat feststellt (vorher nur der automatische Heartbeat)
- Zeitplan-Dialog und Hilfe erklären, wann der Heartbeat-Auslöser greift und was ohne ihn passiert
## pocketknife-2.5.9 (19.10.2026)
- **Automatische Sicherung** (Einstellungen → Sicherung): täglich zur eingestellten Stunde (Standard 02:00) und **vor jedem Update** (beim ersten Start einer neuen Version, noch vor den Datenbank-Änderungen)
- **Versionen aufheben** einstellbar (Standard 10) – ältere Sicherungen werden automatisch gelöscht
- Je Sicherung ein ZIP mit Datenbank (konsistente Online-Kopie), `pocketknife.key` und HTTPS-Zertifikat; Liste mit Herunterladen und Löschen, „Jetzt sichern“; Prüfung auf freien Speicher, Hinweis wenn die Sicherungen auf demselben Datenträger wie die Datenbank liegen
- Neue `.env`-Variable **`PK_BACKUP_PATH`** (Standard `<PK_DATA_PATH>-backup`), in der `docker-compose.yml` als `/backup` eingebunden
- Hilfe/Handbuch/README: Sichern und Wiederherstellen
## pocketknife-2.5.8 (19.10.2026)
- **Tägliche Datenbankwartung** (Standard 03:00 Uhr, einstellbar): Aufräumen, Statistiken aktualisieren (`PRAGMA optimize`), WAL-Datei zurückschreiben und bei mehr als 20 % freiem Platz `VACUUM` – die Datei wird wieder kleiner. Prüft vorher, ob genug Speicher frei ist; Ergebnis im Protokoll.
- **Einstellungen → Wartung**: Datenbankgröße, freier Anteil, WAL-Größe, letzte Wartung und „Jetzt aufräumen“
- Aufbewahrung der **Softwareänderungen** einstellbar (Standard 365 Tage); das stündliche Aufräumen entfernt zusätzlich Reste ohne Bezug (Bereitstellungsziele, Zielverläufe, Inventardaten gelöschter Computer)
- Hilfe/Handbuch: Abschnitt „Aufräumen der Datenbank“
## pocketknife-2.5.7 (19.10.2026)
- **Systemanforderungen und Größenplanung** in Hilfe, Handbuch (DE/EN) und README: Docker-Host, Clients, Richtwerte für CPU, RAM, Speicher, Parallelität und Heartbeat nach Umgebungsgröße (bis 250 bis über 5 000 Computer), Ressourcenbedarf von Datenbank, winget-Index, Scans, Heartbeat, Bereitstellungen und Fernsitzungen sowie Tipps für große Umgebungen
## pocketknife-2.5.6 (19.10.2026)
- **Sicherheitsabfrage vor jedem Entfernen**: zusätzlich zu den bestehenden Löschabfragen (Sammlungen, Computer, Pakete, Zeitpläne, Scanprofile, Scanner, Tools, Felder, Mandanten, Bereitstellungen) fragt POCKET KNIFE jetzt auch beim Entfernen in Editoren nach:
  - Bedingungen und Gruppen dynamischer Sammlungen
  - Auslöser von Zeitplänen und Scanprofilen
  - Schritte und Bedingungen (Datei/Registry) in Paketen, Registry-Einträge
  - Pakete in einem Zeitplan, AD-Ein-/Ausschlussregeln
  - Computer aus einer statischen Sammlung entfernen, Zielverlauf eines Zeitplans zurücksetzen
## pocketknife-2.5.5 (19.10.2026)
- **Zentrales VNC-Kennwort** unter Einstellungen → Fernsitzungen (standardmäßig leer): wird verschlüsselt gespeichert (Schlüssel `/data/pocketknife.key`), nie angezeigt und lässt sich ändern oder entfernen; Änderungen werden protokolliert
- Im VNC-Dialog gibt es dann zusätzlich „Zentrales VNC-Kennwort“ – vorausgewählt, solange eines hinterlegt ist; „Ohne Kennwort / Nachfrage vom Server“ und „Mit Kennwort“ bleiben wählbar
## pocketknife-2.5.4 (19.10.2026)
- **Behoben: „Interner Fehler … IndexError“ beim Öffnen eines Computers**, an dem niemand angemeldet ist – die Live-Abfrage des angemeldeten Benutzers zeigt dann wie vorgesehen „niemand angemeldet“
- Dieselbe Absicherung für leere Antworten an weiteren Stellen (Scan-Ergebnis, Scanner-Test, Bedingungsprüfung, Dienst-/Prozessaktionen, BitLocker)
## pocketknife-2.5.3 (19.10.2026)
- **Bereitstellungen: neue Spalte „Ziel“** – zeigt die gewählte Sammlung bzw. die Ziele des Zeitplans (z. B. „Sammlung: Ohne TightVNC“) und die ersten Computernamen mit „+N“ für weitere; vollständiger Text als Tooltip, filter- und sortierbar. In der Detailansicht steht das Ziel zusätzlich oben.
## pocketknife-2.5.2 (19.10.2026)
- **Behoben: Zeitplan speichern schlug mit „FOREIGN KEY constraint failed“ fehl**, wenn die Datenbank Hilfstabellen aus einer Vorabversion enthielt (Bezug auf die nicht vorhandene Tabelle `deploy_schedules`, fehlende Spalten im Zielverlauf)
- **Automatische Datenbank-Reparatur beim Start**: Tabellen, deren Fremdschlüssel auf falsche Tabellen zeigen oder denen Spalten fehlen, werden im richtigen Aufbau neu angelegt; vorhandene Daten bleiben erhalten, Zeilen ohne gültigen Bezug entfallen. Jede Reparatur steht im Protokoll (System).
- Zeitpläne: ein fehlgeschlagenes Speichern hinterlässt keinen halb angelegten Zeitplan mehr; Auslöser ohne Ausgangspunkt erhalten beim Start „jetzt“, damit vergangene Termine nicht sofort ausgeführt werden
## pocketknife-2.5.1 (19.10.2026)
- **Aussagekräftige Fehlermeldungen statt „Fehler 500“**: Unerwartete Fehler zeigen jetzt Art und Text des Fehlers in der Meldung an; der vollständige Ablauf (Traceback) steht im Protokoll (Kategorie System) und in der Container-Ausgabe (`docker logs pocketknife`)
- Zeitpläne: ungültige Einträge bei Paketen, Computern oder Sammlungen werden als verständliche Meldung abgelehnt statt mit einem internen Fehler
## pocketknife-2.5 (19.10.2026)
- **Zeitpläne und Regeln zusammengeführt**: Alles, was automatisch verteilt oder aktualisiert wird, liegt jetzt unter „Zeitpläne“. Beim Anlegen wählt man die Art „Pakete verteilen“ oder „winget-Updates“ (alle veralteten oder bestimmte winget-IDs, Ausnahmen, Bereich). Der Menüpunkt „Regeln“ entfällt.
- **Bestehende Regeln werden beim Update automatisch übernommen**: winget-Regeln → Zeitplan „winget-Updates“, Regeln „Paket bereitstellen“ → Zeitplan „Pakete verteilen“ mit „erneut versorgen nach“; Intervall, Sammlung (ohne Sammlung: „Alle Computer“), Wochentage und Zeitfenster bleiben erhalten. Die Übernahme wird protokolliert.
- **Heartbeat für beide Arten**: Auslöser „Heartbeat“ versorgt bzw. aktualisiert Computer, sobald sie online kommen; offline Ziele werden vorgemerkt – bei winget-Updates jetzt auch Computer, die zum Zeitpunkt des Laufs aus waren.
- **Zeitfenster für alle Zeitpläne**: Wochentage und Uhrzeit (auch über Mitternacht). Fällige Läufe außerhalb des Fensters werden zu Beginn des Fensters nachgeholt, beim Heartbeat online kommende Computer vorgemerkt; „Jetzt ausführen“ ignoriert das Fenster. Die Liste zeigt, ob das Fenster gerade offen ist.
- Neue Optionen: **Wartezeit nach Fehlschlag** (Stunden, je Zeitplan statt einer globalen Einstellung) und bei Paketen **erneut versorgen nach X Stunden** (für sich selbst pflegende Umgebungen mit dynamischen Sammlungen)
- **Vorschau** je Zeitplan (bei winget: welche Programme auf welchen Computern, inkl. offline), Spalten „Art“, „Was“ und „Zeitfenster“ in der Liste
## pocketknife-2.4.5 (19.10.2026)
- **Fehlgeschlagene Schritte einzeln wiederholen**: Die Bereitstellung zeigt den Bereich „Fehlgeschlagene Schritte“ mit den betroffenen Computern; „Nur diesen Schritt“ führt ausschließlich den Schritt erneut aus, „Ab diesem Schritt“ diesen und alle folgenden Schritte samt Abschluss (bereits erfolgreiche Schritte davor laufen nicht noch einmal)
- Dasselbe für einen einzelnen Computer in „Details“ über „Wiederholen ▾“ beim fehlgeschlagenen Schritt
- Die Wiederholung ist eine eigene Bereitstellung („… – ab „Schritt“ wiederholt“) mit dem Paket-Stand der ursprünglichen Bereitstellung; Bedingungen, „Ausführen als“, Neustart und Scan bleiben gleich
- Schrittergebnisse merken sich jetzt die Schritt-ID (ältere Bereitstellungen werden über die Reihenfolge zugeordnet)
## pocketknife-2.4.4 (19.10.2026)
- **Registry in Paketen**: neuer Schritt „Registry“ – Werte setzen (REG_SZ, REG_EXPAND_SZ, REG_DWORD, REG_QWORD, REG_MULTI_SZ, REG_BINARY), Werte oder Schlüssel löschen, Schlüssel anlegen; mehrere Einträge je Schritt, 64- oder 32-Bit-Ansicht (WOW6432Node), fehlende Schlüssel werden angelegt, jeder Eintrag wird im Protokoll mit OK/FEHLER gemeldet
- **„Aus .reg importieren“**: Inhalt einer exportierten .reg-Datei (UTF-16 oder UTF-8, REGEDIT4/5) einfügen oder Datei wählen – Schlüssel, alle Werttypen sowie Löschungen ([-Schlüssel], "Wert"=-) werden als Einträge übernommen
- **.reg-Dateien als Installationsdatei**: Dateityp „Registry-Datei“ im Schritt „Installieren“ – ausgeführt mit dem 64-Bit-`reg.exe import` (wie `regedit /s`, meldet Fehler aber über den Exitcode); Vorschau mit gleichwertigen `reg add`/`reg delete`-Zeilen und Hinweis zu HKCU und „Ausführen als“
- Fernsitzung VNC: „Ohne Kennwort / Nachfrage vom Server“ steht jetzt oben und ist vorausgewählt
## pocketknife-2.4.3 (19.10.2026)
- **Eigene Zertifikate ohne Reverse Proxy**: neuer Bereich Einstellungen → Zertifikat (HTTPS) mit Anzeige des aktuellen Zertifikats (Namen, Aussteller, Gültigkeit, Schlüssel, Fingerabdruck, Kette, Hostname-Prüfung) und Download
- Hochladen als PFX/PKCS#12 mit Kennwort oder als PEM/CER/DER/P7B mit (auch verschlüsselter) Schlüsseldatei und optionalen Zwischenzertifikaten; „Prüfen“ zeigt vorab Ergebnis und Hinweise
- **Zertifikatsanforderung (CSR)** direkt in POCKET KNIFE (RSA 3072/2048 oder ECDSA P-256, SAN mit DNS-Namen und IPs) – der Schlüssel verlässt den Server nicht; das ausgestellte Zertifikat wird ohne Schlüsseldatei hochgeladen
- Prüfungen vor dem Aktivieren: Schlüssel passt, gültig, Serverauthentifizierung, mind. RSA 2048, ladbar für TLS; Hinweise bei fehlendem Hostnamen, fehlender Kette oder Ablauf in < 30 Tagen (zusätzlich Warnung im Protokoll beim Start)
- **Wechsel ohne Neustart**: das neue Zertifikat gilt sofort für neue Verbindungen (auch beim Wechsel zwischen RSA und ECDSA); TLS ab Version 1.2
- Sicherung der letzten fünf Zertifikate mit Wiederherstellung, „Neues selbstsigniertes Zertifikat“ (z. B. nach Änderung von PK_PUBLIC_HOSTNAME); alle Aktionen werden protokolliert
## pocketknife-2.4.2 (19.10.2026)
- **Dynamische Sammlung per Rechtsklick aus den Inventardaten**: in der Computeransicht (Anwendungen, Drucker, Dienste, Hotfixes, Netzwerkkarten, Laufwerke, Benutzer, Gruppen, Features, Aufgaben, eigene Scanner usw.), in der Softwareübersicht, in der Computerliste (angeklickte Spalte, z. B. Betriebssystem oder Modell) und bei Werten auf der Seite „Allgemein“ → „Dynamische Sammlung erstellen“
- Vorschläge je nach Eintrag: Computer mit diesem Eintrag, mit der angeklickten Spalte (z. B. Druckertreiber), beides zusammen, ohne diesen Eintrag; bei Programmen zusätzlich „jede Version“ (Name ohne Versionsnummer als Muster), eine bestimmte Version und „älter als“ diese Version; bei Mehrfachauswahl „mindestens einer“, „alle“ oder „keiner“
- Der Sammlungsdialog öffnet sich mit fertigem Filter und Namensvorschlag und zeigt sofort die Trefferzahl; Inventartabellen der Computeransicht erlauben dafür Mehrfachauswahl
## pocketknife-2.4.1 (19.10.2026)
- **Angemeldeter Benutzer wird zuverlässig angezeigt**: Bei Remotedesktop-Sitzungen (und teils auch lokal) liefert Windows den Benutzer nicht über Win32_ComputerSystem – POCKET KNIFE ermittelt ihn jetzt zusätzlich über die Explorer-Prozesse (auch mehrere Benutzer)
- Beim Öffnen eines Online-Computers wird der angemeldete Benutzer live abgefragt und gespeichert (vorher nur Stand des letzten Scans, der z. B. direkt nach dem Start vor der Anmeldung lief); Anzeigename aus dem AD wird mit aktualisiert

## pocketknife-2.4 (19.10.2026)
- **Englische Oberfläche**: Umschalter DE / EN unten links, auf der Anmeldeseite und unter Einstellungen → Wartung; alle Seiten, Dialoge, Menüs und Meldungen – auch vom Server – werden übersetzt (Wörterbuch mit rund 2 000 Texten)
- **Hilfe im Programm**: neue Seite „Hilfe“ mit Inhaltsverzeichnis, Volltextsuche, Beispielen und Bildern; **?** oben rechts auf jeder Seite sowie in Paket-, Zeitplan-, Sammlungs-, Scanprofil- und Scanner-Dialogen springt zum passenden Kapitel
- **Handbuch als PDF** in Deutsch und Englisch (rund 30 Seiten mit Bildern) – in der Hilfe herunterladbar, alles lokal
- Werkzeuge für die Pflege: `tools/i18n_extract.py`, `tools/build_i18n.py`, `tools/build_manual.py`

## pocketknife-2.3.4 (19.10.2026)
- **Einstellungen aufgeräumt**: Bereichsliste links (Verbindung & Konten, Active Directory, Zeitpläne & Leistung, Repository & winget, Fernsitzungen, Inventar & Felder, Mandanten, Wartung), Inhalt rechts in voller Breite; der gewählte Bereich bleibt beim Neuladen erhalten
- Formulare laufen nicht mehr über den Kartenrand; Abgleich-Intervall in einer Zeile, Status des letzten AD-Abgleichs umbricht sauber; Wartung und winget als übersichtliche Formulare; Speichern-Knopf direkt beim jeweiligen Bereich

## pocketknife-2.3.3 (19.10.2026)
- Texte in Oberfläche, README und Changelog überarbeitet (Bezüge auf andere Produkte entfernt)

## pocketknife-2.3.2 (19.10.2026)
- **Deinstallationspaket aus gefundener Software**: Rechtsklick auf ein Programm (beim Computer unter „Anwendungen“ oder in der Softwareübersicht) → „Deinstallationspaket erstellen …“, außerdem Knopf „Deinstallationspaket …“. Das Paket lässt sich wie jedes andere auf Computer, Sammlungen oder per Zeitplan verteilen
- **Neuer Schritt „Deinstallieren“**: sucht beim Ausführen live in „Programme und Features“ nach dem Anzeigenamen (Platzhalter *, optional Version) und ermittelt je Computer den stillen Befehl (MSI-Produktcode, Silent-Befehl des Herstellers, bekannte Uninstaller wie Inno/NSIS/Mozilla-helper.exe, winget); gespeicherte Deinstallations-Vorlagen haben Vorrang, ein eigener Befehl mit Platzhaltern ist möglich. Benutzer-Installationen werden als der jeweilige Benutzer entfernt; optional verwaiste Registry-Einträge bereinigen; „nicht installiert“ gilt als übersprungen
- Rechtsklick-Menü in der Anwendungsliste eines Computers (Aktualisieren, Deinstallieren, Deinstallationspaket, Paket aus winget, in der Softwareübersicht zeigen)

## pocketknife-2.3.1 (19.10.2026)
- **winget wird vor jedem winget-Schritt geprüft und bei Bedarf nachinstalliert** (z. B. Windows Server 2019/2022/2025, ältere Windows-10-Builds): App-Installer samt Abhängigkeiten (VCLibs, UI.Xaml) und Lizenz als bereitgestelltes Paket – aus einem Repository-Ordner (offline) oder direkt vom GitHub-Release von microsoft/winget-cli. Einstellungen → winget → „Fehlendes winget“
- Betriebssysteme ohne winget-Unterstützung (vor Windows 10 1809 / Server 2019, z. B. Server 2016): winget-Schritte werden **übersprungen** statt fehlzuschlagen; Auto-Update-Regeln und „Aktualisieren“ lassen diese Computer aus
- Neues Tool „winget prüfen / nachinstallieren“; Spalte „winget“ in der Computerliste
- **Softwareübersicht mit Mehrfachauswahl**: „Auswahl aktualisieren“ (Knopf und Rechtsklick) aktualisiert alle veralteten Installationen der gewählten Programme per winget; im Programm-Detail „Auswahl aktualisieren“ für die markierten Computer
- **Nach jeder Installation oder Deinstallation** läuft standardmäßig der Scan „Anwendungen“ – auch wenn sie fehlgeschlagen ist; im Paket unter „Danach scannen“ ein anderes Scanprofil oder „Nicht scannen“ wählbar

## pocketknife-2.3 (19.10.2026)
- **Pakete** aus Schritten mit den Abschnitten Vorbereitung, Schritte und Abschluss (läuft immer)
  - Schritttypen: Installieren (winget oder Datei aus dem Repository), Befehl, PowerShell, verschachteltes Paket, Dateikopie, Scannen, Neustart (wartet, bis der Computer zurück ist), Warten, Nachricht, Abmelden
  - Ziehen & Ablegen, ↑/↓, Duplizieren, Deaktivieren, Rechtsklick „Verschieben nach“
  - **Bedingungen** für Paket und Schritt: Betriebssystem, Architektur – standardmäßig ist alles vorausgewählt –, PowerShell-Version, Anmeldestatus, Datei, Registry, Sammlung
  - **Ausführen als**: Standard ist das **Dienstkonto** (Deploy-Benutzer), optional Lokales System oder angemeldeter Benutzer – je Paket und Schritt; Kennwort wird nicht im Skripttext übertragen
  - Fehlerverhalten je Schritt (Paket anhalten oder weitermachen), Zeitlimit und Erfolgs-Exitcodes je Schritt
  - **„Wird ausgeführt“** zeigt den tatsächlichen Aufruf auf dem Client (vollständiger Pfad, Parameter, Protokoll, Konto)
  - **MSI-Autoerkennung**: beim Auswählen einer MSI werden Name, Version, Hersteller, Produktcode und Plattform gelesen und Erkennung sowie Architektur-Bedingung vorbelegt
  - „winget-Paket“ und „Eigenes Paket“ zu **einem „Neues Paket“** zusammengeführt
  - Bereitstellungen zeigen das Ergebnis jedes Schritts; neuer Status „Übersprungen“ (Bedingung nicht erfüllt)
  - Bestehende Pakete werden unverändert als Paket mit einem Installationsschritt übernommen und laufen weiter als Lokales System
- **Zeitpläne**: Auslöser (einmalig, Intervall, täglich, wöchentlich, monatlich, Heartbeat), Ziele (Sammlungen und Computer), mehrere Pakete in Reihenfolge, Optionen (bei Fehler abbrechen, Erfolgreiche nicht erneut, offline Computer nachholen, max. Fehlversuche), Zielverlauf
- **Mandanten** mit eigenen Paketen und Zeitplänen, optional auf eine Sammlung beschränkt und mit eigenem Repository-Ordner
- **Feste Scanprofile**: können nicht gelöscht werden, ihre Scanner sind fest; „Standard“ enthält alle Scanner (ohne Dateien & Ordner, PowerShell, Registry, WMI)
- **PowerShell-Scanner repariert**: Ausgaben mit Write-Host, Hashtables, Format-Table/-List, Texten und Fehlern werden jetzt übernommen (vorher blieb die Kategorie leer); Scanner-Dialog mit **„Testen“** auf einem Computer; Kategorie beim Computer zeigt den Stand und „Jetzt scannen“
- Tools: „RDP-Datei herunterladen“ und „Remoteunterstützung (msra)“ entfernt
- Sicherheit: PowerShell-Zeichenketten maskieren jetzt auch typografische Apostrophe (‘ ’ ‚ ‛), die PowerShell als Anführungszeichen wertet
- Bestehende, angepasste Scanprofile mit dem Namen eines festen Profils bleiben als „… (eigene Anpassung)“ erhalten

## pocketknife-2.2 (19.10.2026)
- **Rechtsklick-Menüs** für Sammlungen, Computer und Pakete (mit Untermenüs für Scanprofile, Tools und Berichte)
- **Sammlungen verschieben, kopieren, ausschneiden, einfügen, duplizieren und umbenennen** – auch ganze Ordner samt Inhalt; per Ziehen & Ablegen (Strg = kopieren) und mit `Strg+X/C/V`, `F2`, `Entf`; Suchfeld im Sammlungsbaum; aufgeklappte Ordner werden gemerkt
- Computer per **Ziehen auf eine statische Sammlung** hinzufügen; „Zur Sammlung ▸“, „Aus Sammlung entfernen“, „Neue statische Sammlung …“ im Rechtsklick
- Dynamische Sammlungen unter einer Sammlung erben deren Mitglieder; „Ablegen in“ im Sammlungsdialog
- **Tabellen:** Spalten verschieben (Spaltenkopf ziehen), ein-/ausblenden und **filtern** nach Werten oder Text; je Ansicht gemerkt; Mehrfachauswahl mit Strg/Umschalt, Doppelklick öffnet, `Strg+A`, `Entf`, `Strg+C` (Namen kopieren); viele zusätzliche Spalten (Betriebszeit, Prozessor, RAM, MAC, OU, Seriennummer, Letzte AD-Anmeldung …); CSV-Export der gefilterten Liste
- **Heartbeat** für ausgewählte Computer und Sammlungen; „Scannen erlaubt“ je Computer (sperrt automatische Scans)
- **Am Arbeitsplatz öffnen:** Remote-Unterstützung anbieten (`msra /offerra`), Ereignisanzeige, Computerverwaltung, Dienste, Explorer `C$`, PowerShell-Sitzung, RDP (`mstsc`) – als kleine `.cmd`-Datei für den Rechner des Bedieners
- **Tool-Läufe laufen auf dem Server weiter**, auch nach Schließen des Browsers oder Abmelden
- **Dienstkonto und Repository-Konto in der Oberfläche** statt in der `.env`: Anmeldung wird vor dem Speichern geprüft (Schutz vor Kontosperre), Kennwort verschlüsselt in der Datenbank (`/data/pocketknife.key` mitsichern!)
- **Active Directory in der Oberfläche:** Container ein-/ausschließen (Auswahl aus dem AD mit Computeranzahl), Abgleich-Intervall in Minuten/Stunden/Tagen, Abgleich abschaltbar, Löschen oder Markieren verschwundener Computer, deaktivierte Computerkonten übernehmen ja/nein, Status des letzten Abgleichs
- **AD-Sammlungen:** OU-Struktur unter dem Domänennamen und Ordner „Gruppen“ mit allen AD-Gruppen, die Computer enthalten – automatisch gepflegt
- **Pakete:** bei winget-Paketen neueste Katalogversion, Anzahl Installationen und veraltete Installationen; ohne feste Version zeigt das Paket automatisch die aktuelle Version an; MSI/MSP/MSU/EXE/PS1 als Typ erkennbar; Duplizieren per Rechtsklick
- Weiterhin keine Ressourcen aus dem Internet (Schriften, Skripte, Bilder alle lokal; strenge CSP)

## pocketknife-2.1 (19.10.2026)
- **Deinstallation überarbeitet**
  - Reihenfolge: eigener gespeicherter Befehl → MSI-Produktcode → QuietUninstallString → bekannte Uninstaller → winget (nur bei sicherer Zuordnung) → UninstallString
  - `…\uninstall\helper.exe` (Firefox, Thunderbird, **Zotero** …) wird immer mit `/S` aufgerufen; neu erkannt: Chromium-Uninstaller, Bootstrapper mit `/uninstall`
  - **Benutzer-Installationen** (HKEY_USERS) laufen im Kontext des Benutzers, dem sie gehören („Ausführen als: angemeldeter Benutzer“); Meldung, wenn er nicht angemeldet ist
  - Option „Registry-Eintrag entfernen, wenn das Programm nicht mehr vorhanden ist“ für verwaiste Einträge
  - **Eigenen Deinstallationsbefehl merken** (Muster wie `Zotero*`, Platzhalter `{UninstallString}`, `{ProductCode}`, `{InstallLocation}`) – gilt danach für alle Computer
- **winget-Zuordnung genauer:** Varianten wie ESR/Beta/LTS und die Sprache im Anzeigenamen werden berücksichtigt („Mozilla Firefox ESR (x64 de)“ → `Mozilla.Firefox.ESR.de` statt `Mozilla.Firefox`). Unsichere Zuordnungen werden nicht automatisch aktualisiert und nicht per winget deinstalliert
- **MSI/MSP/MSU:** `/qn`, `/quiet`, `/norestart` in den eigenen Parametern werden nicht mehr doppelt übergeben; automatisches MSI-Protokoll, bei Fehlern erscheint der relevante Auszug in der Ausgabe; **Befehlsvorschau** im Paketdialog
- Ausführung über geplante Aufgabe wahlweise als SYSTEM oder als angemeldeter Benutzer (Grundlage für Pakete in 2.3)
- **Hardware:** „Generic volume shadow copy“, Legacy-Treiber und Software-Geräte werden ausgeblendet
- **Statische Sammlungen** lassen sich im Sammlungsdialog direkt mit Computern befüllen
- **Standard-Scanprofile** (Active Directory, Anwendungen, Computer-Details, Laufwerke, Bildschirme, Umgebungsvariablen, Dateien & Ordner, Hardware, Hotfixes, Speichermodule, Netzwerkkarten, PowerShell, Drucker, Registry, Dienste, Freigaben, Benutzer & Gruppen, Windows-Features, Geplante Aufgaben, WMI); neuer Scanner „Active Directory“; Profile können „alle eigenen Scanner eines Typs“ enthalten
- Neue Computer aus dem AD-Abgleich werden automatisch mit dem Standardprofil gescannt, sobald sie online sind
- **Auto-Cleanup des Scan-Protokolls** (Einstellungen, Standard 14 Tage, 0 = aus; gescannte Daten bleiben)
- Auslöser-Leiste im Scanprofil (Einmalig, Intervall, Täglich, Wöchentlich, Monatlich, Scan-Alter, Heartbeat – beliebig viele)
- **Tools-Seite** als kompakter Baum; zu große Symbole (Tools, Repository-Browser) behoben
- LDAP-Filter fest auf Windows-Computer

## pocketknife-2.0 (18.10.2026)
- **Remotedesktop (RDP) und VNC im Browser** über Apache Guacamole – eigener Tab, Verbindungsdialog, keine Ressourcen aus dem Internet
  - Anmeldung mit der eigenen POCKET KNIFE-Anmeldung (Kennwort nur verschlüsselt im Arbeitsspeicher, abschaltbar), mit anderen Anmeldedaten oder per Nachfrage (NLA-Dialog im Browser)
  - Strg+Alt+Entf, Windows-Taste, Zwischenablage in beide Richtungen, an Fenster anpassen/Originalgröße, Vollbild mit ausblendender Leiste, dynamische Auflösung, Ton, Konsolensitzung, Tastaturlayout, Farbtiefe, Port; VNC mit „Nur ansehen“
  - Verständliche Fehlermeldungen (Anmeldung abgelehnt, nicht erreichbar, …), „Erneut verbinden“
  - Knopf „Remotedesktop“ in der Computeransicht; Tools ▾ → „Remotedesktop im Browser“, „VNC im Browser“, „RDP-Datei herunterladen“
  - Einstellungen → Fernsitzungen: Status von guacd, eigene Anmeldung ein/aus, Standard-Tastaturlayout, aktive Sitzungen mit „Beenden“; jede Sitzung wird protokolliert
- Neuer Container `pocketknife-guacd` in der docker-compose.yml (wird beim Update automatisch geladen); neue optionale Variablen `PK_GUACD_HOST`, `PK_GUACD_PORT`, `PK_GUACD_VERSION`

## pocketknife-1.9 (18.10.2026)
- **Scanprofile statt „Scanner“**: Profile aus beliebigen Standard- und eigenen Scannern, gezielt auf Sammlungen angewendet
  - Der Inventar-Scan ist in 20 einzeln wählbare Standard-Scanner zerlegt; ein Profil-Scan aktualisiert nur seine Bereiche
  - Auslöser je Profil: Einmalig, Intervall, Täglich, Wöchentlich, Monatlich, Scan-Alter (Tage/Stunden/Minuten, optional nur in einem Zeitfenster) und Heartbeat; „offline Computer nachholen“, Enddatum, einzeln deaktivierbar
  - Profil-Dialog mit Reitern Details, Scanner, Auslöser, Sammlungen, Computer (Stand je Computer, ausgewählte sofort scannen); Duplizieren, Als Standard, Jetzt ausführen
  - Eigene Scanner werden auf der Seite „Scanprofile“ verwaltet und Profilen zugeordnet
  - „Scannen ▾“ mit Profilauswahl; Kategorie „Scans“ zeigt Stand je Profil und den Profilnamen im Verlauf
  - Migration: Standardprofil aus dem bisherigen Scan-Intervall, zusätzlich Profil „Anwendungen“ (wird nach Bereitstellungen verwendet). Die Einstellung „Scan-Intervall“ entfällt
- **Wake-on-LAN über Relay:** Das Magic Packet sendet ein Online-Computer im selben Subnetz wie das Ziel (aus IP/Subnetzmaske), sonst ein beliebiger Online-Client per gerichtetem Broadcast; zusätzlich der Container. Die Ausgabe nennt den Relay

## pocketknife-1.8 (18.10.2026)
- **Tools:** Schaltfläche „Tools ▾“ in der Computerliste (für alle ausgewählten Computer) und in der Computeransicht. Menü mit Suche und Ordnern
  - Eingebaut: Ping & Ports (WinRM, SMB, RDP, RPC), Remotedesktop (.rdp-Datei), Wake-on-LAN, Neustart, Herunterfahren, Abbrechen, gpupdate, Jetzt scannen
  - Eigene Tools: PowerShell, CMD (mehrzeilig) oder URL (z. B. vnc://$(IP)); Ausführung als Dienstkonto (schnell) oder als SYSTEM; optionale Rückfrage; Variablen $(Computer), $(Host), $(IP), $(MAC), $(User), $(Domain)
  - 22 Beispiel-Tools in den Ordnern BITLOCKER, GPO, NETZWERK, DRUCKER, WINDOWS UPDATE, SYSTEM, WEB (frei änder- und löschbar)
  - Ausgabe je Computer, parallel (4 gleichzeitig), mit Exitcode, Dauer und „Ausgabe kopieren“; jede Ausführung wird protokolliert
  - Neue Seite „Tools“ zum Anlegen, Ändern, Kopieren und Löschen
- **Scanner** haben eine eigene Seite in der Navigation (unter Regeln) mit „Online-Computer jetzt scannen“
- „Weitere Aktionen“ in der Computeransicht ist im Tools-Menü aufgegangen
- Docker-Image enthält `ping`

## pocketknife-1.7.1 (18.10.2026)
- **Fehlerbehebung Scan:** „A positional parameter cannot be found that accepts argument 'DisplayName'“ – die neue Hilfsfunktion zum Lesen der Registry hieß `RV`, was in PowerShell ein eingebauter Alias für `Remove-Variable` ist. Funktion umbenannt; alle Funktionsnamen der Skripte gegen die PowerShell-Aliase geprüft
- Der Software-Abschnitt des Scans ist jetzt abgesichert: Ein Fehler dort bricht den Scan nicht mehr ab, sondern erscheint unter „Scans“ als Hinweis

## pocketknife-1.7 (18.10.2026)
- **Software deinstallieren** – einzeln oder mehrere, in der Computeransicht (Anwendungen) und unter Software für ausgewählte Computer. Der stille Befehl wird automatisch ermittelt: MSI-Produktcode (`msiexec /x … /qn`), QuietUninstallString des Herstellers, winget, Inno Setup, NSIS, Mozilla, Office Click-to-Run, sdbinst. Unsichere Fälle werden markiert, der Befehl ist vor dem Ausführen änderbar. Läuft als Bereitstellung (SYSTEM) und wartet bei Uninstallern, die sich selbst neu starten, bis der Registry-Eintrag verschwunden ist
- **Anwendungen mit allen Details:** Installationsdatum und -zeit (Zeitpunkt des Registry-Schlüssels), Deinstallationsbefehl, stille Deinstallation, Registry-Hive, Registry-Pfad, Schlüssel/Produktcode, Installationsort, Größe. Auch als Sammlungsfilter verfügbar
- **Dienste** auf dem Client starten, stoppen, neu starten und den Starttyp ändern
- **Prozesse** auf dem Client beenden (mit Prüfung, dass die PID noch zum selben Prozess gehört)
- „Live vom Computer laden“ für Dienste und Prozesse
- **LAPS-Kennwörter** (Windows LAPS im Klartext und Legacy LAPS) und **BitLocker-Wiederherstellungsschlüssel** aus dem AD anzeigen, sofern das Dienstkonto sie lesen darf. BitLocker-Schlüssel zusätzlich direkt vom Computer lesbar. Verdeckte Anzeige mit „Anzeigen“/„Kopieren“; jeder Abruf wird protokolliert (Kategorie „Kennwörter/Schlüssel“)

## pocketknife-1.6 (18.10.2026)
- **Dynamische Sammlungen:** neuer Filter-Editor mit verschachtelten Gruppen (Alle / Eine / Nicht alle / Keine) und Bedingungen auf alle Scan-Daten: Computer, Anwendungen, Hotfixes, Dienste, Laufwerke (logisch und physisch), CPU, Speichermodule, Bildschirme, Hardware, Netzwerkkarten, Drucker, Prozesse, lokale Benutzer/Gruppen/Administratoren, Freigaben, Windows-Features, geplante Aufgaben, AD-Gruppen, Umgebungsvariablen, Produktschlüssel, Bereitstellungen, Softwareänderungen, benutzerdefinierte Felder und eigene Scanner
- Bedingungen derselben Tabelle in einer „Alle“-Gruppe gelten für denselben Eintrag (Anwendung „Firefox“ UND Version < 130); „Keine“-Gruppe für „hat etwas nicht“
- Neue Vergleiche: Versionsvergleich (kleiner/größer als), Muster mit * und ?, reguläre Ausdrücke, Datum (innerhalb/älter als X Tage, vor/nach), Zahlen
- Wertevorschläge aus den vorhandenen Daten, Live-Vorschau der Treffer, Vorlagen (Software fehlt, Software älter als Version, wenig Speicher, …)
- Standardsammlungen lassen sich per Doppelklick als Kopie anpassen; bestehende Sammlungen werden automatisch übernommen
- **Neuer Regeltyp „Paket bereitstellen“:** verteilt ein Paket an alle Online-Computer einer Sammlung. Zusammen mit einer dynamischen Sammlung pflegt sich die Umgebung selbst. „Auto-Update-Regeln“ heißen jetzt „Regeln“
- **Bereitstellen an einzelne Computer:** Im Dialog „Neue Bereitstellung“ lassen sich Computer direkt suchen und auswählen (Name, IP, Benutzer, Modell; „nur online“)

## pocketknife-1.5 (18.10.2026)
- **Fehlerbehebung:** Die Computerübersicht brach mit „(e.antivirus || []).map is not a function“ ab, wenn ein Computer genau einen Virenscanner (oder ein Firewall-Profil/BitLocker-Volume) hatte – PowerShell liefert dann ein Einzelobjekt statt einer Liste. Wird jetzt beim Speichern und in der Anzeige abgefangen
- Kategorieliste der Computeransicht mit 28 Einträgen: Computer, Active-Directory-Gruppen, Anwendungen, Sammlungen, CPU, Benutzerdefinierte Felder, Bereitstellungen, Laufwerke, Bildschirme, Umgebungsvariablen, Dateien & Ordner, Hardware, Hotfixes, Lokale Gruppen, Lokale Benutzer, Speichermodule, Netzwerkkarten, PowerShell, Drucker (lokal), Prozesse, Produktschlüssel, Registry, Scans, Dienste, Freigaben, Windows-Features, Geplante Aufgaben, WMI
- Entfernt: Grafikkarten, Benutzerprofile und Autostart (Scan, Filter, Berichte) sowie die Einträge Lokale Administratoren, Softwareänderungen, Protokoll und Notizen in der Computeransicht. Lokale Administratoren stehen unter „Lokale Gruppen“; Softwareänderungen und Protokoll weiterhin unter Berichte bzw. Protokoll

## pocketknife-1.4 (18.10.2026)
- **Neue Computeransicht:** Kategorieliste links, Übersicht mit Feldblöcken Allgemein, Betriebssystem, Active Directory, System, Sicherheit und Scan; unterstrichene Felder springen in die passende Kategorie
- **Viele neue Inventardaten:** CPU-Details, Speichermodule (Steckplatz, Typ, Teilenummer), physische Datenträger (SSD/HDD, Zustand), Volumes, Bildschirme (Modell, Seriennummer, Baujahr), Grafikkarten, Geräte-Manager inkl. Problemgeräte, Umgebungsvariablen, lokale Gruppen mit Mitgliedern, lokale Benutzer, Netzwerkkarten (Status, Geschwindigkeit, DHCP-Server), Drucker, Prozesse, Produktschlüssel & Aktivierung, Freigaben, Windows-Features/Serverrollen, geplante Aufgaben, Benutzerprofile, Autostart
- Übersicht zusätzlich: Gehäusetyp, BIOS-Hersteller/Asset-Tag/Familie/SKU/UUID, OS-Seriennummer, Systemlaufwerk, SMBv1 Server/Client, IE-Version, .NET-Versionen, BIOS-Modus, Secure Boot, letztes Windows-Update, Antivirus, Firewall, Domänencontroller, Betriebszeit, Anzeigename des angemeldeten Benutzers
- AD-Abgleich liest zusätzlich die AD-Gruppen und den Standort des Computers
- **Eigene Scanner** (Einstellungen): Registry, Dateien & Ordner, WMI und PowerShell – Ergebnisse unter den gleichnamigen Kategorien
- **Benutzerdefinierte Felder** (z. B. Inventarnummer, Garantie bis) je Computer, auch als Sammlungsfilter
- **Scan-Verlauf** je Computer mit Dauer, Fehler und Hinweisen auf unvollständige Abschnitte
- Neue Sammlungsfilter: AD-Gruppe, Drucker, Prozess, lokaler Benutzer, Freigabe, geplante Aufgabe, Autostart, Gerät, Gerät mit Problem, Bildschirm, Windows-Feature, Benutzerprofil, Gehäusetyp, BIOS-Modus, Secure Boot, SMBv1, TPM-Version, .NET, Standort, Anzahl Speichermodule
- 9 neue Berichte: Systemeigenschaften, Speichermodule, Bildschirme, Drucker, lokale Benutzer, Produktschlüssel & Aktivierung, Geräte mit Problemen, Autostart, Benutzerprofile
- Jeder Abschnitt des Scans ist einzeln abgesichert – ein Fehler (z. B. fehlendes Modul) bricht den Scan nicht mehr ab
- Datenbank wird beim Start automatisch erweitert; bestehende Daten bleiben erhalten. Die neuen Kategorien füllen sich beim nächsten Scan

## pocketknife-1.3 (18.10.2026)
- **Dienstkonto-Schutz:** Scheitert eine Anmeldung des Dienstkontos eindeutig (gesperrt, falsches Passwort, deaktiviert, abgelaufen), pausiert POCKET KNIFE alle automatischen Zugriffe für 15 Minuten, statt weitere Fehlanmeldungen zu erzeugen. Hinweis auf Übersicht und Einstellungen mit „Prüfen & fortsetzen“
- **Fehlerbehebung SMB:** Bei Anmeldefehlern wurde die Anmeldung sofort ein zweites Mal versucht – jeder Fehlversuch zählte doppelt für die Kontosperre. Jetzt nur noch ein Neuaufbau bei echten Verbindungsabbrüchen
- **Anmeldung ohne Dienstkonto:** Benutzer melden sich mit ihren eigenen Daten an und prüfen ihre Gruppenmitgliedschaft selbst. Die Anmeldung funktioniert dadurch auch, wenn das Dienstkonto gesperrt ist
- **Verschachtelte Gruppen:** zuerst serverseitig über LDAP_MATCHING_RULE_IN_CHAIN, sonst rekursiv über memberOf (bis 15 Ebenen, schleifensicher). Neu: primäre Gruppe wird berücksichtigt, Gruppen-DN wird unabhängig von Schreibweise und Leerzeichen erkannt
- Verständliche Meldungen für Windows-/LDAP-Fehlercodes (z. B. 0xC0000234 „Konto gesperrt“, 0xC000015B „Netzwerkanmeldung verweigert“)
- WinRM-Fehler 401: Es wird unterschieden, ob das Dienstkonto selbst abgelehnt wird oder nur auf diesem Computer keine Adminrechte hat

## pocketknife-1.2 (18.10.2026)
- **Fehlerbehebung SMB:** Abgebrochene SMB-Verbindungen (Neustart des Fileservers, Leerlauf-Timeout) werden jetzt automatisch neu aufgebaut. Bisher schlugen Kopien danach bis zum Neustart des Containers fehl
- Neuer Test unter Einstellungen → Repository: DNS, Port 445, Anmeldung (SMB-Version, Signierung, Verschlüsselung), Auflisten sowie Schreiben/Lesen/Prüfen/Löschen einer 4-MB-Testdatei mit Durchsatz
- „Verbindung testen“ beim Computer prüft zusätzlich den SMB-Zugriff auf ADMIN$ des Clients
- Bei Fehlern vor dem Start der Installation werden die kopierten Paketdateien auf dem Client wieder entfernt

## pocketknife-1.1 (18.10.2026)
- DNS-Server, DNS-Suchdomäne, Port, Zeitzone, Datenpfad und Repository-Ordner werden jetzt in der `.env` gesetzt – `docker-compose.yml` muss bei einem Update nicht mehr angepasst werden
- Datenverzeichnis kann außerhalb des Versionsordners liegen (`PK_DATA_PATH`), Compose-Projektname ist fest (`pocketknife`)
- Versionsnummer in der Datei `VERSION`, Anzeige in der Seitenleiste und unter Einstellungen
- `update.sh` übernimmt die `.env` aus dem alten Versionsordner und startet die neue Version

## pocketknife-1.0 (18.10.2026)
- Erste Version: Inventarisierung, Sammlungen, winget- und Repository-Pakete, Bereitstellungen, Auto-Update-Regeln, Berichte, Protokoll
