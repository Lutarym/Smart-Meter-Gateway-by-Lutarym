# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden hier dokumentiert.
Die Versionsnummer muss immer mit `custom_components/lutarym_ppc_smgw/manifest.json`
("version") und `custom_components/lutarym_ppc_smgw/const.py` (`VERSION`)
übereinstimmen.

## 2.4.6

**Import-Statistik: sum-Werte schließen jetzt nahtlos an die Live-Kette an
(kein -Zählerstand-Sprung mehr in change-Karten)**

- Problem: Die Statistik-Karte (`type: statistic`, `stat_type: change`)
  zeigte für den laufenden Monat einen riesigen negativen Wert (z.B.
  -4854 kWh), obwohl der reale Verbrauch nur ~0,5 kWh betrug. Ursache: Der
  Import schrieb `sum` als absoluten Zählerstand, während Home Assistant
  die Live-Statistik eines `total_increasing`-Sensors mit `sum` = 0 ab
  dessen erstem Zustand kompiliert. Beide sum-Ketten hatten damit
  unterschiedliche Nullpunkte; die `change`-Berechnung über die
  Monatsgrenze griff über diesen Bruch und ergab -(Zählerstand).
- Fix: Der Import schreibt `state` weiterhin als absoluten Zählerstand
  (korrekte Anzeige, korrekte Reset-Erkennung), aber `sum` jetzt so, dass
  die LETZTE importierte Zeile bei 0 endet und alle früheren entsprechend
  negativ sind. Dadurch schließt die importierte Reihe nahtlos an die bei
  0 startende Live-Kette an - der Übergang ist sprungfrei. (Dasselbe
  Prinzip nutzen etablierte Statistik-Import-Werkzeuge.)
- Nachgerechnet gegen einen echten Jahres-Export: `change` je Monat ergibt
  jetzt exakt den realen Monatsverbrauch (Aug +0,59; Jul +5,21; Feb
  +1713,63 kWh), die letzte importierte sum ist 0, und der state-Verlauf
  bleibt streng monoton steigend.
- Aufräumung: Die überholte "Brücke bis jetzt" (lineare Interpolation vom
  CSV-Ende zum Live-Wert) wurde entfernt - im Rückwärts-Modus ist der
  Live-Wert bereits der Anker auf der letzten CSV-Zeile, es gibt nichts zu
  überbrücken.

## 2.4.5

**Statistik-Einbruch nach Import verhindert (Ausreißer nahe 0 wird jetzt
auch beim ersten Poll abgefangen)**

- Problem: Nach einem CSV-Import (oder generell nach Löschen+Neu-Hinzufügen
  bzw. einem Neustart) war die bestehende Plausibilitätsprüfung für den
  ERSTEN Live-Poll blind, weil ihr Referenzwert (`_last_good_meter_values`)
  nur im Arbeitsspeicher liegt und dann leer ist. Traf in genau diesem
  Fenster ein einzelner fehlerhafter Messwert nahe 0 ein, wurde er
  ungeprüft übernommen. Da der Zähler-Sensor `total_increasing` ist,
  wertete Home Assistant den Sturz auf 0 als Zähler-Reset und riss die
  Monatssumme ins Negative (z.B. auf -4854 kWh), obwohl die Entity nur
  kurz einbrach und sofort wieder korrekt war.
- Fix 1 - Seeding nach Import: Nach einem erfolgreichen Historien-Import
  wird der importierte Endstand direkt als Plausibilitäts-Referenz im
  Coordinator gesetzt (`seed_last_good_value`). Der unmittelbar folgende
  erste Live-Poll wird dadurch geprüft; ein Ausreißer nahe 0 wird
  verworfen und der letzte gute Wert beibehalten, statt die gerade
  importierte Statistik zu zerstören.
- Fix 2 - Referenz aus letztem Zustand: Liegt (etwa nach einem reinen
  Neustart ohne Import) kein Referenzwert im Speicher vor, wird er aus dem
  von Home Assistant über Neustarts hinweg erhaltenen letzten
  Sensorzustand rekonstruiert (`_restore_last_good_from_state`), bevor der
  erste Poll ausgewertet wird.
- Beide Wege sind allgemein und nicht fallspezifisch: Sie schützen jeden
  Nutzer vor genau diesem Reset-Artefakt, unabhängig von konkreten
  Zählerständen. Der Coordinator erhält dafür jetzt eine Referenz auf den
  Config-Entry (Entity-Auflösung).

## 2.4.4

**CSV-Import: korrekte kW→kWh-Umrechnung, takt-unabhängig, Text korrigiert**

- **Einheiten-Fix (Kern):** Der TraveNetz-Export liefert in der Wertspalte
  die mittlere LEISTUNG des Intervalls in kW, nicht bereits die Energie in
  kWh (Spaltenkopf "Einheit" = "kW"). Bisher wurde der Wert direkt als kWh
  aufsummiert - dadurch waren die importierten Mengen grob falsch. Jetzt
  wird korrekt `kWh = kW × Intervalldauer` gerechnet, wobei die Dauer aus
  den Spalten "von"/"bis" jeder Zeile bestimmt wird.
- **Takt-unabhängig:** Weil die Intervalldauer aus den Daten kommt,
  funktioniert der Import ohne Code-Änderung sowohl für den Tages-Export
  (24 h pro Zeile) als auch für den 15-Minuten-Export (0,25 h pro Zeile) -
  letzteres relevant für Nutzer mit dynamischen Stromtarifen. Auch die
  interne Lückenfüllung leitet ihr Raster jetzt aus dem tatsächlichen
  Zeilenabstand ab (Median), statt fest ein Stundenraster anzunehmen
  (das bei Tagesdaten 23 Fake-Lücken pro Tag erzeugt hätte).
- **Plausibilitätsschutz (allgemein):** Ist im Rückwärts-Modus die
  CSV-Gesamtenergie größer als der als Anker gesetzte aktuelle
  Zählerstand, würde der Reihenbeginn negativ. Dieser Fall (z.B.
  Zählerwechsel im Exportzeitraum) wird jetzt mit einer klaren
  Fehlermeldung abgebrochen, statt unsinnige negative Stände zu schreiben.
- **Text korrigiert:** Die Beschreibung im Import-Dialog sprach
  fälschlich von "stündlich" und einem manuell zu setzenden Startwert.
  Sie weist jetzt korrekt auf Tages-/15-Minuten-Unterstützung und die
  Rückwärts-Verankerung am Live-Zählerstand hin.

## 2.4.3

**Historien-Import: rückwärts vom aktuellen Zählerstand rechnen**

- Der 1:1-CSV-Import (TraveNetz) rechnet die kumulierte Reihe jetzt
  standardmäßig RÜCKWÄRTS: Der aktuelle HAN-Live-Zählerstand (1-0:1.8.0,
  beim Import frisch abgerufen) wird als Anker auf die LETZTE CSV-Zeile
  gelegt und von dort Stunde für Stunde nach hinten abgezogen. Der
  Startwert ergibt sich rechnerisch und muss nicht mehr vorgegeben werden.
- Hintergrund: Bisher wurde vorwärts ab einem manuell gesetzten Startwert
  auf der ERSTEN CSV-Zeile gerechnet. War unklar, welcher realen Zeit die
  erste Zeile entspricht bzw. stimmte der Aufzeichnungsbeginn nicht,
  verschob sich die gesamte Kurve. Der aktuelle Zählerendstand ist die
  verlässliche Größe - daran wird jetzt verankert.
- Die frühere "Brücke bis jetzt" (lineare Interpolation vom CSV-Ende zum
  Live-Wert) entfällt im Rückwärts-Modus, weil der Live-Wert bereits der
  Endpunkt der Reihe ist - es gibt nichts mehr zu überbrücken.
- Fallback: Ist der Live-Wert beim Import nicht abrufbar, greift
  weiterhin der bisherige Vorwärts-Modus mit vorgegebenem Startwert.
- Der resultierende Verlauf bleibt streng monoton steigend
  (total_increasing-konform); die Monatsaufteilung ist unverändert.

## 2.4.2

_(Version 2.4.1 wurde übersprungen - deren Umfang ist vollständig in diesem Release enthalten.)_

**Einstellbares Abrufintervall + Standard-IP + Sichtbarkeit statt Filterung**

- Standard-IP im Einrichtungsassistenten von `192.168.1.200` auf
  `172.20.0.1` geändert - typische Adresse der HAN-Schnittstelle eines
  PPC/TraveNetz-Gateways, sodass das Feld im Normalfall nur bestätigt
  statt geändert werden muss.
- Abrufintervall im Optionen-Dialog frei einstellbar (Minuten, Minimum 5,
  Standard 15) statt fest verdrahteter 900 Sekunden.
- Alle vom Gateway gelieferten Messwerte und Auswertungsprofile werden
  wieder vollständig abgerufen - nichts wird mehr im Coordinator
  weggefiltert. Welche Entitäten in Home Assistant standardmäßig sichtbar
  sind, steuert jetzt ausschließlich `entity_registry_enabled_default`
  in `sensor.py`:
    - Standardmäßig AKTIV: Zähler-Messwerte (u.a. 1-0:1.8.0 Bezug,
      1-0:2.8.0 Einspeisung), Firmware und "Zähleradresse" (Zählernummer).
    - Standardmäßig DEAKTIVIERT, aber vorhanden und jederzeit unter
      Einstellungen -> Geräte & Dienste -> Entitäten aktivierbar: alle
      übrigen Zähler-Metadaten, sämtliche Auswertungsprofile sowie
      Integrations-Version, IP-Adresse, Benutzername, OBIS-Aktiv-Status
      und Zugang-gültig-ab.
- 1-0:2.8.0 (Einspeisung) bleibt dadurch ein vollwertiger Sensor, selbst
  wenn das Gateway aktuell keinen Wert dafür liefert - die Entität bleibt
  bestehen und zeigt einen Wert an, sobald einer kommt.

## 2.4.0

**Kein eigener Statistik-Schreibmechanismus mehr - Home Assistant kompiliert
jetzt vollständig selbst**

- Der seit 1.18.0 aktive Selbst-Schreib-Mechanismus (`sensor.py`:
  `_handle_coordinator_update` / `_async_self_publish_with_gap_fill` /
  `_self_publish_statistic`) ist entfernt. Grund: er kollidierte
  wiederholt mit Home Assistants eigener, paralleler Kompilierung
  derselben Stunde (UNIQUE-Constraint-Fehler auf `(metadata_id,
  start_ts)`), was die komplette Recorder-Batch-Transaktion inkl.
  fremder, unbeteiligter Entities im selben Zyklus blockiert hat.
- Home Assistants eingebaute Langzeit-Statistik-Kompilierung für
  `state_class: total_increasing`-Sensoren erkennt einen fallenden
  Rohwert selbst zuverlässig als Zähler-Reset und führt `sum` trotzdem
  korrekt fort - der Sensor liefert jetzt nur noch seinen rohen
  Zählerstand über `native_value`, alles Weitere macht HA.
- Die Verbindungstoleranz (bis zu 3 aufeinanderfolgende fehlgeschlagene
  Zyklen, siehe `coordinator.py`) und die Plausibilitätsprüfung einzelner
  Messwerte bleiben unverändert bestehen und sind jetzt der einzige
  Schutzmechanismus gegen Anomalien in der Statistik - siehe README,
  Abschnitt "Eingebauter Schutz vor Anomalien" (vormals "Automatische
  Selbstheilung").
- Die automatische Lückenfüllung bei kurzen Ausfällen (vormals bis zu 3
  Tage rückwirkend über `_async_self_publish_with_gap_fill`) entfällt
  damit ebenfalls. Für echte Lücken bleibt der CSV-Import (siehe
  `travenetz_import.py`) der genaue Weg.

**Bugfixes**

- `config_flow.py`: `login()` wurde an drei Stellen (Ersteinrichtung,
  "Neu konfigurieren", Optionen-Dialog) nie mit einem `logout()`
  abgeschlossen - im Gegensatz zu jedem regulären Coordinator-Zyklus
  (`coordinator.py`, try/finally). Da das Gateway nur eine aktive Session
  gleichzeitig zuverlässig verarbeitet, konnte das dazu führen, dass der
  erste Coordinator-Refresh direkt nach Setup/Reconfigure/Options-Speichern
  noch auf eine offene Alt-Session trifft. `_async_close_client()` meldet
  die Session jetzt zuerst sauber ab, bevor der httpx-Client geschlossen
  wird.
- `config_flow.py`: "Neu konfigurieren" erlaubt das Ändern des Hosts,
  hat die `unique_id` (= Host) danach aber nie nachgezogen - sie blieb
  auf dem alten Host stehen, während `entry.data[CONF_HOST]` bereits den
  neuen enthielt. Eine spätere Neueinrichtung für den alten Host wäre
  dadurch fälschlich als "already_configured" blockiert worden. Zieht die
  `unique_id` jetzt bei einer Host-Änderung nach (mit explizitem
  Duplikat-Check gegen andere bestehende Einträge, bricht in dem Fall mit
  "already_configured" ab statt eines der beiden Einträge zu verbiegen).
- `__init__.py`: der Service-Handler für `import_history` hat den
  Anzeigenamen bei fehlendem `friendly_name`-Attribut direkt auf die
  rohe entity_id zurückfallen lassen, statt (wie die drei anderen
  Service-Handler) zuerst den Namen aus der Entity Registry zu
  probieren.
- `config_flow.py`: `ConfigFlowResult` wurde als Rückgabetyp verwendet,
  aber nie importiert (nur durch `from __future__ import annotations`
  zur Laufzeit unschädlich geblieben).
- `hacs.json`: deklariertes Mindest-HA von 2024.1.0 auf 2024.11.0
  angehoben - `_get_reconfigure_entry()`/`async_update_reload_and_abort()`
  (Reconfigure-Flow, seit 2.1.0) gibt es erst ab den im Herbst 2024
  eingeführten Reauth/Reconfigure-Hilfsmethoden.

## 2.1.0

**Neu: "Neu konfigurieren" - Host/Benutzername/Passwort ändern ohne die
Integration zu löschen**

- Bisher gab es keinen Weg, geänderte HAN-Zugangsdaten (z. B. nach einem
  vom Messstellenbetreiber ausgelösten Passwort-Reset) einzutragen, ohne
  die komplette Integration zu entfernen und neu einzurichten - was die
  `unique_id`, alle Entity-IDs, den Verlauf und importierte
  Langzeit-Statistiken (siehe `travenetz_import.py`/`history_import.py`)
  gefährdet hätte.
- Neuer `async_step_reconfigure` im Config Flow: Über das ⋮-Menü der
  Integration → **"Neu konfigurieren"** lassen sich Host, Benutzername
  und Passwort ändern. Die neuen Zugangsdaten werden mit einem
  Live-Login-Versuch getestet (dieselbe Fehlerbehandlung wie beim
  Ersteinrichten: `invalid_auth`, `parsing_error`, `cannot_connect`) und
  erst bei Erfolg über `async_update_reload_and_abort` übernommen -
  Entities, Verlauf und Statistiken bleiben dabei vollständig erhalten,
  da die `unique_id` (Host-IP) unverändert bleibt.
- Formular ist mit den bisherigen Werten vorausgefüllt, damit nicht alles
  neu eingetippt werden muss.
- `strings.json`/`translations/en.json`/`translations/de.json` um den
  neuen `reconfigure`-Schritt sowie die Abort-Meldung
  `reconfigure_successful` ergänzt.

## 1.10.0

Reiner Versionssprung auf 1.10.0 (von 1.0.7), keine Codeänderung.

## 1.0.7

**Veraltetes Versions-Beispiel in README korrigiert**

- In der "Update veröffentlichen"-Anleitung stand noch ein altes
  Beispiel (`v0.16.3`) statt eines zur aktuellen Version passenden
  (`v1.0.6`/`v1.0.7`) - korrigiert.
- Zur Klarstellung: Zwei Stellen im Code (`__init__.py`, `api.py`)
  erwähnen bewusst "Version 0.13.0" - das sind KEINE veralteten
  Versionsangaben, sondern historische Dokumentation, WANN der Wechsel
  von aiohttp auf httpx eingeführt wurde. Diese bleiben unverändert.

## 1.0.6

**`brand/`-Ordner (Integrations-Icon) jetzt fester Bestandteil des Projekts**

- `brand/icon.png` (256×256) und `brand/icon@2x.png` (512×512) ergänzt -
  vorher nur direkt auf GitHub hochgeladen, jetzt auch hier im
  Projektstand enthalten, damit zukünftige ZIPs sie automatisch
  mitliefern.
- README um kurzen Hinweis dazu ergänzt.

## 1.0.5

**Reiner Versionssprung, keine Codeänderung**

- Version von 1.0.1 direkt auf 1.0.5 angehoben (auf Wunsch, ohne
  Zwischenversionen 1.0.2-1.0.4). Funktional identisch zu 1.0.1.

## 1.0.1

**Lizenz geändert: MIT → restriktive "Nutzung erlaubt, Kopieren nicht"-Lizenz**

- Bisher MIT-Lizenz (erlaubt uneingeschränktes Kopieren/Verändern/
  Weiterverbreiten). Auf Wunsch geändert zu einer eigenen, restriktiven
  Lizenz: **Installation und Betrieb** (auch über HACS) bleiben
  ausdrücklich erlaubt, **Kopieren, Verändern oder Weiterverbreiten des
  Quellcodes ist NICHT erlaubt** ohne vorherige schriftliche Zustimmung.
- Wichtiger Hinweis: Da das Repository öffentlich auf GitHub liegt, kann
  der Quellcode technisch weiterhin von jedem eingesehen werden - die
  Lizenz macht eine Weiterverwendung entgegen dieser Bedingungen aber zu
  einer Lizenzverletzung, gegen die vorgegangen werden kann (z. B.
  DMCA-Takedown, Unterlassungsforderung).
- README-Lizenzabschnitt entsprechend angepasst.

## 1.0.0

**Erstes stabiles Release**

- README um Hinweis ergänzt: Bei **TraveNetz AG** werden die
  iMSys-Gateways unter der festen IP `172.20.0.1` angesprochen
  (abweichend vom Gateway-Standardwert `192.168.1.200`).
- Nach der ausführlichen Fehlersuche und den Umbauten der letzten
  Versionen (0.10.0 bis 0.16.4) ist die Integration jetzt funktional
  stabil: korrekte Zählerstand-Auslesung über "Zählerprofil" (httpx,
  Digest-Auth pro Request), Auswertungsprofil-Unterstützung mit
  automatischer Aktuell-vs-Abgelaufen-Erkennung, vollständige Geräte-
  Diagnose-Informationen und ausführliche Dokumentation.
- Keine funktionalen Änderungen gegenüber 0.16.4 - reiner
  Versionssprung auf 1.0.0 zur Kennzeichnung der Stabilität.

## 0.16.4

**Dokumentation überarbeitet, keine Funktionsänderung**

- `README.md` komplett neu strukturiert: Inhaltsverzeichnis, vollständige
  Liste der angelegten Entitäten, eigener Troubleshooting-Abschnitt
  (insbesondere: veraltete HAN-Zugangsdaten als häufigste Ursache für
  "eingefrorene" Werte, Session-Limit des Gateways, "Gateway neu
  starten"-Button), Architektur-Überblick für Mitwirkende, und eine
  Schritt-für-Schritt-Anleitung, wie ein Update für HACS korrekt
  veröffentlicht wird (inkl. des `hacs.json`-Namensfeld-Stolpersteins).
- Alle Python-Dateien durchgegangen und Docstrings/Kommentare ergänzt, wo
  sie fehlten oder nach den letzten Umbauten (httpx-Wechsel,
  Geräte-Info-Refactoring) nicht mehr ganz aktuell waren -
  `PPCSmgwClient`-Klassendocstring mit Methodenübersicht,
  `_raw_request()`-Docstring, `async_setup_entry()`-Docstrings in
  `sensor.py`/`button.py`, Kommentare im Update-Zyklus in
  `coordinator.py`, `_async_close_client()`/`async_step_init()`-Docstrings
  in `config_flow.py`.
- Keine Verhaltensänderung.

## 0.16.3

**Kleinere Textänderung: "Integrations-Version:" zu "Version:" gekürzt**

- In der Geräte-Karte steht neben dem Modellnamen jetzt nur noch
  "Version: X.X.X" statt "Integrations-Version: X.X.X".

## 0.16.2

**Integrationsversion jetzt auch in der Geräte-Übersichtskarte, korrekt
über `model_id` statt im irreführenden "Hardware"-Feld**

- Home Assistant erlaubt in der Geräte-Karte nur drei fest beschriftete
  Zusatzfelder: "Firmware", "Hardware", "Seriennummer" - eine eigene
  Beschriftung "Software" gibt es dort nicht (im Frontend-Quellcode
  bestätigt). "Hardware" für eine Integrationsversion zu nutzen wäre
  inhaltlich falsch.
- Stattdessen wird jetzt `model_id` genutzt ("Integrations-Version:
  X.X.X" als zusätzliche Zeile unter dem Modellnamen) - anders als
  `name` wirkt sich das NICHT auf die Namen der einzelnen Entitäten aus.
- `hw_version` wird nicht mehr gesetzt.
- Die zusätzliche Diagnose-Entität "Integrations-Version" (0.15.0) bleibt
  unverändert bestehen.

## 0.16.1

**Fix: HACS zeigte trotz 0.16.0 weiterhin den alten Namen**

- Ursache gefunden (über direkte Analyse von `.storage/hacs.repositories`
  in Home Assistant): HACS liest den in seiner Repository-Übersicht
  angezeigten Namen aus `hacs.json` **im Hauptverzeichnis** des Repos -
  eine komplett andere Datei als `custom_components/lutarym_ppc_smgw/
  manifest.json`, die in 0.16.0 korrekt angepasst wurde. `hacs.json`
  wurde dabei übersehen und enthielt weiterhin den alten Namen.
- Fix: `hacs.json` auf denselben Namen wie `manifest.json` gebracht:
  "PPC Smart Meter Gateway (iMSys) by Lutarym".
- Kein HACS-Cache-Problem, kein GitHub-Problem - einfach eine
  übersehene zweite Datei mit eigenem Namensfeld.

## 0.16.0

**Anzeigename geändert: Nickname jetzt als Attribution am Ende statt am Anfang**

- Bisher: "LUTARYM PPC Smart Meter Gateway (iMSys)" / Gerätename
  "LUTARYM PPC SMGW (...)". Jetzt: **"PPC Smart Meter Gateway (iMSys) by
  Lutarym"** (Integrationsname in HACS/Integrationsliste), Gerätename
  vereinfacht zu **"PPC SMGW (...)"** (ohne Wiederholung des Nicknamens
  bei jeder einzelnen Entität).
- Betrifft: `manifest.json` (Anzeigename), Gerätename, Config-Entry-Titel,
  Einrichtungsdialog-Überschrift (`strings.json`/Übersetzungen), README.
- **Bewusst NICHT geändert:** die Domain (`lutarym_ppc_smgw`) und der
  Ordnername - eine Änderung daran würde alle bestehenden Entitäten
  zerstören und eine komplette Neueinrichtung erzwingen. Rein kosmetische
  Namensänderung, keine Neueinrichtung nötig.
- Home Assistant sucht daher weiterhin auch treffsicher, wenn man in
  "Integration hinzufügen" nach "PPC Smart Meter Gateway" oder "iMSys"
  sucht (README entsprechend angepasst).

## 0.15.1

**Neue Diagnose-Entität: "Benutzername"**

- Zeigt den konfigurierten HAN-Benutzernamen als eigene Diagnose-Entität
  am Gerät (Kategorie "Diagnose", neben Firmware, Integrations-Version,
  IP-Adresse etc.). Das Passwort wird NICHT angezeigt und bleibt
  ausschließlich in der Config-Entry-Konfiguration.

## 0.15.0

**Geräte-Infos als eigene, klar beschriftete Diagnose-Sensoren statt in
die "Hardware"-Zeile gequetscht**

- Die in 0.14.0/0.14.1 ergänzten Zusatzinfos (Integrationsversion,
  IP-Adresse, 1.8.0/2.8.0-Status, Gültig-ab-Datum) wurden bisher alle in
  ein einziges `hw_version`-Textfeld gepackt - das landete in Home
  Assistant unter der Beschriftung **"Hardware"**, was inhaltlich nicht
  passt (Integrationsversion ist keine Hardware), und alles stand in
  einer Zeile nebeneinander statt übersichtlich untereinander.
- Fix: Fünf neue, einzelne Diagnose-Sensoren am Gerät - **"Firmware"**
  (unverändert), **"Integrations-Version"**, **"IP-Adresse"**,
  **"1.8.0 Aktiv"**, **"2.8.0 Aktiv"**, **"Zugang gültig ab"**. Jede hat
  ihren eigenen, korrekten Namen und erscheint wie gewohnt als eigene
  Zeile in der Entitätenliste (Kategorie "Diagnose") - untereinander,
  nicht mehr zusammengequetscht.
- `hw_version` wird nicht mehr gesetzt. `sw_version` (Firmware) und
  `serial_number` (Zählernummer) bleiben wie in 0.14.0.

## 0.14.1

**Geräte-Info um IP-Adresse ergänzt**

- Die Geräte-Info-Karte zeigt jetzt zusätzlich die konfigurierte
  IP-Adresse des Gateways (in `hw_version`, zusammen mit den übrigen in
  0.14.0 ergänzten Infos).

## 0.14.0

**Die eigentliche Ursache für alle "eingefrorenen Werte" seit 0.10.0 ist
gefunden: veraltete HAN-Zugangsdaten - kein Software-Fehler**

- Nach ausführlichem Vergleich der tatsächlich gesendeten Digest-Auth-
  Header zwischen unserem Code und einer Referenz-Implementierung zeigte
  sich: beide fragten zwar dasselbe Gateway ab, aber mit **zwei
  unterschiedlichen HAN-Benutzernamen**. Der bei der Ersteinrichtung
  verwendete Zugang war ein bei TraveNetz zwischenzeitlich abgelöster,
  alter Zugang - der Login damit gelang zwar weiterhin fehlerfrei, lieferte
  aber ausschließlich einen eingefrorenen, signierten Ablesewert vom
  1.1.2026 (vermutlich der Stand zum Zeitpunkt eines
  Registrierungswechsels). Der komplette dreistufige "Zählerstand"-Umbau
  (0.10.0), der Fallback-auf-Zählerprofil-Umbau (0.10.3), der
  aiohttp-zu-httpx-Umbau (0.13.0) und alle Diagnose-Bemühungen dazwischen
  waren technisch zwar teils sinnvolle Verbesserungen, haben aber nicht
  die eigentliche Ursache behoben - die lag ausschließlich in den
  hinterlegten Zugangsdaten.
- **Kein Code-Fix nötig** - einfach die Integration mit den aktuell
  gültigen HAN-Zugangsdaten (siehe TraveNetz-Kundenportal) neu einrichten.
- Debug-Logs (`RAW METERVALUE HTML`, `GESENDETE REQUEST-HEADER`) auf
  DEBUG-Level zurückgestuft, da nicht mehr für laufenden Betrieb benötigt.

**Neue Funktionen:**

- Standard-Abfrageintervall von 300 auf **900 Sekunden** geändert -
  entspricht dem tatsächlichen Ausleseintervall des Gateways (15 Min);
  häufigeres Abfragen brachte ohnehin keine zusätzliche Aktualität,
  sondern nur unnötige zusätzliche Sessions am Gateway.
- Geräte-Info-Karte erweitert: zusätzlich zu PPC-Firmware jetzt auch
  Integrationsversion, ob 1.8.0/2.8.0 aktuell aktiv sind, ab wann der
  Zugang gültig ist (Beginn Validierungsperiode) und die Zählernummer
  (als Seriennummer) - alles in Großbuchstaben. `build_device_info()`
  zentral in `coordinator.py`, statt in `sensor.py`/`button.py`
  dupliziert.

## 0.13.2

**Temporär: Request-Header-Diagnose-Log**

- httpx-Umbau (0.13.0/0.13.1) hat das Kernproblem NICHT gelöst - unser
  Code liefert weiterhin denselben eingefrorenen, byte-identischen
  Zählerstand wie vor dem Umbau, obwohl HTTP-Client, Auth-Muster und
  Cookie-Handling jetzt strukturell der Referenz-Implementierung
  entsprechen. Nächster Diagnoseschritt: tatsächlich gesendete
  Request-Header direkt vergleichen (nie zuvor gemacht - bisher wurden
  nur Antworten verglichen).
- Neues Log `GESENDETE REQUEST-HEADER (unser Code): ...` auf
  WARNING-Level bei jedem Zählerstand-Abruf.

## 0.13.1

**Kritischer Fix: Einrichtungsassistent blieb bei "Zähler auswählen" hängen**

- Regression durch den httpx-Umbau in 0.13.0: Jeder Einrichtungsschritt
  (Zugangsdaten → Zähler → Auswertungsprofile) hat einen komplett neuen,
  leeren `httpx`-Client erzeugt. Anders als beim vorherigen
  `aiohttp`-Ansatz (Cookie+Token als einfache Strings zwischen
  unabhängigen Kurz-Sessions weitergereicht) steckt bei `httpx` die
  Session (Cookie-Jar + Digest-Auth-Zustand) im Client-Objekt selbst - ein
  neuer Client pro Schritt hatte dadurch nie eine gültige Session, der
  Zähler-Abruf schlug still fehl, und der Assistent zeigte dauerhaft
  "keine Zähler gefunden".
- Fix: Der `httpx`-Client wird jetzt EINMAL beim Zugangsdaten-Schritt
  erzeugt und über alle weiteren Einrichtungsschritte hinweg
  wiederverwendet (als Instanzvariable auf dem Config-Flow gehalten),
  statt pro Schritt neu erzeugt zu werden. Wird erst nach dem letzten
  Schritt (oder bei einem Fehler) sauber geschlossen.
- Betrifft nur die Ersteinrichtung (Options-Flow zum nachträglichen Ändern
  war bereits korrekt, da dort ein Client für den ganzen Schritt
  wiederverwendet wurde).

## 0.13.0

**Großer Umbau: HTTP-Client von `aiohttp` auf `httpx` umgestellt**

- Hintergrund: Nach umfangreichen, zeitgleichen Vergleichstests (curl
  UND unser aiohttp-Code gegen dieselbe Zählerprofil-Seite, im
  Sekundenabstand) hat sich ein reproduzierbarer Unterschied gezeigt: Ein
  strukturell ansonsten identischer `httpx`-basierter Referenz-Client
  (Digest-Auth auf JEDEM einzelnen Request statt nur einmalig beim Login,
  automatischer Cookie-Jar statt manuell verwaltetem Cookie-Header) bekam
  zuverlässig frische, aktuelle Zählerstände, während unser bisheriger
  `aiohttp`-Client (Digest-Auth nur beim Login, danach rein
  Cookie-basierte Folge-Requests) wiederholt einen alten/eingefrorenen
  Wert bekam - bei ansonsten identischer mid, mit sauber isolierter
  Session, ohne Netzwerk-/Host-Unterschied. Die genaue Ursache auf
  Gateway-Seite bleibt unklar, das Verhalten war aber mehrfach
  reproduzierbar.
- Fix: `api.py` nutzt jetzt `httpx.AsyncClient` statt `aiohttp.ClientSession`.
  Pro Login-Zyklus wird ein frisches `httpx.DigestAuth`-Objekt erzeugt und
  für ALLE Folge-Requests desselben Zyklus wiederverwendet (Login, Zähler-
  /Auswertungsprofil-Abfragen, Logout) - jeder einzelne Request durchläuft
  damit den vollen Digest-Auth-Handshake, nicht nur der allererste. Die
  Cookie-Weitergabe läuft jetzt über httpx' eingebauten automatischen
  Cookie-Jar statt über einen manuell gebauten `Cookie`-Header.
- `__init__.py`, `config_flow.py`, `coordinator.py` und `button.py`
  entsprechend angepasst (Client-Methoden geben nur noch `token` zurück/
  entgegen, kein separates `cookie` mehr - die Session-Cookie-Verwaltung
  übernimmt httpx intern).
- Eigener Digest-Auth-Header-Bau (`_build_digest_header`, `_md5`,
  `_parse_digest_challenge`) sowie die manuelle Cookie-Extraktion
  (`_extract_session_cookie`) wurden entfernt - das übernimmt jetzt
  vollständig `httpx.DigestAuth` bzw. der eingebaute Cookie-Jar.
- Debug-Log `RAW METERVALUE HTML (unser Code, httpx)` bleibt vorerst
  aktiv (WARNING-Level), um den nächsten Vergleich mit der
  Referenz-Integration zweifelsfrei zu bestätigen. Wird danach wieder auf
  DEBUG-Level zurückgestuft.

## 0.12.1

**Temporär: RAW-Debug-Log für direkten Vergleich mit Referenz-Integration**

- Loggt bei jedem Zyklus die rohe `metervalue`-Tabelle auf WARNING-Level
  (`RAW METERVALUE HTML (unser Code): ...`) - analog zum Debug-Log, das in
  der Referenz-Integration ("PPC SMGW") manuell ergänzt wurde. Zweck:
  direkter, zeitgleicher Vergleich beider Integrationen im HA-Log, um zu
  klären, ob unser `aiohttp`-Code tatsächlich andere Daten vom Gateway
  bekommt als die `httpx`-basierte Referenz. Wird nach Abschluss der
  Fehlersuche wieder entfernt.

## 0.12.0

**Neu: "Signiert"-Diagnose-Entität je Zähler/OBIS-Code**

- Bei der Fehlersuche rund um "eingefrorene" Zählerstände hat sich
  gezeigt, dass die vom Gateway gelieferten Werte eine kryptografische
  Signatur (`table_metervalues_col_sign`) enthalten können - ein Hinweis
  darauf, dass es sich um einen offiziell signierten/eichrechtskonformen
  Ablesewert handelt (z.B. Jahresabschluss, Lieferantenwechsel), NICHT um
  einen fortlaufend aktualisierten Live-Wert. Solche Werte ändern sich nur
  zu bestimmten Anlässen, nicht kontinuierlich - das ist normales
  Gateway-Verhalten, kein Fehler.
- Neue Diagnose-Entität **"... Signiert (eichrechtskonformer
  Ablesewert)"** je Zähler/OBIS-Code: zeigt "Ja", wenn der aktuell
  angezeigte Wert eine solche Signatur trägt. Damit direkt in Home
  Assistant sichtbar (kein Terminal/curl nötig), ohne auf den nächsten
  signierten Ablesewert warten zu müssen, um das einzuordnen.
- Technisch: `_extract_meter_readings()` in `api.py` liest zusätzlich
  `table_metervalues_col_sign` aus; neues Feld in `METER_EXTRA_FIELDS`
  (`sensor.py`).

## 0.11.1

**Robustheits-Fix: Zeitstempel-Vererbung für Zeilen ohne eigenen
Zeitstempel (z.B. 2.8.0 Einspeisung)**

- Manche Firmware-Stände tragen den Zeitstempel nur in der ersten
  Werte-Zeile ein (z.B. bei 1.8.0) und lassen ihn in nachfolgenden Zeilen
  derselben Ablesung (z.B. 2.8.0) leer. `_extract_meter_readings()`
  übernimmt jetzt in diesem Fall den Zeitstempel der vorherigen Zeile,
  statt ihn leer zu lassen.
- Reine Robustheits-/Vollständigkeits-Korrektur für die Anzeige - ändert
  NICHT den ausgelesenen Wert selbst.

## 0.11.0

**Neu: "Gateway neu starten"-Button - behebt eingefrorene/veraltete Werte**

- Ursache für die in 0.10.x untersuchten "eingefrorenen" Werte (Zählerstand
  UND Auswertungsprofile blieben auf demselben Zeitstempel stehen)
  gefunden: Das SMGW selbst bleibt gelegentlich mit einem hängenden
  HAN-Register stecken - "Letzte Synchronisation" schreitet dann nicht
  mehr fort, unabhängig davon, welcher Client/welches Skript abfragt. Der
  Zähler selbst und die Backend-Anbindung beim Netzbetreiber sind davon
  NICHT betroffen (dort laufen die Daten normal weiter) - nur die lokale
  HAN-Schnittstelle des Gateways hängt fest.
- Ein Neustart/Selbsttest des Gateways (`action=selftest`) behebt das
  zuverlässig, ganz ohne Netzbetreiber-Kontakt.
- Neue Entität: **"Gateway neu starten"** (Button, Geräteklasse "Restart")
  am Gerät. Bei eingefrorenen Sensorwerten einmal drücken und einen
  Update-Zyklus abwarten.
- Kein Logout nach dem Auslösen - das Gateway startet neu, eine
  eventuell offene Session wird dadurch ohnehin hinfällig.
- Technisch: neue `PPCSmgwClient.selftest()`-Methode in `api.py`, neue
  `button.py`-Plattform, `Platform.BUTTON` in `__init__.py` ergänzt.

## 0.10.3

**Wichtige Korrektur: Dreistufiger "Zählerstand"-Verlauf (seit 0.10.0)
entfernt - "Zählerprofil" ist jetzt der alleinige, primäre Weg**

- Hintergrund: Beim Abgleich mit einer unabhängigen Referenz-Implementierung
  (unterstützt PPC/EMH/Theben-Gateways) hat sich gezeigt, dass diese
  ausschließlich `action=showMeterProfile` nutzt und daraus zuverlässig
  frische, zeitgestempelte Werte für ALLE Register (z.B. 1.8.0 Bezug UND
  2.8.0 Einspeisung) aus einer "metervalue"-Tabelle liest - mit exakt
  denselben Spalten-IDs, die unser eigener Code bereits kennt. Der in
  0.10.0 eingeführte dreistufige Weg über "Zählerstand"
  (`showMeterValuesForm` -> `showMeterValues`) beruhte auf der falschen
  Annahme, dass NUR dieser Weg echte/aktuelle Werte liefert und
  "Zählerprofil" primär veraltete Metadaten zeigt. In echten Logs lieferte
  dieser dreistufige Weg wiederholt "Keine Zählerdaten vorhanden" über den
  kompletten 6-Stunden-Abfragezeitraum, obwohl die mid-Extraktion korrekt
  ablief - vermutlich, weil diese Firmware/dieses Gateway-Modell
  "Zählerstand" schlicht nicht zuverlässig über HAN befüllt.
- Fix: `get_meter_readings()` nutzt jetzt nur noch `showMeterProfile` (wie
  die Referenz-Implementierung). Der komplette dreistufige
  "Zählerstand"-Code (`_get_meter_readings_via_history`,
  `_extract_metervalues_form_mid`) wurde entfernt.
- Zusätzlich: Messwerte UND Zähler-Metadaten wurden bisher über ZWEI
  separate, identische `showMeterProfile`-Aufrufe geholt. Das ist jetzt
  EIN einziger Request - reduziert die Zeit pro Zähler in der (laut
  Gateway nur einfach erlaubten) Session.
- **Falls du eine Weile schon veraltete Zählerstände gesehen hast, sollte
  dieses Update das beheben.**

## 0.10.2

**Log-Rauschen reduziert bei leerer "Zählerstand"-Antwort + Diagnose-Hinweis**

- Beobachtung aus echten Logs: Der dreistufige "Zählerstand"-Formular-Fluss
  (seit 0.10.0) läuft korrekt ab (frische `mid` wird korrekt extrahiert und
  verwendet), das Gateway liefert aber teils dennoch "Keine Zählerdaten
  vorhanden" für den kompletten 6-Stunden-Abfragezeitraum. Der Footer-
  Zeitstempel "Letzte Synchronisation" blieb dabei über mehrere Abrufe
  hinweg eingefroren, ohne dass eine zweite gleichzeitige Verbindung zum
  Gateway bestand - deutet auf eine ausstehende Synchronisation des
  Gateways mit dem Zähler hin, nicht auf einen Fehler in dieser
  Integration. Der bestehende Fallback auf "Zählerprofil" greift in diesem
  Fall bereits korrekt (zeigt einen ggf. älteren Wert statt "nicht
  verfügbar").
- Bisher wurde bei jedem so einem Vorkommnis der komplette HTML-Body
  (mehrere KB) auf WARNING-Level geloggt. Das wird jetzt auf eine kurze,
  informative Zeile reduziert (inkl. "Letzte Synchronisation"-Zeitstempel
  aus dem Gateway-Footer, sofern vorhanden). Der volle HTML-Body wird nur
  noch auf DEBUG-Level geloggt.
- Kein Verhaltensunterschied bei der eigentlichen Datenverarbeitung -
  reine Diagnose-/Logging-Verbesserung.

## 0.10.1

**Wichtiger Fix: Auswertungsprofile mit identischem Label lieferten
zufällig veraltete Werte**

- Ursache: Wenn das SMGW zwei Auswertungsprofile mit demselben Anzeigenamen
  liefert (z.B. ein abgelaufenes/historisches Profil eines früheren
  Lieferantenwechsels UND das aktuell aktive Profil, beide "Bezug
  15-Minuten"), passen beide auf denselben Label-Filter und wurden beide
  abgerufen. Da sie unter demselben Schlüssel (`tarif:<Label>`) im
  Daten-Dict abgelegt wurden, gewann schlicht das zuletzt verarbeitete -
  und das Gateway liefert dabei KEINE zuverlässige Reihenfolge
  (aktuell/historisch), wodurch teils dauerhaft die alten, eingefrorenen
  Werte angezeigt wurden statt der aktuellen.
- Fix: Neue Hilfsfunktion `_is_more_current()` in `coordinator.py`
  entscheidet bei einer Label-Kollision aktiv, welches Profil behalten
  wird: (1) ein nicht-abgelaufenes Profil gewinnt immer gegen ein
  abgelaufenes, (2) bei Gleichstand gewinnt der spätere Beginn der
  Validierungsperiode.
- Betrifft nur Setups mit mehreren Auswertungsprofilen gleichen Namens
  (typischerweise nach einem Lieferanten-/Messstellenbetreiberwechsel).
  Kein Einfluss auf Zähler-Messwerte (Zählerstände) - diese sind über
  eindeutige OBIS-Codes bereits kollisionsfrei.

## 0.10.0

**Wichtiger Fix: "Zählerstand"-Formular-Fluss war zweistufig statt

dreistufig implementiert**

- Ursache gefunden in den Logs: Der echte Formular-Fluss für "Zählerstand"
  ist DREISTUFIG: (1) Zählerliste (meterform) liefert eine `mid`, (2) das
  "Zählerstand"-Formular öffnen (`showMeterValuesForm`) liefert eine
  EIGENE, NEUE `mid` speziell für diesen Schritt, (3) erst MIT dieser
  frischen `mid` liefert die eigentliche Abfrage (`showMeterValues`)
  Ergebnisse. Wir haben bisher Schritt 2 übersprungen und direkt die `mid`
  aus Schritt 1 für Schritt 3 verwendet - das Gateway antwortete daraufhin
  mit "Keine Zählerdaten vorhanden" und wir fielen automatisch auf die
  (eingefrorene) `showMeterProfile`-Antwort zurück.
- Fix: `_get_meter_readings_via_history()` ruft jetzt zuerst
  `showMeterValuesForm` auf, extrahiert die dort neu ausgestellte `mid` aus
  dem `input_metervalues`-Formular, und verwendet ERST diese für die
  eigentliche `showMeterValues`-Abfrage. Extraktion gegen echte
  Gateway-Antwort verifiziert.

## 0.9.2

- Diagnose: "Kommunikationsprofil" (`action=commprofile`) - der einzige
  Hauptmenüpunkt des Gateways, den wir bisher nie abgerufen hatten - wird
  jetzt testweise abgerufen und geloggt. Prüft die Vermutung, dass dort
  weitere, providerbezogene Profile/Register sichtbar sein könnten, die in
  Zähler- oder Auswertungsprofil-Liste nicht auftauchen.

## 0.9.1

**Möglicher Fix für falsche/veraltete Zählerwerte - dedizierte Session ohne Cookie-Jar**

- Ursache gefunden durch Vergleich mit dem tatsächlichen Quellcode von
  jannickfahlbusch/ha-ppc-smgw (`gateways/ppc/ppcsmgw/ppc_smgw.py`, per ZIP
  bereitgestellt): Deren Client musste explizit das automatische
  Cookie-Handling seines HTTP-Clients (httpx) umgehen
  (`SessionCookieStillPresentError`, mit dem Kommentar "prevents
  subsequent readings"). Wir haben bisher die von Home Assistant geteilte
  aiohttp-Session verwendet, die ebenfalls einen automatischen Cookie-Jar
  hat - dieser konnte mit dem von uns manuell gesetzten Session-Cookie-
  Header kollidieren und zu veralteten Werten führen.
- Fix: Eigene, dedizierte `aiohttp.ClientSession` mit
  `cookie_jar=aiohttp.DummyCookieJar()` (deaktiviertes automatisches
  Cookie-Handling) statt der geteilten HA-Session - sowohl für den
  laufenden Betrieb (Coordinator) als auch für den Einrichtungsdialog.

## 0.9.0

**Auswertungsprofile beim Einrichten auswählbar**

- Neuer Config-Flow-Schritt nach der Zählerauswahl: Alle über die
  HAN-Zugangsdaten sichtbaren Auswertungsprofile werden angezeigt, der
  Nutzer wählt aus, welche als Entitäten angelegt werden sollen (z.B. um
  bekannt abgelaufene/historische Profile früherer Lieferantenwechsel
  abzuwählen).
- Auswahl nachträglich änderbar über die Options des Config-Entries
  (Zahnrad-Symbol bei der Integration).
- Wichtiger Hinweis: Diese Auswahl filtert nur, WELCHE der bereits über
  HAN sichtbaren Profile angezeigt werden - sie kann keine Profile
  "finden", die nicht in der Gateway-eigenen Liste auftauchen (das wäre
  eine Aufgabe des Messstellenbetreibers/GWA).

## 0.8.2

**Fix: Alle Auswertungsprofil-Basisprofil-Felder waren leer**

- Ursache: Die Auswertungsprofil-Basisprofil-Tabelle nutzt
  `<td width=20%>Label</td>` statt schlichtem `<td>Label</td>` - die
  Extraktions-Regex hat exaktes `<td>` ohne Attribute erwartet und ist
  daher bei JEDEM Feld dieser Tabelle leer ausgegangen (Profil-ID,
  Profilname, TAF-Typ, Register-/Abrechnungsperiode, Vorhaltezeit, Beginn/
  Ende Gültigkeit, Alias, Zählpunkt, Tarifstufen, Tag Beginn - betraf ALLE
  diese Felder, nicht nur das Datum). Regex akzeptiert jetzt beliebige
  Attribute in beiden `<td>`-Tags. Gegen echte Daten verifiziert.

## 0.8.1

**Kurze, abgeleitete Entitätsnamen statt voller technischer Bezeichner**

- Zähler-Entitäten: Name basiert jetzt auf dem OBIS-Kurzcode statt dem
  vollen technischen Zählernamen, z.B. "1.8.0 Zähler-ID" statt
  "01005e318002.1lgz0081554715.sm Zähler-ID".
- Auswertungsprofil-Entitäten: Name wird aus erkennbaren Bestandteilen des
  Profilnamens abgeleitet, z.B. "Bezug 15-Min Abgelaufen" statt
  "IM4G_TAF07_BEZUG_15MI_PROD_01 Abgelaufen". Unbekannte Namensmuster
  fallen auf eine gekürzte Rohform zurück.
- Feldbezeichnungen leicht gekürzt (z.B. "Ende Gültigkeit" statt "Ende
  Validierungsperiode"), Begriffe bleiben nah am PPC-Handbuch.

## 0.8.0

**Alle Parameter jetzt als eigene Entitäten (nicht mehr nur Attribute)**

- Jedes bisher nur als Attribut versteckte Feld ist jetzt eine eigene
  Sensor-Entität: Zähler-ID, Zähler-Name, Beschreibung, Kommunikationstyp,
  Protokoll-Typ, Protokoll-Version, Ausleseintervall, Abfrageversuche,
  Zähleradresse, Medium, Wert-Zeitstempel, Wert-Gültigkeit (je Zähler) und
  Profil-ID, TAF-Typ, OBIS, Messgröße, Register-/Abrechnungsperiode,
  Vorhaltezeit, Beginn/Ende Validierungsperiode, Abgelaufen-Status, Alias,
  Zählpunktbezeichnung, Tarifstufen, Tag Beginn (je Auswertungsprofil).
- Neu: Eigene Firmware-Versions-Entität für das Gateway.
- Alle diese Zusatz-Entitäten sind als "Diagnose" kategorisiert und
  erscheinen dadurch übersichtlich gruppiert auf der Geräteseite, statt
  die Haupt-Entitätenliste zu überladen.
- Erwartete Anzahl Entitäten bei 1 Zähler + 2 Auswertungsprofilen: ca. 44
  (vorher: 3).

## 0.7.0

**Ursache für "falschen" Wert endgültig gefunden + alle Parameter ergänzt**

- Root Cause: Die Auswertungsprofile auf dem Gateway waren zum Zeitpunkt
  des Tests seit dem 1.1.2026 abgelaufen ("Ende Validierungsperiode") -
  passend zum Enddatum des vorherigen Stromanbieters. Das erklärt den
  eingefrorenen Zählerwert (Zeitstempel exakt beim Ablaufdatum). Das ist
  eine administrative Konfigurationsfrage beim Messstellenbetreiber, kein
  Software-Bug.
- Fix: `get_tariff_profile_value()` komplett neu - die Auswertungsprofil-
  Seite enthält KEINE Messwert-Tabelle (falsche Annahme in 0.4.0), sondern
  ausschließlich Konfigurationsdaten. Liefert jetzt alle Basisprofil-Felder
  (Profil-ID, TAF-Typ, Register-/Abrechnungsperiode, Vorhaltezeit,
  Validierungszeitraum, Alias, Zählpunktbezeichnung, Tarifstufen) plus
  automatische `abgelaufen`-Erkennung (Ende Validierungsperiode vs. jetzt).
- Zähler-Metadaten erweitert um alle Felder aus "Zählerprofil" (Zähler-ID,
  Name, Beschreibung, Protokoll-Version, Abfrageversuche, Medium) statt
  bisher nur eines Teils.
- Auswertungsprofil-Sensoren zeigen jetzt korrekt "unbekannt" als Zustand
  (da kein Messwert existiert) statt fälschlich einen Fehler zu werfen -
  alle Konfigurationsdetails stehen als Attribute zur Verfügung.

## 0.6.0

**Umstellung auf "Zählerstand" (showMeterValues) als primäre Datenquelle**

- Messwerte werden jetzt primär über die im PPC-Handbuch (Kapitel 4.3,
  Abbildung 14) dokumentierte "Zählerstand"-Historie gelesen
  (`action=showMeterValues`, letzte 6 Stunden, neueste Zeile je OBIS-Code),
  statt wie bisher über "Zählerprofil" (`showMeterProfile`), das primär für
  Metadaten gedacht ist und ggf. veraltete Werte zeigt.
- Automatischer Rückfall auf "Zählerprofil", falls die Zählerstand-Historie
  keine Werte liefert, inkl. vollständigem Diagnose-Log in diesem Fall.
- Zähler-Metadaten (Kommunikationstyp, Protokoll-Typ etc.) werden weiterhin
  separat über "Zählerprofil" gelesen, unabhängig von der Werte-Quelle.

## 0.5.2

- Neu: Gateway-eigene Firmware-Version (z.B. "33918-34868") wird jetzt aus
  jeder Antwort extrahiert und als Geräte-Firmware in HA angezeigt (statt
  bisher fälschlich der Integrationsversion).
- Diagnose: Da der Logout-Fix (0.5.1) den falschen Zählerwert nicht behoben
  hat, wird jetzt zusätzlich die im PPC-Handbuch (Kapitel 4.3) dokumentierte
  "Zählerstand"-Historie (`action=showMeterValues`, mit Datumsbereich)
  testweise abgerufen und geloggt - das ist laut Handbuch der eigentlich
  vorgesehene Weg für echte, signierte Zählerstände, im Unterschied zu
  "Zählerprofil" (showMeterProfile), das primär Metadaten zeigt.

## 0.5.1

**Fix für falsche/eingefrorene Zählerwerte (851 statt ~4850 kWh)**

- Ursache gefunden mit Hilfe eines Vergleichs mit jannickfahlbusch/ha-ppc-smgw:
  Das SMGW erlaubt nur eine aktive Session gleichzeitig und synchronisiert
  seine Register offenbar nicht zuverlässig neu, solange eine alte Session
  nicht sauber per `action=logout` beendet wurde. Diese Integration hat sich
  bisher nie ausgeloggt - dadurch blieb der zurückgegebene Registerwert auf
  einem alten/eingefrorenen Stand hängen (erkennbar auch daran, dass die
  "Letzte Synchronisation"-Zeit des Gateways über mehrere Update-Zyklen
  hinweg nicht fortschritt).
- Fix: Jeder Update-Zyklus meldet sich jetzt am Ende garantiert ab
  (`logout()`, per try/finally auch im Fehlerfall) - wie im bekannt
  funktionierenden Referenzcode.

## 0.5.0

**Der eigentliche Hauptbug für "fehlende Werte" - Danke an den Vergleich mit
jannickfahlbusch/ha-ppc-smgw für den entscheidenden Hinweis!**

- Fix: Die Werte-Tabelle einer Zähler-Antwort (`showMeterProfile`) kann
  MEHRERE Zeilen gleichzeitig enthalten (z.B. eine für Bezug/1.8.0 UND eine
  für Einspeisung/2.8.0) - alle Zeilen verwenden dieselben (technisch nicht
  eindeutigen) Spalten-IDs. Die Extraktion suchte bisher nur global nach dem
  ERSTEN Treffer im gesamten Dokument und ignorierte dadurch jede weitere
  Zeile komplett. Jetzt wird pro Zeile extrahiert (verifiziert gegen echte
  und simulierte Zwei-Zeilen-Antworten).
- Breaking Change: `get_meter_value()` (ein Wert) ersetzt durch
  `get_meter_readings()` (Liste aller Werte). Ein Zähler kann jetzt mehrere
  Sensor-Entitäten erzeugen (Schlüssel-Format
  "<Zähler-Label>::<OBIS-Code>"). **Einmaliges Neu-Einrichten der
  Integration erforderlich** (alte Entitäten hatten andere unique_ids).

## 0.4.1

- Diagnose: "Abgeleitete Register" (`action=showTarificationForm`) - ein
  bisher nicht erkundeter Menüpunkt neben den Auswertungsprofilen - wird
  jetzt testweise abgerufen und geloggt. Möglicherweise zeigt diese Ansicht
  rohe Zähler-Register unabhängig vom konfigurierten Auswertungsprofil,
  was auch 2.8.0 (Einspeisung) enthalten könnte, falls physisch vorhanden.

## 0.4.0

- Erkenntnis: Das Gateway stellt für diesen Anschluss zwei
  "Auswertungsprofile" bereit (Bezug 15-Minuten, Bezug Monat) - aber
  keines für Einspeisung/2.8.0. Das ist eine Konfigurationsfrage beim
  Messstellenbetreiber, keine Einschränkung der Integration.
- Neu: Alle gefundenen Auswertungsprofile werden jetzt automatisch als
  zusätzliche Sensoren angelegt (bisher nur der Zählerstand aus
  "Zählerprofil"). Aktuell noch nicht einzeln abwählbar - werden alle
  automatisch erstellt.
- `tid` von Auswertungsprofilen wird wie die Zähler-`mid` als
  session-gebunden/rotierend behandelt (frisch pro Update-Zyklus
  nachgeschlagen).

## 0.3.1

- Diagnose: Laut PPC-Handbuch bestimmt das "Auswertungsprofil" des
  Gateways, welche OBIS-Werte überhaupt an der HAN-Schnittstelle
  bereitgestellt werden - vermutlich der Grund, warum bisher nur 1.8.0
  (Bezug) sichtbar war, obwohl der Zähler laut "Kommunikationstyp: 2"
  bidirektional ist und auch 2.8.0 (Einspeisung) liefern können müsste.
  Temporärer Diagnose-Abruf des Auswertungsprofils (`action=tariffform`)
  ergänzt, um dessen Struktur zu ermitteln.

## 0.3.0

- Erkenntnis: Dieser Zähler/dieses Gateway stellt über die HAN-Schnittstelle
  tatsächlich nur EINEN Messwert bereit (OBIS 1-0:1.8.0, Gesamtverbrauch) -
  kein Einspeisewert, keine separaten Tarifzeiten. Das ist keine
  Einschränkung der Integration, sondern die Grenze dessen, was das
  HAN-Profil dieses Anschlusses hergibt.
- Neu: Zusätzliche statische Zähler-Metadaten (Kommunikationstyp,
  Protokoll-Typ, Ausleseintervall, Zähleradresse) werden jetzt als
  Sensor-Attribute mit ausgelesen und angezeigt - bisher ungenutzte
  Information aus derselben Antwort.
- Diagnose-Logging aus 0.2.2/0.2.3 wieder entfernt (Ursache geklärt).

## 0.2.3

- Diagnose-Kurskorrektur: `showMeterValuesForm` (Button "Zählerstand") ist
  entgegen der Vermutung in 0.2.2 nur ein Datums-Bereich-Formular für
  historische Werte, keine Übersicht mehrerer Parameter. Diagnose-Aufruf
  dafür wieder entfernt (spart unnötige zusätzliche Gateway-Anfragen).
- Neue Diagnose: Voller Body einer erfolgreichen `showMeterProfile`-Antwort
  wird jetzt geloggt (inkl. Anzahl gefundener Werte-Zeilen), um zu prüfen,
  ob dort bereits mehrere Parameter (Verbrauch, Einspeisung, Tarife) in
  einer Tabelle stehen, die die aktuelle Extraktion (nur 1. Treffer)
  übersieht.

## 0.2.2

- Diagnose (noch kein Feature): Zusätzlicher, temporärer Abruf der
  "Zählerstand"-Ansicht (`action=showMeterValuesForm`) - das ist der zweite
  Button im Zähler-Menü neben "Zählerprofil". Vermutung: Diese Ansicht
  liefert alle OBIS-Werte eines Zählers (Verbrauch, Einspeisung,
  Tarifzeiten...), nicht nur den einen Wert, den "Zählerprofil" liefert.
  Antwort wird als Warnung geloggt, um die Tabellenstruktur zu ermitteln.

## 0.2.1

- Fix: Wenn nach dem 0.2.0-Update die Integration nicht neu eingerichtet
  wurde, verschwanden alle Entitäten stillschweigend (0 Sensoren, aber auch
  kein Fehler). Löst jetzt einen klaren Fehler ("Keiner der konfigurierten
  Zähler wurde gefunden ... Integration entfernen und neu einrichten") aus,
  statt sich schweigend leer zu melden.

## 0.2.0

**Breaking Change - einmaliges Neu-Einrichten der Integration nötig!**

- Fix (eigentliche Ursache des "Parameterfehler"-Bugs): Die `mid` eines
  Zählers aus der Gateway-API ist NICHT stabil, sondern rotiert bei jedem
  Login neu. Die Integration hatte die `mid` beim Einrichten einmalig
  gespeichert und bei jedem Update wiederverwendet - das schlug fehl, sobald
  sich die `mid` geändert hatte. Jetzt wird stattdessen der stabile,
  sichtbare Zählername (z.B. "01005e318002.1lgz0081554715.sm") gespeichert
  und bei jedem Update-Zyklus die aktuell gültige `mid` frisch nachgeschlagen.
- Da sich das Format der gespeicherten Zähler-Auswahl geändert hat
  (Zählername statt `mid`), muss die Integration einmalig entfernt und neu
  eingerichtet werden (Einstellungen → Geräte & Dienste → Integration
  entfernen → neu hinzufügen).

## 0.1.10

- Erkenntnis: Das Gateway antwortet auf `action=showMeterProfile&mid=<id>`
  mit "Parameterfehler" statt der erwarteten Messwert-Tabelle - das ist ein
  anderes Problem als die bisherigen HTML-Parsing-Bugs. Vermutung: falscher
  Parametername oder das CSRF-Token muss zwischen den Formular-Schritten
  aktualisiert werden.
- Diagnose: Volle Antwort auf die Zählerliste (`action=meterform`) sowie die
  tatsächlich gesendeten POST-Daten beim Messwert-Abruf werden jetzt
  zusätzlich geloggt, um die exakten Formularfeld-Namen dieser
  Firmware-Version zu ermitteln.

## 0.1.9

- Diagnose: Voller HTML-Body statt nur der ersten 3000 Zeichen wird jetzt
  bei fehlendem Messwert geloggt - die relevante Tabelle liegt oft erst
  nach dem Navigationsmenü, das allein schon über 3000 Zeichen einnimmt.

## 0.1.8

- Diagnose: Wenn der Messwert auf der Zähler-Detailseite nicht gefunden
  wird, wird jetzt automatisch ein HTML-Ausschnitt als Warnung (nicht nur
  Debug) in die normalen HA-Protokolle geschrieben - kein manuelles
  Aktivieren von Debug-Logging mehr nötig, um das zu diagnostizieren.

## 0.1.7

- Fix: Fehler wurden falsch klassifiziert. Ein erfolgreicher Login (HTTP 200)
  mit anschließendem Parsing-Problem wurde fälschlich als "Benutzername
  oder Passwort falsch" angezeigt, obwohl die Zugangsdaten korrekt waren.
  Neue eigene Fehlerklasse `PPCSmgwParsingError` für diesen Fall, mit
  eigener, korrekter Fehlermeldung.
- Umstrukturiert: Der Login-Schritt (Zugangsdaten) prüft jetzt NUR noch die
  Authentifizierung. Der Abruf der Zählerliste läuft in einem eigenen,
  nachgelagerten Schritt mit eigener Fehleranzeige - ein Zähler-Problem
  erscheint nicht mehr fälschlich im Login-Formular.

## 0.1.6

- Fix (Hauptursache des Login-Problems): Das Gateway verwendet in seinem
  HTML **einfache Anführungszeichen** (`'...'`) statt doppelter (`"..."`).
  Sämtliche HTML-Extraktion (Token, Zählerliste, Messwerte) suchte aber nur
  nach doppelten Anführungszeichen und fand dadurch nichts. Jetzt werden
  beide Varianten erkannt. Gegen echte, vom Nutzer bereitgestellte
  HTML-Auszüge verifiziert.
- Fix: HTML-Entities in Zählernamen/-werten (z. B. `&auml;` für „ä") werden
  jetzt korrekt dekodiert.
- Neu: Versionsnummer wird jetzt auch im Zugangsdaten-Schritt angezeigt
  (nicht nur im ersten Schritt), um Browser-Cache-Verwirrung zu vermeiden.

## 0.1.5

- Fix: Die Debug-Diagnoseausgabe im Login-Formular enthielt rohe HTML-Tags
  (z. B. `<input>`), die vom Home-Assistant-Frontend als echte, leere
  Formularfelder gerendert wurden statt als lesbarer Text. Wird jetzt vor
  der Anzeige korrekt HTML-escaped.

## 0.1.4

- Fix: Token-Extraktion nach dem Login schlug fehl ("Token gefunden: false"),
  weil die Integration blind das allererste `<input>`-Element der Seite als
  Token angenommen hatte. Sucht jetzt gezielt nach `name="tkn"` statt nach
  Position auf der Seite. Betraf Nutzer mit neuerer SMGW-Firmware, bei der
  das Token-Feld nicht mehr das erste Input-Element ist.
- Diagnosetext bei fehlendem Token zeigt jetzt zusätzlich alle gefundenen
  `<input>`-Tags der Seite an.

## 0.1.3

- Neu: Bei fehlgeschlagenem Login werden die technischen Diagnosedetails
  (Status-Codes, gesendeter Authorization-Header, Server-Antwort) jetzt
  direkt im Config-Flow-Formular angezeigt – kein Blick in die HA-Logs mehr
  nötig, um Login-Probleme zu diagnostizieren.

## 0.1.2

- Erweitertes Debug-Logging: Der vollständige gesendete
  `Authorization`-Header sowie die `WWW-Authenticate`-Antwort bei einem
  erneuten 401 werden jetzt geloggt, um Login-Probleme direkt mit einem
  erfolgreichen `curl --digest`-Test vergleichen zu können.

## 0.1.1

- Fix: Digest-Auth-Header war fehlerhaft formatiert (`qop` und `algorithm`
  wurden fälschlich in Anführungszeichen gesendet), wodurch der Login am
  Gateway trotz korrekter Zugangsdaten mit „Login falsch" fehlschlug.
  Header-Format wurde gegen einen erfolgreichen `curl --digest`-Testaufruf
  gegen ein echtes Gateway verifiziert.
- Neu: Installierte Versionsnummer wird jetzt direkt im ersten
  Config-Flow-Dialog angezeigt (bevor IP/Zugangsdaten eingegeben werden),
  sowie als Geräte-Softwareversion nach erfolgreicher Einrichtung.
- Fix: GitHub-Owner-Schreibweise in `manifest.json` korrigiert
  (`Lutarym` statt `LUTARYM`).

## 0.1.0

- Erste Version: Config Flow mit getrennten Schritten (Host-Erreichbarkeits-
  prüfung auf Port 443 → Zugangsdaten-Login → Zählerauswahl), ein Gerät mit
  Sensor-Entitäten pro ausgewähltem Zähler, Deutsch/Englisch-Übersetzung.
