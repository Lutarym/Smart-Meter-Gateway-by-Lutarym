# Integrationsversion: 2.4.4
"""Sensor-Plattform für die PPC Smart Meter Gateway Integration.

Jedes einzelne von der API gelieferte Feld wird als EIGENE Entität
angelegt (nicht nur als Attribut einer anderen Entität) - so sind alle
Werte direkt im Dashboard, in Automationen und in der Entitätenliste
nutzbar. Entitätsnamen werden dabei bewusst KURZ gehalten (kein voller
technischer Bezeichner als Präfix), abgeleitet aus OBIS-Code (Zähler) bzw.
erkennbaren Namensbestandteilen (Auswertungsprofil).
"""

from __future__ import annotations

import logging
import re

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, VERSION
from .coordinator import (
    METER_OBIS_SEPARATOR,
    TARIFF_KEY_PREFIX,
    PPCSmgwCoordinator,
    build_device_info,
    gateway_gueltig_ab,
    gateway_obis_status,
)

_LOGGER = logging.getLogger(__name__)

# Bekannte Namensbestandteile in PPC-Auswertungsprofilnamen (z.B.
# "IM4G_TAF07_BEZUG_15MI_PROD_01"), die zu einem kurzen, lesbaren Label
# zusammengesetzt werden. Unbekannte Profilnamen fallen auf eine gekürzte
# Rohform zurück.
_TARIFF_NAME_TOKENS = {
    "BEZUG": "Bezug",
    "EINSPEISUNG": "Einspeisung",
    "VERBRAUCH": "Verbrauch",
    "ERZEUGUNG": "Erzeugung",
    "15MI": "15-Min",
    "MON": "Monat",
    "TAG": "Tag",
    "JAHR": "Jahr",
    "WOCHE": "Woche",
}

# Zusätzliche Felder, die als EIGENE Diagnose-Entität je Zähler/OBIS
# angelegt werden (data-Key, kurzer Anzeigename - Begriffe direkt aus dem
# PPC-Handbuch Kapitel 4.3 übernommen).
METER_EXTRA_FIELDS: list[tuple[str, str]] = [
    ("zaehler_id", "Zähler-ID"),
    ("zaehler_name", "Name"),
    ("beschreibung", "Beschreibung"),
    ("kommunikationstyp", "Kommunikationstyp"),
    ("protokoll_typ", "Protokoll-Typ"),
    ("protokoll_version", "Protokoll-Version"),
    ("ausleseintervall_sekunden", "Ausleseintervall"),
    ("abfrageversuche", "Abfrageversuche"),
    ("zaehleradresse", "Zähleradresse"),
    ("medium", "Medium"),
    ("timestamp", "Zeitstempel"),
    ("isvalid", "Ist valide"),
    ("signiert", "Signiert (eichrechtskonformer Ablesewert)"),
]

# Zusätzliche Felder je Auswertungsprofil (data-Key, kurzer Anzeigename -
# Begriffe direkt aus dem PPC-Handbuch Kapitel 4.5 übernommen).
TARIFF_EXTRA_FIELDS: list[tuple[str, str]] = [
    ("profil_id", "Profil-ID"),
    ("taf_typ", "TAF-Typ"),
    ("obis", "OBIS"),
    ("messgroesse", "Messgröße"),
    ("registerperiode_sekunden", "Registerperiode"),
    ("abrechnungsperiode_sekunden", "Abrechnungsperiode"),
    ("vorhaltezeit_tage", "Vorhaltezeit"),
    ("beginn_validierungsperiode", "Beginn Gültigkeit"),
    ("ende_validierungsperiode", "Ende Gültigkeit"),
    ("abgelaufen", "Abgelaufen"),
    ("alias", "Alias"),
    ("zaehlpunktbezeichnung", "Zählpunkt"),
    ("tarifstufen", "Tarifstufen"),
    ("tag_beginn", "Tag Beginn"),
]


def _short_label(coordinator: PPCSmgwCoordinator, key: str, is_tariff: bool) -> str:
    """Leitet ein KURZES, lesbares Kürzel für Entitätsnamen ab.

    Zähler: OBIS-Kurzform, z.B. "1-0:1.8.0" -> "1.8.0".
    Auswertungsprofil: erkennbare Namensbestandteile, z.B.
    "IM4G_TAF07_BEZUG_15MI_PROD_01" -> "Bezug 15-Min". Unbekannte Muster
    fallen auf eine gekürzte Rohform zurück.
    """
    if is_tariff:
        profile_name = key[len(TARIFF_KEY_PREFIX):]
        tokens = re.split(r"[_\-.]", profile_name.upper())
        picked = [_TARIFF_NAME_TOKENS[t] for t in tokens if t in _TARIFF_NAME_TOKENS]
        if picked:
            return " ".join(dict.fromkeys(picked))  # Duplikate entfernen, Reihenfolge behalten
        return profile_name[:20]

    data = coordinator.data.get(key, {})
    obis = data.get("obis")
    if obis:
        return obis.rsplit(":", 1)[-1]  # "1-0:1.8.0" -> "1.8.0"
    meter_label, _, _ = key.partition(METER_OBIS_SEPARATOR)
    return meter_label[:16]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Legt alle Sensor-Entitäten für einen Config Entry an.

    Zwei Gruppen von Entitäten:
    1. Feste, immer vorhandene Gateway-Info-Sensoren (Firmware,
       Integrations-Version, IP-Adresse, Benutzername, OBIS-Aktiv-Status,
       Gültig-ab-Datum) - unabhängig davon, was der erste Datenabruf
       geliefert hat.
    2. Dynamisch, EIN Eintrag pro Schlüssel in `coordinator.data` (also
       pro gefundenem Zähler-OBIS-Code bzw. Auswertungsprofil): ein
       Werte-Sensor (`PPCSmgwValueSensor`) plus je einen Diagnose-Sensor
       pro Zusatzfeld (`PPCSmgwFieldSensor`, siehe METER_EXTRA_FIELDS/
       TARIFF_EXTRA_FIELDS). Wird die Zähler-/Profil-Auswahl später über
       den Options-Flow geändert, lädt Home Assistant die Plattform neu
       und diese Funktion läuft mit den dann aktuellen Daten erneut durch.
    """
    coordinator: PPCSmgwCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        PPCSmgwFirmwareSensor(coordinator, entry),
        PPCSmgwIntegrationVersionSensor(coordinator, entry),
        PPCSmgwIpAddressSensor(coordinator, entry),
        PPCSmgwUsernameSensor(coordinator, entry),
        PPCSmgwObisActiveSensor(coordinator, entry, "1-0:1.8.0", "1.8.0 Aktiv"),
        PPCSmgwObisActiveSensor(coordinator, entry, "1-0:2.8.0", "2.8.0 Aktiv"),
        PPCSmgwGueltigAbSensor(coordinator, entry),
    ]

    for key in coordinator.data:
        is_tariff = key.startswith(TARIFF_KEY_PREFIX)
        entities.append(PPCSmgwValueSensor(coordinator, entry, key, is_tariff))
        fields = TARIFF_EXTRA_FIELDS if is_tariff else METER_EXTRA_FIELDS
        for field_key, label in fields:
            entities.append(
                PPCSmgwFieldSensor(coordinator, entry, key, is_tariff, field_key, label)
            )

    async_add_entities(entities)


class PPCSmgwFirmwareSensor(CoordinatorEntity[PPCSmgwCoordinator], SensorEntity):
    """Eigene Entität für die Gateway-Firmware-Version (einmal pro Gerät)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Firmware"

    def __init__(self, coordinator: PPCSmgwCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_firmware_version"
        self._attr_device_info = build_device_info(coordinator, entry)

    @property
    def native_value(self) -> str | None:
        return self.coordinator.client.firmware_version


class PPCSmgwIntegrationVersionSensor(CoordinatorEntity[PPCSmgwCoordinator], SensorEntity):
    """Eigene Entität für die Version dieser Integration (nicht die des

    Gateways selbst - siehe PPCSmgwFirmwareSensor dafür).
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Integrations-Version"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: PPCSmgwCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_integration_version"
        self._attr_device_info = build_device_info(coordinator, entry)

    @property
    def native_value(self) -> str:
        return VERSION


class PPCSmgwIpAddressSensor(CoordinatorEntity[PPCSmgwCoordinator], SensorEntity):
    """Eigene Entität für die konfigurierte IP-Adresse/Host des Gateways."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "IP-Adresse"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: PPCSmgwCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ip_address"
        self._attr_device_info = build_device_info(coordinator, entry)
        self._host = entry.data[CONF_HOST]

    @property
    def native_value(self) -> str:
        return self._host


class PPCSmgwUsernameSensor(CoordinatorEntity[PPCSmgwCoordinator], SensorEntity):
    """Eigene Entität für den konfigurierten HAN-Benutzernamen (kein

    Passwort - das bleibt ausschließlich in der Config-Entry-Konfiguration
    und wird nirgends als Entität angezeigt).
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Benutzername"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: PPCSmgwCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_username"
        self._attr_device_info = build_device_info(coordinator, entry)
        self._username = entry.data[CONF_USERNAME]

    @property
    def native_value(self) -> str:
        return self._username


class PPCSmgwObisActiveSensor(CoordinatorEntity[PPCSmgwCoordinator], SensorEntity):
    """Zeigt, ob ein bestimmter OBIS-Code (z.B. 1.8.0/2.8.0) im aktuellen

    Datensatz vorhanden ist ("Ja"/"Nein") - unabhängig davon, ob dafür
    gerade eine eigene Werte-Entität existiert (die nur bei vorhandenen
    OBIS-Zeilen angelegt wird).
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: PPCSmgwCoordinator, entry: ConfigEntry, obis: str, name: str
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_obis_active_{obis}"
        self._attr_device_info = build_device_info(coordinator, entry)
        self._obis = obis

    @property
    def native_value(self) -> str:
        return "Ja" if gateway_obis_status(self.coordinator).get(self._obis) else "Nein"


class PPCSmgwGueltigAbSensor(CoordinatorEntity[PPCSmgwCoordinator], SensorEntity):
    """Zeigt, ab wann der aktuelle HAN-Zugang gültig ist (Beginn

    Validierungsperiode des ersten gefundenen Auswertungsprofils).
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Zugang gültig ab"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: PPCSmgwCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_zugang_gueltig_ab"
        self._attr_device_info = build_device_info(coordinator, entry)

    @property
    def native_value(self) -> str | None:
        return gateway_gueltig_ab(self.coordinator)


class PPCSmgwValueSensor(CoordinatorEntity[PPCSmgwCoordinator], SensorEntity):
    """Der eigentliche Messwert (Zähler) bzw. Platzhalter-Entität

    (Auswertungsprofil, hat keinen eigenen Messwert - siehe
    PPCSmgwFieldSensor für dessen Konfigurationsdaten als eigene
    Entitäten).
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PPCSmgwCoordinator,
        entry: ConfigEntry,
        key: str,
        is_tariff: bool,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._is_tariff = is_tariff
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = _short_label(coordinator, key, is_tariff)
        self._attr_device_info = build_device_info(coordinator, entry)

        if is_tariff:
            self._attr_device_class = None
            self._attr_state_class = None
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            # Auswertungsprofil-Werte werden immer mit abgerufen, sind aber
            # für den Alltag selten relevant - daher standardmäßig
            # deaktiviert angelegt und bei Bedarf jederzeit unter
            # Einstellungen -> Entitäten aktivierbar.
            self._attr_entity_registry_enabled_default = False
        else:
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            # Zähler-Messwerte (z.B. 1-0:1.8.0 Bezug, 1-0:2.8.0 Einspeisung)
            # sind die zentralen Werte dieser Integration und bleiben immer
            # aktiv - auch ein aktuell nicht gelieferter Wert (native_value
            # None) bleibt als Entität bestehen, damit er sichtbar ist,
            # sobald das Gateway ihn liefert.
            self._attr_entity_registry_enabled_default = True

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data.get(self._key)
        if not data or data.get("value") is None:
            return None
        try:
            return float(str(data["value"]).replace(",", "."))
        except ValueError:
            return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        data = self.coordinator.data.get(self._key)
        return data.get("unit") if data else None

    # WICHTIG: Kein eigener Statistik-Schreibmechanismus mehr (siehe
    # Integrationsversion vor 1.14.0 fuer die vorherige, selbstschreibende
    # Variante). Home Assistant kompiliert fuer state_class=TOTAL_INCREASING
    # Sensoren die Langzeit-Statistik VOLLSTAENDIG SELBST, inklusive
    # korrekter Reset-Erkennung (ein fallender Rohwert wird automatisch
    # als Zaehler-Reset erkannt, 'sum' laeuft trotzdem korrekt weiter,
    # ohne manuelles Zutun). Der fruehere Selbst-Schreib-Mechanismus war
    # ein Workaround fuer ein Problem, das HA bereits eingebaut loest -
    # er hat stattdessen wiederholt mit HAs eigener Kompilierung um
    # dieselbe (metadata_id, start_ts)-Zeile kollidiert (UNIQUE constraint
    # failed), was die komplette Recorder-Batch-Transaktion inkl. fremder,
    # unbeteiligter Entitaeten im selben Zyklus blockiert hat. Der Sensor
    # liefert jetzt nur noch brav seinen rohen Zaehlerstand ueber
    # native_value - alles Weitere macht HA.


class PPCSmgwFieldSensor(CoordinatorEntity[PPCSmgwCoordinator], SensorEntity):
    """Eine eigene Entität für EIN einzelnes Zusatzfeld

    (z.B. "Zähler-ID", "Abgelaufen", "Ende Gültigkeit", ...) - damit jeder
    Wert einzeln im Dashboard/in Automationen nutzbar ist, statt nur als
    Attribut einer anderen Entität versteckt zu sein. Name = kurzes Kürzel
    + Feldname, z.B. "1.8.0 Zähler-ID" oder "Bezug 15-Min Abgelaufen".
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: PPCSmgwCoordinator,
        entry: ConfigEntry,
        key: str,
        is_tariff: bool,
        field_key: str,
        label: str,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._field_key = field_key
        self._attr_unique_id = f"{entry.entry_id}_{key}_{field_key}"
        short = _short_label(coordinator, key, is_tariff)
        self._attr_name = f"{short} {label}"
        self._attr_device_info = build_device_info(coordinator, entry)
        # Von allen Zusatzfeldern bleibt standardmäßig nur "Zähleradresse"
        # ("Zählernummer") aktiv - alle übrigen (Tarif-Konfigurationsdaten
        # sowie Zähler-Metadaten wie Kommunikationstyp/Protokoll/
        # Ausleseintervall etc.) werden angelegt, aber deaktiviert
        # ausgeliefert und sind bei Bedarf jederzeit unter Einstellungen ->
        # Entitäten aktivierbar.
        self._attr_entity_registry_enabled_default = (
            not is_tariff and field_key == "zaehleradresse"
        )

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data.get(self._key, {})
        value = data.get(self._field_key)
        if isinstance(value, bool):
            return "Ja" if value else "Nein"
        if value is None:
            return None
        return str(value)
