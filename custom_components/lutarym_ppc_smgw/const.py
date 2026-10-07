# Integrationsversion: 2.9.0
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

DEFAULT_SCAN_INTERVAL_SECONDS = 86400  # 24 Stunden (empfohlen, schont die HAN Schnittstelle).

# Schlüssel für das in entry.options gespeicherte, nutzerkonfigurierbare
# Poll-Intervall in Sekunden. Fehlt der Schlüssel (Entries von vor dieser
# Version), gilt DEFAULT_SCAN_INTERVAL_SECONDS. Untergrenze bewusst bei 5
# Minuten, um das Gateway (Embedded-Webserver, nur eine aktive Session)
# nicht durch zu häufige Login-Zyklen zu belasten.
CONF_SCAN_INTERVAL = "scan_interval"
MIN_SCAN_INTERVAL_SECONDS = 300

# Optionaler zweiter HAN-Login ausschließlich für OBIS 2.8.0 (Einspeisung),
# z.B. wenn der Netzbetreiber Bezug und Einspeisung als getrennte Zugänge
# (andere Kostenstelle) bereitstellt. Beide Schlüssel liegen in entry.data;
# fehlen sie (oder sind leer), wird der Schritt als "übersprungen" gewertet
# und alle Werte kommen wie bisher über den ersten Login.
CONF_USERNAME_EXPORT = "username_export"
CONF_PASSWORD_EXPORT = "password_export"

MANUFACTURER = "Power Plus Communications AG"
MODEL = "LTE Smart Meter Gateway"

# Fester Pfad der HAN-CGI-Schnittstelle auf dem Gateway. Wird mit
# "https://" + Host zur vollen URL zusammengesetzt (siehe api.py).
HAN_PATH = "/cgi-bin/hanservice.cgi"

# WICHTIG: Muss manuell synchron zur "version" in manifest.json gehalten werden.
# Wird im Config-Flow-Dialog und als Geräte-Softwareversion angezeigt, damit
# man die installierte Version prüfen kann, auch bevor eine Verbindung
# erfolgreich zustande kommt.
VERSION = "2.9.0"

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
# Ob die vorhandene Langzeit-Statistik vor dem CSV-Import gelöscht wird.
ATTR_CLEAR_EXISTING = "clear_existing"
# 1:1-CSV-Import (travenetz_import.py) - Pfad zu einer TraveNetz-
# Exportdatei auf dem HA-Host (z.B. /config/imsys_export.csv). Hat
# Vorrang vor dem skalierten Quell-Entity-Modus, falls beides angegeben ist.
ATTR_CSV_PATH = "csv_path"
# Feldname des FileSelector im Einrichtungsassistenten (config_flow.py) -
# der Wert ist eine temporäre Upload-ID, kein Pfad; wird dort sofort in
# einen dauerhaften csv_path (siehe oben) umgewandelt.
ATTR_CSV_UPLOAD = "csv_upload"

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
# OBIS-Code der Einspeisung. Der Historien-Import kann auch auf diesen
# Sensor angewendet werden (Service-Feld "obis" bzw. zweites Upload-Feld
# im Einrichtungsassistenten).
TARGET_OBIS_EXPORT = "1-0:2.8.0"
ATTR_OBIS = "obis"
# Einrichtungsassistent: zweiter, getrennter Historien-Auftrag für 2.8.0
# (gleiche Struktur wie ATTR_HISTORY_IMPORT, wird ebenfalls einmalig
# verarbeitet und danach aus entry.data entfernt).
ATTR_HISTORY_IMPORT_EXPORT = "history_import_export"
ATTR_CSV_UPLOAD_EXPORT = "csv_upload_export"
ATTR_START_VALUE_EXPORT = "start_value_export"
