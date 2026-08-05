# Integrationsversion: 2.4.1
"""Konstanten für die PPC Smart Meter Gateway (iMSys) Integration."""

# Interner, technischer Bezeichner der Integration. Wird u.a. für
# Entity-IDs (z.B. sensor.lutarym_ppc_smgw_...) und als Schlüssel in
# hass.data verwendet. NICHT ändern - eine Änderung würde alle
# bestehenden Entitäten/Config-Entries verwaisen lassen und eine
# komplette Neueinrichtung erzwingen. Der sichtbare Anzeigename der
# Integration ("PPC Smart Meter Gateway (iMSys) by Lutarym") ist davon
# unabhängig und steht in manifest.json/hacs.json.
DOMAIN = "lutarym_ppc_smgw"

# Schlüssel für die in entry.options gespeicherte Zähler-Auswahl (Liste
# von Zähler-"label"-Strings, siehe api.py:_extract_meter_options).
CONF_METER_IDS = "meter_ids"
# Schlüssel für die in entry.options gespeicherte Auswertungsprofil-
# Auswahl (Liste von Profil-"label"-Strings, siehe
# api.py:list_tariff_profiles). None (Schlüssel fehlt) bedeutet "alle
# gefundenen Profile abrufen", eine leere Liste bedeutet "explizit keine".
CONF_TARIFF_IDS = "tariff_ids"

DEFAULT_SCAN_INTERVAL_SECONDS = 900  # Entspricht dem Ausleseintervall des Gateways (15 Min).

# Schlüssel für das in entry.options gespeicherte, nutzerkonfigurierbare
# Poll-Intervall in Sekunden. Fehlt der Schlüssel (z.B. bei Entries, die
# vor Einführung dieser Option angelegt wurden), gilt
# DEFAULT_SCAN_INTERVAL_SECONDS. Untergrenze bewusst bei 5 Minuten, um das
# Gateway (embedded Webserver, nur eine aktive Session) nicht durch zu
# häufige Login-Zyklen zu belasten.
CONF_SCAN_INTERVAL = "scan_interval"
MIN_SCAN_INTERVAL_SECONDS = 300

# Schlüssel für die in entry.options gespeicherte Einstellung, ob
# Auswertungsprofile überhaupt abgefragt werden sollen. Default: False
# (nur 1-0:1.8.0/1-0:2.8.0 + Zähler-Stammdaten + Firmware abrufen - siehe
# ONLY_TRACKED_OBIS_CODES). Auswertungsprofile verursachen pro Profil
# einen zusätzlichen Request-Zyklus (list_tariff_profiles +
# get_tariff_profile_value je Profil) und werden für die reine
# Bezug/Einspeisung-Auswertung nicht benötigt.
CONF_FETCH_TARIFF_PROFILES = "fetch_tariff_profiles"

# Die einzigen OBIS-Codes, die bei deaktivierten Auswertungsprofilen
# (Standardeinstellung) als Werte-Entität angelegt werden - alle anderen,
# vom Gateway ggf. zusätzlich gelieferten OBIS-Zeilen werden ignoriert.
# Reduziert sowohl die Zahl der angelegten Entitäten als auch (indirekt,
# weil weniger relevante Daten verarbeitet werden müssen) den Aufwand pro
# Zyklus.
ONLY_TRACKED_OBIS_CODES = {"1-0:1.8.0", "1-0:2.8.0"}

MANUFACTURER = "Power Plus Communications AG"
MODEL = "LTE Smart Meter Gateway"

# Fester Pfad der HAN-CGI-Schnittstelle auf dem Gateway. Wird mit
# "https://" + Host zur vollen URL zusammengesetzt (siehe api.py).
HAN_PATH = "/cgi-bin/hanservice.cgi"

# WICHTIG: Muss manuell synchron zur "version" in manifest.json gehalten werden.
# Wird im Config-Flow-Dialog und als Geräte-Softwareversion angezeigt, damit
# man die installierte Version prüfen kann, auch bevor eine Verbindung
# erfolgreich zustande kommt.
VERSION = "2.4.1"

# Service "lutarym_ppc_smgw.import_history" - einmaliger Import einer
# korrigierten historischen Zeitreihe für den 1-0:1.8.0-Sensor
# (siehe history_import.py für die eigentliche Logik).
SERVICE_IMPORT_HISTORY = "import_history"
ATTR_START_DATE = "start_date"
ATTR_START_VALUE = "start_value"
ATTR_SOURCE_ENTITY = "source_entity"
ATTR_MONTHLY_KWH = "monthly_kwh"
ATTR_TARGET_ENTITY = "target_entity"
ATTR_DRY_RUN = "dry_run"
# 1:1-CSV-Import (travenetz_import.py) - Pfad zu einer TraveNetz-
# Exportdatei auf dem HA-Host (z.B. /config/imsys_export.csv). Hat
# Vorrang vor dem skalierten Quell-Entity-Modus, falls beides angegeben ist.
ATTR_CSV_PATH = "csv_path"
# Feldname des FileSelector im Einrichtungsassistenten (config_flow.py) -
# der Wert ist eine temporäre Upload-ID, kein Pfad; wird dort sofort in
# einen dauerhaften csv_path (siehe oben) umgewandelt.
ATTR_CSV_UPLOAD = "csv_upload"

# Service "lutarym_ppc_smgw.repair_statistics_reset" (repair_statistics.py) -
# korrigiert einen Statistik-Reset (sum auf 0 gefallen, state aber korrekt
# weitergelaufen) durch exaktes Verschieben der bereits vorhandenen Werte
# um den richtigen Offset - keine Schätzung.
SERVICE_REPAIR_STATISTICS_RESET = "repair_statistics_reset"
ATTR_SINCE = "since"

# Service "lutarym_ppc_smgw.repair_erroneous_ramp" (repair_statistics.py) -
# entfernt einen fälschlich eingefügten linearen Anstieg (Gegenstück zum
# obigen Reset: hier liegt der Fehler-Überschuss zu HOCH statt zu NIEDRIG).
SERVICE_REPAIR_ERRONEOUS_RAMP = "repair_erroneous_ramp"
ATTR_RAMP_START = "ramp_start"
ATTR_RAMP_END = "ramp_end"

# Schlüssel, unter dem der optionale Historien-Import-Auftrag aus dem
# Einrichtungsassistenten (config_flow.py:async_step_history) EINMALIG in
# entry.data zwischengespeichert wird - __init__.py:async_setup_entry
# verarbeitet ihn beim ersten Laden und entfernt ihn danach wieder aus
# entry.data (kein Dauerkonfigurationsfeld, sondern ein "Auftrag").
ATTR_HISTORY_IMPORT = "history_import"

# OBIS-Code des Zählerstands, den dieser Service korrigiert ("Bezug",
# siehe METER_OBIS_SEPARATOR/coordinator.py für das Schlüssel-Format, in
# dem dieser Code in coordinator.data auftaucht).
TARGET_OBIS = "1-0:1.8.0"
