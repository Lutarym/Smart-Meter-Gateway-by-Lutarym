# PPC Smart Meter Gateway (iMSys) by Lutarym

Diese Home-Assistant-Integration liest ein **PPC Smart Meter Gateway**
(LTE SMGW) lokal über seine **HAN-Schnittstelle** aus und bringt die
Messwerte deines intelligenten Stromzählers direkt in Home Assistant.

Dabei werden **alle Messwerte und Auswertungsprofile ausgelesen, die das
Gateway bereitstellt**, und jeder einzelne davon bekommt seine eigene
Home-Assistant-Entität. Dazu gehören zum Beispiel der Bezug
(OBIS `1-0:1.8.0`), die Einspeisung (`1-0:2.8.0`) und die vom
Messstellenbetreiber freigeschalteten Auswertungsprofile. Es wird also
nichts vorab ausgewählt oder herausgefiltert: Du bekommst alles, was dein
Gateway hergibt, und entscheidest in Home Assistant selbst, was du davon
nutzen möchtest. Die wichtigsten Messwerte sind sofort aktiv, während
seltener benötigte Metadaten-Entitäten zwar angelegt, aber zunächst
deaktiviert bleiben, sodass du sie bei Bedarf mit einem Klick einschalten
kannst.

Darüber hinaus kannst du die **historischen Verbrauchsdaten** deines
Netzbetreibers nachträglich importieren, etwa aus dem Kundenportal der
TraveNetz AG. Der Import schließt nahtlos an die laufende
Home-Assistant-Statistik an, sodass keine spätere Reparatur der Werte
nötig ist.

> **Hardware-Kompatibilität**
>
> Diese Integration wurde ausschließlich mit einem Gateway von PPC (Power
> Plus Communications AG) entwickelt und getestet, wie es beispielsweise
> die TraveNetz AG einsetzt. Da das BSI mit der Technischen Richtlinie
> [TR-03109-1](https://www.bsi.bund.de/dok/7614914) eine gemeinsame
> HAN-Schnittstelle für alle intelligenten Messsysteme vorschreibt, ist es
> durchaus möglich, dass die Integration auch mit Gateways anderer
> Hersteller zusammenarbeitet. Garantieren lässt sich das allerdings
> nicht: Die Richtlinie vereinheitlicht vor allem die Transport- und
> Sicherheitsebene (TLS, HTTP und die Digest-Authentifizierung), während
> der konkrete Ablauf hinter der Weboberfläche des Geräts von Hersteller zu
> Hersteller abweichen kann und hier nur für PPC-Geräte umgesetzt wurde.
> Wenn du ein intelligentes Messsystem eines anderen Herstellers besitzt,
> probiere die Integration gerne aus und [gib mir
> Rückmeldung](#hilfe--kontakt). Eine Gewähr, dass alles zuverlässig läuft,
> kann ich dir aber nicht geben.

## Inhalt

- [Installation](#installation)
- [Einrichtung](#einrichtung)
- [Historische Daten importieren (CSV-Import)](#historische-daten-importieren-csv-import)
- [Eingebauter Schutz vor Anomalien](#eingebauter-schutz-vor-anomalien)
- [Service-Referenz](#service-referenz)
- [Fehlerbehebung](#fehlerbehebung)

## Installation

### Über HACS

1. Öffne in HACS unter Integrationen über das Menü (**⋮**) die
   benutzerdefinierten Repositories.
2. Trage die URL dieses Repositories ein und wähle als Kategorie
   **Integration**.
3. Installiere anschließend "PPC Smart Meter Gateway (iMSys) by Lutarym".
4. Starte Home Assistant danach **komplett neu**.

### Manuell

Kopiere den gesamten Inhalt von `custom_components/lutarym_ppc_smgw/` in
das Verzeichnis `config/custom_components/lutarym_ppc_smgw/` deiner
Home-Assistant-Installation und starte Home Assistant anschließend neu.

## Einrichtung

Gehe zu Einstellungen, dann zu Geräte & Dienste, und füge dort über
"Integration hinzufügen" die "PPC Smart Meter Gateway"-Integration hinzu.
Der Assistent führt dich durch die folgenden Schritte:

1. Zuerst gibst du die **Host- bzw. IP-Adresse** deines Gateways an. Die
   Standardvorgabe ist `172.20.0.1`, und die Erreichbarkeit wird über Port
   443 geprüft.
2. Danach folgen die **HAN-Zugangsdaten**, die du von deinem
   Messstellenbetreiber erhalten hast.
3. Anschließend **wählst du die Zähler aus**, die als Sensoren angelegt
   werden sollen.
4. Optional kannst du zusätzlich **Auswertungsprofile auswählen**.
5. Zum Schluss lassen sich optional **historische Daten importieren** (mehr
   dazu im nächsten Abschnitt).

> **⚠️ Fehlen dir Messwerte wie die Einspeisung (`2.8.0`)? Dann müssen sie
> beim Netzbetreiber freigeschaltet werden.**
>
> Das Gateway liefert über die HAN-Schnittstelle nur jene Messwerte und
> Auswertungsprofile, die dein Messstellen- oder Netzbetreiber dafür
> freigeschaltet hat. Häufig ist standardmäßig nur der Bezug
> (`1-0:1.8.0`) aktiv, während die Einspeisung (`1-0:2.8.0`) oder weitere
> Werte fehlen, selbst wenn dein Zähler sie technisch längst erfasst.
> Wenn erwartete Werte bei "Zähler auswählen" oder bei den
> Auswertungsprofilen also gar nicht erst auftauchen, liegt das nicht an
> dieser Integration, sondern an der fehlenden Freischaltung. Wende dich in
> diesem Fall an deinen Netzbetreiber und bitte ausdrücklich darum, die
> gewünschten OBIS-Kennzahlen für die HAN-Schnittstelle freizuschalten
> (etwa `1-0:2.8.0` für die Einspeisung). Sobald das erledigt ist,
> erscheinen die Werte beim nächsten Abruf automatisch.

### Welche IP-Adresse hat mein Gateway?

Eine deutschlandweit einheitliche HAN-IP gibt es leider nicht, denn sie
hängt vom Messstellenbetreiber und der Konfiguration des Gateways ab.
Folgende Anhaltspunkte helfen weiter:

- Die **Werkseinstellung bei PPC-Geräten** lautet laut
  PPC-Verbraucherhandbuch `192.168.1.200`.
- Die **TraveNetz AG** nutzt `172.20.0.1`, was zugleich die Standardvorgabe
  dieser Integration ist und mit dieser Adresse entwickelt und getestet
  wurde.
- **Bei anderen Betreibern** findest du die HAN-IP meist in den
  Zugangsunterlagen, die du zusammen mit den Zugangsdaten bekommen hast.
  Ist im Gateway DHCP aktiviert, vergibt dein Router automatisch eine
  Adresse; du kannst dann in der DHCP-Client-Liste deines Routers
  nachsehen, welche IP das Gateway erhalten hat. Bei manchen Gateways ist
  der DHCP-Server jedoch deaktiviert, sodass die HAN-Schnittstelle nur
  unter der fest vom Betreiber vorgegebenen Adresse antwortet.

Passt die Standardvorgabe nicht, trägst du im ersten Schritt einfach die
IP-Adresse ein, die dir dein Messstellenbetreiber genannt hat.

Am Ende der Einrichtung zeigt dir der Assistent eine Übersicht mit dem
Ergebnis jedes Schritts. Das **Abrufintervall** kannst du danach jederzeit
in den Optionen der Integration ändern. Voreingestellt sind 15 Minuten,
was dem Ausleseintervall des Gateways entspricht; kürzer als 5 Minuten geht
allerdings nicht.

## Historische Daten importieren (CSV-Import)

Das Gateway wird erst ab dem Zeitpunkt der Ersteinrichtung von Home
Assistant ausgelesen, weshalb die Statistik ohne Import auf diesen Zeitraum
beschränkt bleibt. Mit einem **CSV-Import** trägst du die komplette
Historie seit dem Zähler-Einbau nach, und zwar mit den echten Messwerten
deines Netzbetreibers, ganz ohne Schätzung oder Skalierung.

### CSV von deinem Netzbetreiber besorgen

Bei der TraveNetz AG (und vermutlich bei weiteren Netzbetreibern mit einem
ähnlichen Kundenportal) exportierst du im Online-Kundenportal die
Verbrauchswerte für OBIS `1-0:1.8.0` ("Energie bezogen") als CSV-Datei, am
besten für den gesamten Zeitraum vom Zähler-Einbau bis heute. Unterstützt
werden sowohl der tägliche als auch der viertelstündliche Export; für
dynamische Stromtarife ist dabei der viertelstündliche Export der
passende.

> **Wichtig: Die CSV sollte möglichst aktuell sein.**
>
> Lade die Datei unmittelbar vor dem Import aus dem Kundenportal herunter,
> sodass sie bis zur aktuellsten verfügbaren Stunde reicht und idealerweise
> höchstens eine Stunde alt ist. Der Grund liegt darin, dass der
> importierte Verlauf an den aktuellen Zählerstand deines Gateways
> angehängt wird. Klafft zwischen dem Ende der CSV und dem jetzigen
> Zeitpunkt eine große Lücke, weil die Datei etwa schon Tage alt ist,
> passen die importierte Historie und die laufende Messung nicht mehr sauber
> zusammen. Besorge dir deshalb erst die frische CSV und importiere sie dann
> sofort.

### Erwartetes CSV-Format

Die Integration erwartet das Exportformat des TraveNetz-Kundenportals mit
den folgenden Eigenschaften:

- Als **Kodierung** UTF-8, mit oder ohne BOM.
- Als **Trennzeichen** das Semikolon (`;`).
- Als **Dezimaltrennzeichen** das Komma im deutschen Format, etwa
  `0,706622`.
- Die **ersten beiden Zeilen** sind Kopfzeilen und werden automatisch
  übersprungen.
- **Ab der dritten Zeile** folgt eine Datenzeile pro Messintervall mit
  diesen Spalten:

  | Spalte | Beispielinhalt | Bedeutung |
  |---|---|---|
  | 1 | `24.11.2025 - 00:00:00` | Beginn des Intervalls in deutscher Lokalzeit (`DD.MM.YYYY - HH:MM:SS`) |
  | 2 | `24.11.2025 - 00:15:00` | Ende des Intervalls, woraus sich die Intervalldauer ergibt |
  | 3 | `0,706622` | mittlere Leistung dieses Intervalls in kW (Komma als Dezimaltrennzeichen) |
  | 4 | `kW` | Einheit laut Export |
  | 5 | `E` | Status-Kennzeichen des Netzbetreibers |

  Eine Beispielzeile aus dem viertelstündlichen Export sieht so aus:

  ```
  "24.11.2025 - 00:00:00";"24.11.2025 - 00:15:00";"0,706622";"kW";"E";
  ```

Ein wichtiger Hinweis zu Spalte 3: Dort steht die mittlere **Leistung in
kW**, nicht direkt die Energiemenge, auch wenn der Export das manchmal
etwas irreführend darstellt. Die Integration rechnet diesen Wert selbst
korrekt in kWh um, sodass du an der CSV nichts anpassen musst. Sowohl der
viertelstündliche als auch der tägliche Export werden dabei zuverlässig
verarbeitet.

Fehlende Intervalle erkennt die Integration an einem `-` anstelle eines
Zahlenwerts. Fehlt ein einzelnes Intervall mitten im Datenbereich, wird es
automatisch linear zwischen den beiden benachbarten echten Werten
aufgefüllt. Fehlen dagegen Intervalle ganz am Anfang oder Ende, also vor
der ersten oder nach der letzten echten Messung, werden diese Werte nicht
erfunden. Die Zeitumstellung wird korrekt berücksichtigt, einschließlich
des Wechsels zwischen Sommer- und Winterzeit in der Zeitzone
Europe/Berlin.

Andere CSV-Formate, etwa mit einem Komma als Trennzeichen, mit englischem
Zahlenformat oder mit einer anderen Spaltenreihenfolge, werden nicht
unterstützt und führen beim Import zu einer Fehlermeldung.

### Was beim Import passiert

Damit die importierte Historie exakt zum tatsächlichen Zählerstand passt,
geht der Import in drei Schritten vor:

1. Zuerst wird der **aktuelle Zählerstand** deines Gateways ausgelesen
   (OBIS `1-0:1.8.0`, "Energie bezogen").
2. Dieser Wert wird auf die **letzte Zeile deiner CSV** gelegt, also auf
   den jüngsten Zeitpunkt in der Datei.
3. Von dort aus werden die CSV-Werte **rückwärts in die Vergangenheit**
   eingetragen, indem Stunde für Stunde der jeweilige Verbrauch abgezogen
   wird, bis der Anfang der Datei erreicht ist.

Auf diese Weise stimmt das Ende der importierten Reihe garantiert mit dem
echten aktuellen Zählerstand überein, und der Verlauf geht lückenlos in die
laufende Aufzeichnung über.

Genau deshalb sollte die CSV möglichst frisch sein, wie oben bereits
erwähnt: Je näher die letzte CSV-Zeile am aktuellen Zeitpunkt liegt, desto
genauer passt der importierte Verlauf zum realen Zählerstand. Ist die Datei
dagegen schon mehrere Stunden oder Tage alt, wird der aktuelle Wert auf
einen zu weit zurückliegenden Zeitpunkt gelegt, und die jüngste Zeit fehlt
im Import.

### Import beim Einrichten

Im letzten Schritt des Einrichtungsassistenten ("Historische Daten
importieren") stehen dir zwei Felder zur Verfügung. Über den
**CSV-Datei-Upload** lädst du die exportierte Datei hoch. Beim **Wert an
der ersten Zeile (kWh)** trägst du optional einen Startzählerstand ein,
falls deine CSV nicht am Tag des Zähler-Einbaus beginnt.

Lässt du den Datei-Upload leer, findet kein Import statt, und die
Integration wird ganz normal ohne historische Daten eingerichtet.
Andernfalls startet der Import automatisch nach Abschluss der Einrichtung,
sobald die Entitäten existieren, und meldet das Ergebnis als
Benachrichtigung in Home Assistant.

Damit der importierte Verlauf sauber an die laufende Statistik anschließt,
richtest du die Integration am besten zuerst ein, wartest einen ersten
Live-Abruf ab und importierst erst danach die CSV.

### Import nachträglich (bestehende Installation)

Du kannst den Import auch später auslösen, ohne die Integration neu
einzurichten. Rufe dazu unter Entwicklerwerkzeuge und dann Aktionen den
Service `lutarym_ppc_smgw.import_history` auf:

```yaml
action: lutarym_ppc_smgw.import_history
data:
  csv_path: /config/imsys_export.csv
  start_value: 0
```

Dafür muss die CSV-Datei vorher auf dem Home-Assistant-Host liegen, zum
Beispiel über das File-Editor-Add-on im Verzeichnis `/config/`. Pfad und
Dateiname im Beispiel passt du entsprechend an.

Bevor du den echten Import startest, kannst du ihn mit `dry_run: true`
gefahrlos durchrechnen lassen. In diesem Modus ermittelt der Service das
komplette Ergebnis und meldet, was er schreiben würde, also die Anzahl der
Stundenpunkte, die Aufschlüsselung nach Monaten sowie den errechneten
Start- und Endwert, ohne dabei tatsächlich etwas in die Statistik zu
schreiben. So prüfst du Format und Größenordnung vorab:

```yaml
action: lutarym_ppc_smgw.import_history
data:
  csv_path: /config/imsys_export.csv
  dry_run: true
```

Die Angabe `target_entity` kannst du weglassen, wenn nur ein einziges
Gateway konfiguriert ist, denn dann wird die Ziel-Entität automatisch
gefunden. Bei mehreren Gateways musst du sie dagegen ausdrücklich angeben,
etwa `sensor.ppc_smgw_1_8_0`.

Rufst du den Import erneut auf, überschreibt er einen vorherigen Import für
den abgedeckten Zeitraum vollständig. Das ist praktisch, wenn du
beispielsweise eine aktuellere CSV mit mehr Tagen einspielen möchtest.

## Eingebauter Schutz vor Anomalien

Für Sensoren mit `state_class: total_increasing` verlässt sich die
Integration auf die eigene Langzeit-Statistik von Home Assistant. Diese
erkennt einen fallenden Rohwert automatisch als Zähler-Reset und führt die
Summe trotzdem korrekt fort. Zwei Mechanismen sorgen dafür, dass diese
Statistik erst gar keine fehlerhaften Ausgangsdaten erhält.

Der erste ist eine **Verbindungstoleranz** im Coordinator: Bis zu drei
aufeinanderfolgende fehlgeschlagene Auslesezyklen führen nicht dazu, dass
die Entität als "nicht verfügbar" gilt, sondern der letzte bekannte Wert
bleibt aktiv. Das ist wichtig, weil eine kurzzeitig nicht verfügbare
Entität von Home Assistant fälschlich als Reset gewertet werden kann. Erst
wenn der Ausfall länger anhält, wird die Entität wie gewohnt als nicht
verfügbar markiert.

Der zweite Mechanismus ist eine **Plausibilitätsprüfung**, ebenfalls im
Coordinator. Offensichtlich unsinnige Messwerte, also negative Werte,
R�cksprünge oder ein unplausibler Sprung von mehr als 20 kWh innerhalb
eines Zyklus, werden verworfen, bevor sie in die Entität oder die Statistik
gelangen. Stattdessen bleibt der letzte bekannte plausible Wert stehen, und
der echte Wert wird beim nächsten plausiblen Zyklus übernommen. Nach einem
Import oder einem Neustart wird der Referenzwert für diese Prüfung aus dem
importierten Endstand beziehungsweise dem letzten bekannten Zustand
rekonstruiert, damit schon der allererste Abruf abgesichert ist.

Für echte Datenlücken, wie sie etwa nach einem längeren Ausfall jenseits
der Verbindungstoleranz entstehen, bleibt der CSV-Import der genauere Weg,
weil er echte Messwerte statt einer Schätzung verwendet.

## Service-Referenz

| Service | Zweck | Wichtigste Felder |
|---|---|---|
| `lutarym_ppc_smgw.import_history` | Historische Verbrauchsdaten importieren | `csv_path`, `start_value` |

Alle Felder dieses Service sind optional:

- `csv_path` gibt den Pfad zur TraveNetz-CSV auf dem Home-Assistant-Host
  an. Das ist der übliche Weg.
- `start_value` legt den Startzählerstand in kWh für die erste CSV-Zeile
  fest. Voreingestellt ist 0.
- `target_entity` bestimmt die Ziel-Entität und wird bei genau einem
  Gateway automatisch ermittelt.
- `dry_run` löst einen reinen Testlauf aus, ohne etwas zu schreiben.
  Voreingestellt ist `false`.
- `source_entity`, `start_date` und `monthly_kwh` dienen einer
  alternativen Importquelle aus einer bereits vorhandenen Entität mit
  optionaler monatlicher Skalierung. Das ist ein Sonderfall, den du für den
  normalen CSV-Import nicht benötigst.

Die vollständigen Feldbeschreibungen erscheinen direkt im
Home-Assistant-Formular unter Entwicklerwerkzeuge und Aktionen.

## Fehlerbehebung

**Ein erwarteter Messwert fehlt, zum Beispiel die Einspeisung (`2.8.0`).**
In aller Regel ist dieser Wert vom Netzbetreiber nicht für die
HAN-Schnittstelle freigeschaltet, es handelt sich also nicht um einen
Fehler der Integration. Wie du die Freischaltung anfragst, steht im
hervorgehobenen Hinweis im Abschnitt [Einrichtung](#einrichtung).

**Der CSV-Import schlägt fehl oder meldet "keine verwertbaren
Datenzeilen".** Prüfe in diesem Fall das Format wie im Abschnitt
[Erwartetes CSV-Format](#erwartetes-csv-format) beschrieben. Portale
anderer Netzbetreiber können ein abweichendes Exportformat verwenden, das
nicht unterstützt wird.

**Nach der Einrichtung erscheinen mehrere Entitäten mit ähnlichem Namen.**
Das kann passieren, wenn das Gerät mehrfach neu eingerichtet wurde. Prüfe
unter Entwicklerwerkzeuge und Statistiken, welche Entität aktuell noch
lebendig ist, und entferne veraltete Karteileichen bei Bedarf über die
Funktion "Probleme beheben" der Statistik-Übersicht.

**Im Protokoll tauchen Verbindungsfehler auf ("Server disconnected").**
Einzelne, seltene Aussetzer werden toleriert, denn die Entität bleibt bis
zu drei aufeinanderfolgende fehlgeschlagene Zyklen auf dem letzten
bekannten Wert. Halten die Verbindungsprobleme dagegen an, prüfe die
Netzwerkverbindung zum Gateway und löse gegebenenfalls über den Button
"Gateway neu starten" der Integration einen Neustart aus.

## Hilfe & Kontakt

Wenn du Hilfe bei der Einrichtung, beim CSV-Import oder bei einem anderen
Problem brauchst, kannst du mich gerne kontaktieren, am einfachsten über
den [Issue-Tracker dieses
Repositories](https://github.com/Lutarym/ha-lutarym-ppc-smgw/issues).
Besonders willkommen sind Rückmeldungen dazu, welche Messstellenbetreiber
und Gateways funktionieren und welche nicht, denn sie helfen mir, die
Kompatibilitätshinweise weiter zu verbessern.

## Lizenz

[MIT](LICENSE)
