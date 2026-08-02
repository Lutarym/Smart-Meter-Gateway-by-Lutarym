# PPC Smart Meter Gateway (iMSys) by Lutarym

Home-Assistant-Integration für das **PPC Smart Meter Gateway** (LTE SMGW),
ausgelesen über die lokale **HAN-Schnittstelle**. Legt für jeden am
Gateway gefundenen Zähler-Messwert (z.B. OBIS `1-0:1.8.0` "Bezug",
`1-0:2.8.0` "Einspeisung") sowie für konfigurierte Auswertungsprofile
eigene Home-Assistant-Entitäten an.

Ab Version **1.13.0** kann zusätzlich die **historische Verbrauchsdaten**
deines Netzbetreibers importiert werden (z.B. von TraveNetz AG), und ab
Version **1.14.0** stehen Werkzeuge zur Reparatur fehlerhafter
Langzeit-Statistiken direkt über Home Assistant bereit.

## Inhalt

- [Installation](#installation)
- [Einrichtung](#einrichtung)
- [Historische Daten importieren (CSV-Import)](#historische-daten-importieren-csv-import)
- [Statistik-Reparatur über Entwicklerwerkzeuge](#statistik-reparatur-über-entwicklerwerkzeuge)
- [Eingebauter Schutz vor Anomalien](#eingebauter-schutz-vor-anomalien)
- [Service-Referenz](#service-referenz)
- [Fehlerbehebung](#fehlerbehebung)

## Installation

### Über HACS

1. HACS → Integrationen → **⋮** → Benutzerdefinierte Repositories
2. URL dieses Repositories eintragen, Kategorie **Integration**
3. "PPC Smart Meter Gateway (iMSys) by Lutarym" installieren
4. Home Assistant **komplett neu starten**

### Manuell

Den kompletten Inhalt von `custom_components/lutarym_ppc_smgw/` in dein
`config/custom_components/lutarym_ppc_smgw/`-Verzeichnis kopieren,
danach Home Assistant neu starten.

## Einrichtung

Einstellungen → Geräte & Dienste → Integration hinzufügen → "PPC Smart
Meter Gateway" suchen. Der Assistent führt durch:

1. **Host/IP-Adresse** des Gateways (wird auf Erreichbarkeit über Port
   443 geprüft)
2. **HAN-Zugangsdaten** (von deinem Messstellenbetreiber erhalten)
3. **Zähler auswählen**, die als Sensoren angelegt werden sollen
4. **Auswertungsprofile auswählen** (optional, z.B. "Bezug 15-Minuten")
5. **Historische Daten importieren** (optional, siehe nächster Abschnitt)

## Historische Daten importieren (CSV-Import)

Da das Gateway erst ab dem Zeitpunkt der Ersteinrichtung Daten
aufzeichnet, bleibt die Home-Assistant-Statistik ohne Import auf diesen
Zeitraum beschränkt. Über einen **1:1-CSV-Import** lässt sich die
komplette Historie seit Zähler-Einbau nachtragen — mit den **echten**
Messwerten deines Netzbetreibers, ohne Schätzung oder Skalierung.

### CSV von deinem Netzbetreiber besorgen

Bei TraveNetz AG (und vermutlich bei weiteren Netzbetreibern mit
ähnlichem Kundenportal): im Online-Kundenportal die **stündlichen**
Verbrauchswerte für OBIS `1-0:1.8.0` ("Energie bezogen") als CSV
exportieren, für den gewünschten Zeitraum (idealerweise ab Zähler-Einbau
bis heute).

### Erwartetes CSV-Format

Die Integration erwartet **exakt** das Exportformat des TraveNetz-
Kundenportals:

- **Kodierung**: UTF-8 (mit oder ohne BOM)
- **Trennzeichen**: Semikolon (`;`)
- **Dezimaltrennzeichen**: Komma (deutsches Format, z.B. `4,289460`)
- **Erste zwei Zeilen**: Kopfzeilen, werden automatisch übersprungen
- **Ab Zeile 3**: eine Datenzeile pro Stunde, Spalten:

  | Spalte | Beispielinhalt | Bedeutung |
  |---|---|---|
  | 1 | `27.11.2025 - 00:00:00` | Beginn der Stunde, **deutsche Lokalzeit** (`DD.MM.YYYY - HH:MM:SS`) |
  | 2 | `27.11.2025 - 01:00:00` | Ende der Stunde (wird nicht ausgewertet) |
  | 3 | `0,489460` | Messwert dieser Stunde (Komma-Dezimal) |
  | 4 | `kW` | Einheit lt. Export - wird trotz Beschriftung als **kWh dieser Stunde** interpretiert (bei 1-Stunden-Intervallen numerisch identisch) |
  | 5 | `W` | Status (`W` = valide, `-` als Wert = fehlende Messung) |

  Beispielzeile:
  ```
  "27.11.2025 - 00:00:00";"27.11.2025 - 01:00:00";"0,489460";"kW";"W";
  ```

- **Fehlende Stunden**: Zeilen mit `-` statt einem Zahlenwert (Status
  meist `F`) werden als Lücke erkannt. Einzelne fehlende Stunden
  **innerhalb** des Datenbereichs werden automatisch linear zwischen den
  beiden benachbarten echten Werten aufgefüllt. Am Anfang/Ende
  fehlende Stunden (vor der ersten bzw. nach der letzten echten Messung)
  werden **nicht** erfunden.
- **Zeitumstellung**: wird korrekt berücksichtigt (Europe/Berlin,
  inklusive Sommer-/Winterzeit-Wechsel).

Andere CSV-Formate (z.B. mit Komma statt Semikolon als Trennzeichen,
englischem Zahlenformat oder anderer Spaltenreihenfolge) werden
**nicht** unterstützt und führen zu einer Fehlermeldung beim Import.

### Import beim Einrichten

Im letzten Schritt des Einrichtungsassistenten ("Historische Daten
importieren"):

- **CSV-Datei-Upload**: die exportierte Datei per Datei-Auswahl
  hochladen
- **Wert an der ersten Zeile (kWh)**: der Zählerstand, der am
  allerersten Zeitpunkt der CSV galt.
  - Startet die CSV genau am Tag des Zähler-Einbaus → **0** eintragen
    (oder leer lassen, Standardwert ist 0)
  - Deckt die CSV nur einen Teil-Zeitraum ab (z.B. weil der Zähler
    schon vorher existierte) → den tatsächlichen Zählerstand zum
    CSV-Startzeitpunkt eintragen

Beide Felder sind optional - bleibt der Datei-Upload leer, wird kein
Import durchgeführt, die Integration wird ganz normal ohne historische
Daten eingerichtet.

Der Import läuft automatisch **nach** dem Abschluss der Einrichtung
(sobald die Entities existieren) und meldet das Ergebnis als
Benachrichtigung in Home Assistant.

### Import nachträglich (bestehende Installation)

Ohne die Integration neu einrichten zu müssen, über **Entwicklerwerkzeuge
→ Aktionen** den Service `lutarym_ppc_smgw.import_history` aufrufen:

```yaml
action: lutarym_ppc_smgw.import_history
data:
  csv_path: /config/imsys_export.csv
  start_value: 0
```

Die CSV-Datei muss dafür vorher auf den Home-Assistant-Host gelegt
werden, z.B. über den File-Editor-Add-on direkt nach `/config/` (der
Pfad im Beispiel geht davon aus, dass die Datei dort unter dem Namen
`imsys_export.csv` liegt - Namen entsprechend anpassen).

`target_entity` kann weggelassen werden, wenn genau ein Gateway
konfiguriert ist (wird dann automatisch gefunden) - bei mehreren
Gateways muss die Ziel-Entity explizit angegeben werden, z.B.
`sensor.ppc_smgw_1_8_0`.

Ein erneuter Aufruf **überschreibt** einen vorherigen Import vollständig
(für den abgedeckten Zeitraum) - nützlich, um z.B. eine aktuellere
CSV-Datei mit mehr Tagen einzuspielen.

**Empfehlung vor größeren Importen**: kurz den Recorder pausieren
(Entwicklerwerkzeuge → Aktionen → `Recorder: Deaktivieren`), nach dem
Import wieder aktivieren (`Recorder: Aktivieren`) - nicht zwingend
nötig, aber sicherer bei sehr großen Datenmengen.

## Statistik-Reparatur über Entwicklerwerkzeuge

Home Assistants interne Langzeit-Statistik-Kompilierung kann in seltenen
Fällen ihren Bezugspunkt verlieren (z.B. nach einer Verbindungsstörung
zum Gateway) - sichtbar als plötzlicher Sprung auf 0, eine unrealistisch
hohe Rampe, oder sogar negative Werte in der Statistik, **obwohl** der
angezeigte Live-Wert der Entity die ganze Zeit korrekt war. Zwei Services
helfen, das gezielt zu reparieren, **ohne** einen kompletten Neu-Import.

> **Version 1.18.0 bis 2.1.x** schrieben ihre Statistik-Werte zusätzlich
> selbst direkt (statt sich auf Home Assistants automatische Ableitung zu
> verlassen). Das kollidierte wiederholt mit Home Assistants eigener,
> paralleler Kompilierung derselben Stunde und wurde in **Version 2.4.0**
> wieder entfernt - siehe Abschnitt "Eingebauter Schutz vor Anomalien"
> unten für den aktuellen Mechanismus. Die folgenden Services bleiben
> trotzdem verfügbar, für den Fall, dass doch mal etwas repariert werden
> muss (z.B. für Zeiträume vor Version 1.18.0 oder nach einem längeren
> Ausfall, der über die automatische Verbindungstoleranz hinausgeht).

### Schritt 1: Problem erkennen

Entwicklerwerkzeuge → Aktionen → `recorder.get_statistics` (YAML-Modus),
um die stündlichen Werte um den vermuteten Zeitpunkt herum zu prüfen:

```yaml
action: recorder.get_statistics
data:
  statistic_ids:
    - sensor.ppc_smgw_1_8_0
  start_time: "2026-07-20 00:00:00"
  end_time: "2026-07-23 00:00:00"
  period: hour
  types:
    - sum
    - state
response_variable: result
```

Nach dem Ausführen erscheint das Ergebnis im "Antwort"-Bereich unten im
Fenster. Zwei Muster sind zu unterscheiden:

**Muster A - Reset auf 0 (oder allgemein: Wert zu niedrig)**: `sum`
fällt an einer Stelle plötzlich stark ab (z.B. auf 0) und zählt von dort
an mit realistisch kleinen Schritten weiter, während `state` unauffällig
bleibt.

**Muster B - fälschliche Rampe (Wert zu hoch)**: `sum`/`state` steigen
über mehrere Stunden mit demselben, unrealistisch großen Betrag pro
Stunde an, bis ein deutlich zu hohes Plateau erreicht wird, danach laufen
die Werte wieder normal weiter.

### Schritt 2a: Muster A reparieren

```yaml
action: lutarym_ppc_smgw.repair_statistics_reset
data:
  target_entity: sensor.ppc_smgw_1_8_0
  since: "2026-07-21 09:00:00"
```

`since` = der Zeitpunkt der **ersten** auffällig niedrigen Stunde. Der
Service ermittelt automatisch den letzten gültigen Wert davor, füllt eine
etwaige echte Zeitlücke davor linear auf, und verschiebt alle bereits
vorhandenen Punkte ab `since` um den korrekten Offset nach oben.

### Schritt 2b: Muster B reparieren

```yaml
action: lutarym_ppc_smgw.repair_erroneous_ramp
data:
  target_entity: sensor.ppc_smgw_1_8_0
  ramp_start: "2026-07-20 22:00:00"
  ramp_end: "2026-07-21 10:00:00"
```

`ramp_start` = letzte normale Stunde **vor** dem Anstieg + 1 Stunde (also
die erste auffällige Stunde). `ramp_end` = erste Stunde, in der die
Werte wieder normal (kleine, plausible Schritte) weiterlaufen. Der
Rampen-Zeitraum wird flach aufgefüllt, alle Werte ab `ramp_end` werden
um den ermittelten Überschuss nach unten korrigiert.

### Vor der Reparatur: Recorder pausieren

Für beide Services empfohlen, damit während der Korrektur kein neuer,
live geschriebener Punkt dazwischenfunkt:

1. Entwicklerwerkzeuge → Aktionen → `Recorder: Deaktivieren` ausführen
2. Reparatur-Service ausführen
3. Entwicklerwerkzeuge → Aktionen → `Recorder: Aktivieren` ausführen

### Ergebnis prüfen

Beide Services zeigen als Antwort eine Zusammenfassung (Anzahl
korrigierter Punkte, ermittelter Offset/Überschuss, Wert vor/nach der
Reparatur) - zusätzlich lässt sich mit der `recorder.get_statistics`-
Abfrage von Schritt 1 gegenprüfen, ob der Verlauf jetzt lückenlos und
plausibel ist.

Mit `dry_run: true` lässt sich jeder der beiden Services testweise
ausführen, ohne dass etwas geschrieben wird - zeigt nur, was passieren
würde.

## Eingebauter Schutz vor Anomalien

Statt Statistik-Werte selbst zu schreiben (siehe Hinweis oben), verlässt
sich die Integration seit **Version 2.4.0** bewusst auf Home Assistants
eigene Langzeit-Statistik-Kompilierung für `state_class:
total_increasing`-Sensoren - die erkennt einen fallenden Rohwert
automatisch als Zähler-Reset und führt `sum` trotzdem korrekt fort, ganz
ohne manuelles Zutun. Zwei Mechanismen sorgen dafür, dass diese
Kompilierung erst gar keine schlechten Ausgangsdaten bekommt:

- **Verbindungstoleranz** (`coordinator.py`): bis zu drei
  aufeinanderfolgende fehlgeschlagene Auslesezyklen (Standard-Intervall:
  15 Minuten) führen NICHT dazu, dass die Entity als "nicht verfügbar"
  markiert wird - der letzte bekannte Wert bleibt aktiv sichtbar. Grund:
  eine kurzzeitig "nicht verfügbare" Entity scheint Home Assistants
  Kompilierung als möglichen Reset zu werten und lässt `sum` auf 0
  zurückfallen, obwohl gar keine echte Störung vorlag - erst bei einem
  wiederholten, anhaltenden Ausfall wird die Entity wie gewohnt nicht
  verfügbar.
- **Plausibilitätsprüfung** (`coordinator.py`): einzelne, offensichtlich
  unsinnige Messwerte (negativ, Rücksprung, unplausibler Sprung von mehr
  als 20 kWh in einem Zyklus) werden verworfen, bevor sie überhaupt in
  die Entity bzw. Statistik einfließen - der letzte bekannte plausible
  Wert bleibt stattdessen stehen, der echte Wert wird beim nächsten
  plausiblen Zyklus ganz normal übernommen.

Für echte Lücken (z.B. durch einen längeren Ausfall oder einen
Home-Assistant-Neustart über mehrere Stunden hinweg, also über die
Verbindungstoleranz hinaus) bleibt ein CSV-Import (siehe oben) der
genauere Weg, da er echte Messwerte statt einer Schätzung nutzt.

## Service-Referenz

| Service | Zweck | Pflichtfelder |
|---|---|---|
| `lutarym_ppc_smgw.import_history` | Historische CSV-Daten importieren (oder ältere skalierte Quell-Entity-Variante, siehe Quellcode-Kommentare) | `csv_path` |
| `lutarym_ppc_smgw.repair_statistics_reset` | Reset-auf-0-Fehler in bestehender Statistik reparieren | `since` |
| `lutarym_ppc_smgw.repair_erroneous_ramp` | Fälschliche Rampe in bestehender Statistik reparieren | `ramp_start`, `ramp_end` |

Alle drei Services unterstützen `target_entity` (optional, wird bei genau
einem Gateway automatisch ermittelt) und `dry_run` (optional, Standard
`false`) - vollständige Feldbeschreibungen erscheinen direkt im
Home-Assistant-Formular unter Entwicklerwerkzeuge → Aktionen.

## Fehlerbehebung

**CSV-Import schlägt fehl / "keine verwertbaren Datenzeilen"**: Format
prüfen (siehe [oben](#erwartetes-csv-format)) - andere Netzbetreiber-
Portale können ein abweichendes Exportformat verwenden, das nicht
unterstützt wird.

**Nach der Einrichtung mehrere Entities mit ähnlichem Namen** (z.B.
`sensor.ppc_smgw_..._1_8_0` und eine umbenannte Variante): kann bei
mehrfacher Neu-Einrichtung des Geräts entstehen. Über
Entwicklerwerkzeuge → Statistiken prüfen, welche Entity aktuell noch
lebendig ist (Wert ändert sich bei jedem Neuladen), und veraltete
Karteileichen bei Bedarf über die "Probleme beheben"-Funktion der
Statistik-Übersicht entfernen.

**Verbindungsfehler ("Server disconnected") im Protokoll**: einzelne,
seltene Aussetzer werden ab Version 1.13.3 toleriert (Entity bleibt bis
zu 3 aufeinanderfolgende fehlgeschlagene Zyklen auf dem letzten bekannten
Wert verfügbar, statt sofort "nicht verfügbar" zu werden). Bei
anhaltenden Verbindungsproblemen: Netzwerkverbindung zum Gateway prüfen,
ggf. über den "Gateway neu starten"-Button der Integration einen
Neustart des Gateways auslösen.

## Lizenz

[MIT](LICENSE)
