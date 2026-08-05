# Integrationsversion: 2.4.5
"""PPC Smart Meter Gateway (iMSys) Integration für Home Assistant.

Einstiegspunkt der Integration (von Home Assistant automatisch anhand des
Domain-Namens gefunden). Verantwortlich für: Erzeugen des HTTP-Clients
und des API-Clients, Erzeugen und ersten Abruf des DataUpdateCoordinator,
Weiterreichen an die Plattformen (sensor.py, button.py), Registrieren des
Service `lutarym_ppc_smgw.import_history` (siehe history_import.py),
sowie sauberes Auf-/Abräumen beim Hinzufügen/Entfernen der Integration.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.httpx_client import create_async_httpx_client

from .api import PPCSmgwClient
from .const import (
    ATTR_CSV_PATH,
    ATTR_DRY_RUN,
    ATTR_HISTORY_IMPORT,
    ATTR_MONTHLY_KWH,
    ATTR_RAMP_END,
    ATTR_RAMP_START,
    ATTR_SINCE,
    ATTR_SOURCE_ENTITY,
    ATTR_START_DATE,
    ATTR_START_VALUE,
    ATTR_TARGET_ENTITY,
    CONF_METER_IDS,
    CONF_TARIFF_IDS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    SERVICE_IMPORT_HISTORY,
    SERVICE_REPAIR_ERRONEOUS_RAMP,
    SERVICE_REPAIR_STATISTICS_RESET,
    TARGET_OBIS,
)
from .coordinator import METER_OBIS_SEPARATOR, PPCSmgwCoordinator
from .history_import import HistoryImportError, import_history
from .repair_statistics import repair_erroneous_ramp, repair_statistics_reset
from .travenetz_import import import_csv_history

_LOGGER = logging.getLogger(__name__)

# Von dieser Integration bereitgestellte Entitäts-Plattformen. Wird sowohl
# beim Einrichten (async_forward_entry_setups) als auch beim Entfernen
# (async_unload_platforms) verwendet.
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]

IMPORT_HISTORY_SCHEMA = vol.Schema(
    {
        # Modus 1 (Vorrang): 1:1-CSV-Import, siehe travenetz_import.py.
        vol.Optional(ATTR_CSV_PATH): cv.string,
        # Modus 2 (Fallback, falls kein csv_path): skalierte Quell-Entity,
        # siehe history_import.py. start_date/source_entity sind für
        # DIESEN Modus zwingend, aber auf Schema-Ebene optional, damit
        # Modus 1 ohne sie auskommt - die Prüfung passiert im Handler.
        vol.Optional(ATTR_START_DATE): cv.date,
        vol.Optional(ATTR_START_VALUE): vol.Coerce(float),
        vol.Optional(ATTR_SOURCE_ENTITY): cv.entity_id,
        vol.Optional(ATTR_MONTHLY_KWH): {cv.string: vol.Coerce(float)},
        vol.Optional(ATTR_TARGET_ENTITY): cv.entity_id,
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)

REPAIR_STATISTICS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SINCE): cv.datetime,
        vol.Optional(ATTR_TARGET_ENTITY): cv.entity_id,
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)

REPAIR_RAMP_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_RAMP_START): cv.datetime,
        vol.Required(ATTR_RAMP_END): cv.datetime,
        vol.Optional(ATTR_TARGET_ENTITY): cv.entity_id,
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)

# Diese Integration unterstützt KEINE Konfiguration über configuration.yaml
# (nur über den Einrichtungsassistenten/Config Entries) - async_setup wird
# unten trotzdem implementiert, aber nur um domain-weite Services zu
# registrieren. hassfest verlangt in diesem Fall trotzdem ein explizites
# CONFIG_SCHEMA, das klarstellt, dass keine YAML-Konfiguration erwartet wird.
CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Läuft genau EINMAL beim Start, unabhängig davon wie viele Gateways

    (Config Entries) konfiguriert sind - der richtige Ort, um einen
    domain-weiten Service EINMAL zu registrieren (nicht pro Entry, das
    würde bei mehreren Gateways zu einem Registrierungs-Konflikt führen).
    """

    # WICHTIG: echte "async def"-Wrapper statt lambda! Eine lambda, die
    # einen async-Aufruf zurückgibt (lambda call: _async_handle(...)),
    # ist selbst KEINE Coroutine-Funktion (asyncio.iscoroutinefunction
    # liefert False dafür) - Home Assistants Service-Aufruf-Mechanismus
    # erkennt sie dann fälschlich als synchronen Handler, ruft sie auf und
    # bekommt ein rohes, nie awaitetes Coroutine-Objekt zurück statt des
    # tatsächlichen Ergebnis-Dicts. Symptom: "Es wurde ein Dictionary
    # erwartet, aber es wurde <class 'coroutine'> erhalten."
    async def _handle_import_history(call: ServiceCall) -> ServiceResponse:
        return await _async_handle_import_history(hass, call)

    async def _handle_repair_statistics_reset(call: ServiceCall) -> ServiceResponse:
        return await _async_handle_repair_statistics_reset(hass, call)

    async def _handle_repair_erroneous_ramp(call: ServiceCall) -> ServiceResponse:
        return await _async_handle_repair_erroneous_ramp(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_HISTORY,
        _handle_import_history,
        schema=IMPORT_HISTORY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REPAIR_STATISTICS_RESET,
        _handle_repair_statistics_reset,
        schema=REPAIR_STATISTICS_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REPAIR_ERRONEOUS_RAMP,
        _handle_repair_erroneous_ramp,
        schema=REPAIR_RAMP_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Richtet einen Config Entry (= ein Gateway/Gerät) ein.

    WICHTIG (seit Version 0.13.0): Nutzt `httpx` statt `aiohttp` als
    HTTP-Client, über Home Assistants `create_async_httpx_client`-Hilfe.
    Diese liefert eine DEDIZIERTE (nicht die geteilte) httpx-Instanz pro
    Aufruf - mit eigenem, automatischem Cookie-Jar, den der Client in
    api.py bewusst nutzt (siehe dortiger Moduldocstring für die Begründung
    des Wechsels von aiohttp zu httpx).
    """
    # verify_ssl=False: das Gateway nutzt ein selbstsigniertes Zertifikat,
    # eine echte CA-Prüfung würde hier immer fehlschlagen.
    httpx_client = create_async_httpx_client(hass, verify_ssl=False)

    client = PPCSmgwClient(
        httpx_client,
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )

    # None = "noch nichts ausgewählt" bzw. "alle verfügbaren abrufen" -
    # siehe jeweilige Docstrings in coordinator.py, wie None von einer
    # leeren Liste (explizit nichts) unterschieden wird.
    meter_ids = entry.options.get(CONF_METER_IDS)
    tariff_ids = entry.options.get(CONF_TARIFF_IDS)
    # Nutzerkonfigurierbares Poll-Intervall (Options-Flow); fällt auf den
    # bisherigen festen Wert zurück, falls noch nicht gesetzt.
    scan_interval_seconds = entry.options.get(
        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS
    )
    coordinator = PPCSmgwCoordinator(
        hass,
        client,
        meter_ids,
        tariff_ids,
        timedelta(seconds=scan_interval_seconds),
        entry=entry,
    )
    # Erster Abruf synchron beim Setup - schlägt er fehl, bricht das
    # Setup des Config Entry mit einer aussagekräftigen Fehlermeldung ab,
    # statt eine Integration mit leeren/fehlenden Entitäten anzulegen.
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Sorgt dafür, dass eine Änderung der Optionen (Zähler-/
    # Auswertungsprofil-Auswahl über den Options-Flow) die Integration
    # automatisch neu lädt, damit die neue Auswahl sofort greift.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Als Task NACH dem Setup einplanen (nicht hier awaiten!): die
    # Verarbeitung aktualisiert am Ende entry.data, was den
    # Update-Listener (Zeile oben) auslöst und die Integration neu lädt -
    # das darf erst passieren, wenn dieses async_setup_entry bereits
    # vollständig durchgelaufen und zurückgekehrt ist, sonst käme es zu
    # einem Reload mitten in einem noch laufenden Setup.
    if entry.data.get(ATTR_HISTORY_IMPORT):
        hass.async_create_task(
            _async_process_pending_history_import(hass, entry, coordinator)
        )

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Lädt die Integration neu, wenn sich die Optionen (Zählerauswahl) ändern."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_process_pending_history_import(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: PPCSmgwCoordinator
) -> None:
    """Verarbeitet einen einmaligen Historien-Import-Auftrag aus dem

    Einrichtungsassistenten (siehe config_flow.py:async_step_history) -
    nur beim ALLERERSTEN Laden nach der Einrichtung relevant (entry.data
    enthält den Auftrag nur dann). Entfernt ihn danach IMMER wieder aus
    entry.data, egal ob erfolgreich oder nicht - ein fehlgeschlagener
    Import soll nicht bei jedem Neustart erneut versucht werden. Das
    Ergebnis (Erfolg wie Misserfolg) wird per persistenter Benachrichtigung
    UND im Log sichtbar gemacht, da an dieser Stelle keine interaktive
    Formular-Rückmeldung mehr möglich ist.
    """
    payload = entry.data.get(ATTR_HISTORY_IMPORT)
    if not payload:
        _LOGGER.debug(
            "SMGW Historien-Import: kein Auftrag in entry.data gefunden - "
            "Einrichtungsassistent wurde ohne CSV-Pfad/Startdatum abgeschlossen, "
            "nichts zu tun."
        )
        return
    _LOGGER.info("SMGW Historien-Import: Auftrag gefunden, Modus=%s", payload.get("mode", "scaled"))

    registry = er.async_get(hass)
    target_entity: str | None = None
    for key, reading in coordinator.data.items():
        if METER_OBIS_SEPARATOR not in key:
            continue
        if reading.get("obis") != TARGET_OBIS:
            continue
        unique_id = f"{entry.entry_id}_{key}"
        target_entity = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        if target_entity:
            break

    if target_entity is None:
        _LOGGER.error(
            "SMGW Historien-Import: konnte die Ziel-Entity (OBIS %s) nach der "
            "Einrichtung nicht finden - Import wird übersprungen.",
            TARGET_OBIS,
        )
        message = (
            f"Historien-Import konnte nicht durchgeführt werden: keine Entity "
            f"für OBIS {TARGET_OBIS} gefunden."
        )
    else:
        try:
            entity_entry = registry.async_get(target_entity)
            target_name = (
                (entity_entry.name or entity_entry.original_name) if entity_entry else None
            ) or target_entity

            mode = payload.get("mode", "scaled")  # aeltere Auftraege (vor CSV-Support) hatten kein "mode"-Feld
            if mode == "csv":
                # Live-Wert HIER neu abrufen (nicht den evtl. veralteten aus
                # dem Auftrag) - überbrückt die Zeit zwischen dem letzten
                # CSV-Punkt und jetzt UND überschreibt dabei automatisch
                # etwaige alte/überholte Statistik-Reste in diesem Fenster
                # (siehe import_csv_history: extend_to_now_value_kwh).
                await coordinator.async_request_refresh()
                extend_value: float | None = None
                fresh_state = hass.states.get(target_entity)
                if fresh_state is not None and fresh_state.state not in (
                    None,
                    "unknown",
                    "unavailable",
                ):
                    try:
                        raw_value = float(str(fresh_state.state).replace(",", "."))
                        unit = (
                            fresh_state.attributes.get("unit_of_measurement") or ""
                        ).strip().lower()
                        extend_value = raw_value / 1000 if unit == "wh" else raw_value
                    except ValueError:
                        _LOGGER.warning(
                            "SMGW Historien-Import: aktueller Zustand von '%s' "
                            "('%s') ist keine Zahl - Brücke bis 'jetzt' wird "
                            "übersprungen.",
                            target_entity,
                            fresh_state.state,
                        )

                # Rückwärts-Modus (bevorzugt): der frisch abgerufene
                # Live-Wert (extend_value) wird als ANKER auf die letzte
                # CSV-Zeile gelegt und von dort rückwärts gerechnet - der
                # Startwert ergibt sich daraus. Robuster, weil der aktuelle
                # Zählerstand die verlässliche Größe ist, nicht der
                # (oft unsichere) Aufzeichnungsbeginn. Fällt der Live-Wert
                # aus (extend_value is None), greift der bisherige
                # Vorwärts-Modus mit vorgegebenem Startwert als Fallback.
                if extend_value is not None:
                    summary = await import_csv_history(
                        hass,
                        target_statistic_id=target_entity,
                        target_name=target_name,
                        csv_path=payload["csv_path"],
                        anchor_end_value_kwh=extend_value,
                        dry_run=False,
                    )
                else:
                    summary = await import_csv_history(
                        hass,
                        target_statistic_id=target_entity,
                        target_name=target_name,
                        csv_path=payload["csv_path"],
                        start_value_kwh=float(payload.get("start_value") or 0.0),
                        dry_run=False,
                    )
                breakdown = ", ".join(
                    f"{k}: {v:.1f} kWh" for k, v in summary["monthly_breakdown_kwh"].items()
                )
                # Plausibilitäts-Referenz für den nächsten Live-Poll setzen
                # (verhindert, dass ein Ausreißer nahe 0 die gerade
                # importierte Statistik als vermeintlichen Zähler-Reset
                # zerstört - siehe coordinator.seed_last_good_value).
                seed_val = summary.get("final_computed_kwh")
                if seed_val is not None:
                    coordinator.seed_last_good_value(target_entity, float(seed_val))
                if summary.get("mode") == "backward_from_anchor":
                    bridge_note = (
                        f" (rückwärts gerechnet ab Live-Anker "
                        f"{summary['anchor_end_value_kwh']:.2f} kWh auf der letzten CSV-Zeile)"
                    )
                elif summary.get("bridge_skipped_reason"):
                    bridge_note = f" (⚠️ Brücke NICHT geschrieben: {summary['bridge_skipped_reason']})"
                elif summary.get("bridged_hours_to_now"):
                    bridge_note = f" (+ {summary['bridged_hours_to_now']} Std. bis jetzt überbrückt)"
                else:
                    bridge_note = " (Vorwärts-Modus, kein Live-Anker verfügbar)"
                message = (
                    f"1:1-CSV-Import für {target_entity} abgeschlossen "
                    f"({summary['csv_path']}).\n\n"
                    f"Zeitraum: {summary['first_timestamp']} bis "
                    f"{summary['last_timestamp']}{bridge_note}\n"
                    f"Start: {summary['start_value_kwh']} kWh\n"
                    f"Ende: {summary['final_computed_kwh']} kWh\n"
                    f"{summary['hourly_points']} Stundenwerte geschrieben.\n\n"
                    f"Monatsaufteilung: {breakdown}"
                )
            else:
                end_value = float(payload["end_value"])
                unit = (payload.get("end_unit") or "").strip().lower()
                if unit == "wh":
                    end_value = end_value / 1000

                summary = await import_history(
                    hass,
                    target_statistic_id=target_entity,
                    target_name=target_name,
                    start_date=date.fromisoformat(payload["start_date"]),
                    start_value_kwh=float(payload["start_value"]),
                    source_entity_id=payload["source_entity"],
                    end_value_kwh=end_value,
                    monthly_kwh=payload.get("monthly_kwh") or {},
                    dry_run=False,
                )
                breakdown = ", ".join(
                    f"{k}: {v:.1f} kWh" for k, v in summary["monthly_breakdown_kwh"].items()
                )
                message = (
                    f"Historien-Import für {target_entity} abgeschlossen.\n\n"
                    f"Start: {summary['start_value_kwh']} kWh am {summary['start_date']}\n"
                    f"Ende: {summary['final_computed_kwh']} kWh "
                    f"(Ziel: {summary['end_value_kwh']} kWh)\n"
                    f"{summary['hourly_points']} Stundenwerte geschrieben.\n\n"
                    f"Monatsaufteilung: {breakdown}"
                )
            _LOGGER.info("SMGW Historien-Import erfolgreich: %s", summary)
        except HistoryImportError as err:
            _LOGGER.error("SMGW Historien-Import fehlgeschlagen: %s", err)
            message = f"Historien-Import fehlgeschlagen: {err}"
        except Exception:  # noqa: BLE001 - darf das Setup der Integration nicht mitreissen
            _LOGGER.exception("SMGW Historien-Import: unerwarteter Fehler")
            message = (
                "Historien-Import mit einem unerwarteten Fehler abgebrochen - "
                "siehe Protokoll für Details."
            )

    persistent_notification.async_create(
        hass,
        message,
        title="PPC SMGW Historien-Import",
        notification_id=f"{DOMAIN}_history_import_{entry.entry_id}",
    )

    # Auftrag entfernen, damit er nicht bei jedem Neustart erneut läuft
    # (löst den Update-Listener aus -> ein einmaliger, harmloser Reload).
    new_data = dict(entry.data)
    new_data.pop(ATTR_HISTORY_IMPORT, None)
    hass.config_entries.async_update_entry(entry, data=new_data)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Entfernt einen Config Entry wieder.

    Die httpx-Client-Instanz von create_async_httpx_client wird von Home
    Assistant selbst verwaltet/geschlossen - hier ist kein manuelles
    Schließen nötig (anders als bei der vorherigen aiohttp-Variante mit
    selbst erzeugter Session).

    Der Service `import_history` wird bewusst NICHT hier abgemeldet: ein
    Reload (unload+setup DESSELBEN Entry, z.B. ausgelöst durch den
    Update-Listener nach _async_process_pending_history_import) trifft
    kurzzeitig genau die "letzter Entry entfernt"-Bedingung, obwohl der
    Entry gar nicht wirklich verschwindet - würde man den Service dabei
    abmelden, käme er nie wieder zurück, da async_setup (wo er registriert
    wird) nur EINMAL beim echten HA-Start läuft, nicht bei jedem Reload.
    Der Service bleibt daher für die Laufzeit von Home Assistant bestehen,
    auch nachdem das letzte Gateway entfernt wurde (schadet nicht - der
    Handler meldet einen klaren Fehler, wenn kein Gateway mehr da ist).
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


def _find_target_1_8_0_entity(hass: HomeAssistant, registry: er.EntityRegistry) -> str:
    """Sucht über ALLE geladenen Gateways hinweg die Entity für

    OBIS 1-0:1.8.0 ("Bezug") - siehe const.TARGET_OBIS. Wird nur genutzt,
    wenn der Service ohne explizites `target_entity` aufgerufen wird.
    """
    matches: list[str] = []
    for entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
        for key, reading in coordinator.data.items():
            if METER_OBIS_SEPARATOR not in key:
                continue
            if reading.get("obis") != TARGET_OBIS:
                continue
            unique_id = f"{entry_id}_{key}"
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
            if entity_id:
                matches.append(entity_id)

    if not matches:
        raise HistoryImportError(
            f"Keine Entity mit OBIS {TARGET_OBIS} gefunden - bitte 'target_entity' "
            "im Service-Aufruf explizit angeben."
        )
    if len(matches) > 1:
        raise HistoryImportError(
            f"Mehrere Entities mit OBIS {TARGET_OBIS} gefunden ({', '.join(matches)}) "
            "- bitte 'target_entity' im Service-Aufruf explizit angeben."
        )
    return matches[0]


def _coordinator_for_entity(
    hass: HomeAssistant, registry: er.EntityRegistry, entity_id: str
) -> PPCSmgwCoordinator | None:
    """Findet den Coordinator, dem eine Entity gehört (für einen frischen

    Refresh vor dem Auslesen des Endwerts) - None, falls die Entity nicht
    zu einem geladenen Gateway dieser Integration gehört (z.B. wenn
    `target_entity` bewusst auf eine fremde Entity zeigt).
    """
    entity_entry = registry.async_get(entity_id)
    if entity_entry is None or entity_entry.config_entry_id is None:
        return None
    return hass.data.get(DOMAIN, {}).get(entity_entry.config_entry_id)


async def _async_handle_import_history(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Handler für den Service `lutarym_ppc_smgw.import_history`.

    Ermittelt die Ziel-Entity (explizit angegeben oder automatisch über
    OBIS 1-0:1.8.0 gefunden). Zwei Modi:
      - csv_path angegeben -> 1:1-Import (travenetz_import.py), kein
        Live-Endwert nötig, da die CSV ihren Endpunkt selbst mitbringt.
      - sonst: start_date + source_entity müssen gesetzt sein -> skalierte
        Quell-Entity über zwei Ankerpunkte (history_import.py), aktueller
        Live-Wert wird als automatischer Endanker frisch abgerufen.
    """
    registry = er.async_get(hass)
    target_entity = call.data.get(ATTR_TARGET_ENTITY) or _find_target_1_8_0_entity(
        hass, registry
    )

    coordinator = _coordinator_for_entity(hass, registry, target_entity)
    if coordinator is not None:
        await coordinator.async_request_refresh()

    state = hass.states.get(target_entity)
    entity_entry = registry.async_get(target_entity)
    friendly_name = (
        (state.attributes.get("friendly_name") if state else None)
        or (entity_entry.name or entity_entry.original_name if entity_entry else None)
        or target_entity
    )

    csv_path = call.data.get(ATTR_CSV_PATH)
    if csv_path:
        extend_value: float | None = None
        if state is not None and state.state not in (None, "unknown", "unavailable"):
            try:
                raw_value = float(str(state.state).replace(",", "."))
                unit = (state.attributes.get("unit_of_measurement") or "").strip().lower()
                extend_value = raw_value / 1000 if unit == "wh" else raw_value
            except ValueError:
                _LOGGER.warning(
                    "SMGW Historien-Import: aktueller Zustand von '%s' ('%s') ist "
                    "keine Zahl - Brücke bis 'jetzt' wird übersprungen.",
                    target_entity,
                    state.state,
                )

        # Rückwärts-Modus bevorzugt: Live-Wert als Anker auf letzte
        # CSV-Zeile (siehe ausführliche Begründung beim Config-Flow-
        # Aufrufer oben). Fallback auf Vorwärts, wenn kein Live-Wert da.
        if extend_value is not None:
            summary = await import_csv_history(
                hass,
                target_statistic_id=target_entity,
                target_name=friendly_name,
                csv_path=csv_path,
                anchor_end_value_kwh=extend_value,
                dry_run=call.data[ATTR_DRY_RUN],
            )
        else:
            summary = await import_csv_history(
                hass,
                target_statistic_id=target_entity,
                target_name=friendly_name,
                csv_path=csv_path,
                start_value_kwh=call.data.get(ATTR_START_VALUE) or 0.0,
                dry_run=call.data[ATTR_DRY_RUN],
            )
        summary["target_entity"] = target_entity
        # Plausibilitäts-Referenz nach echtem (nicht dry-run) Import setzen.
        if (
            not call.data[ATTR_DRY_RUN]
            and coordinator is not None
            and summary.get("final_computed_kwh") is not None
        ):
            coordinator.seed_last_good_value(
                target_entity, float(summary["final_computed_kwh"])
            )
        return summary

    if not call.data.get(ATTR_START_DATE) or not call.data.get(ATTR_SOURCE_ENTITY):
        raise HistoryImportError(
            "Entweder 'csv_path' (1:1-Import) ODER 'start_date' zusammen mit "
            "'source_entity' (skalierter Import) müssen angegeben werden."
        )

    if state is None or state.state in (None, "unknown", "unavailable"):
        raise HistoryImportError(
            f"Ziel-Entity '{target_entity}' hat aktuell keinen gültigen Zustand - "
            "Import abgebrochen."
        )
    try:
        raw_value = float(str(state.state).replace(",", "."))
    except ValueError as err:
        raise HistoryImportError(
            f"Zustand von '{target_entity}' ('{state.state}') ist keine Zahl."
        ) from err

    unit = (state.attributes.get("unit_of_measurement") or "").strip().lower()
    if unit == "wh":
        end_value_kwh = raw_value / 1000
    elif unit in ("kwh", ""):
        end_value_kwh = raw_value
    else:
        _LOGGER.warning(
            "SMGW Historien-Import: unbekannte Einheit '%s' an '%s' - Wert wird "
            "unverändert als kWh interpretiert.",
            state.attributes.get("unit_of_measurement"),
            target_entity,
        )
        end_value_kwh = raw_value

    summary = await import_history(
        hass,
        target_statistic_id=target_entity,
        target_name=friendly_name,
        start_date=call.data[ATTR_START_DATE],
        start_value_kwh=call.data.get(ATTR_START_VALUE) or 0.0,
        source_entity_id=call.data[ATTR_SOURCE_ENTITY],
        end_value_kwh=end_value_kwh,
        monthly_kwh=call.data.get(ATTR_MONTHLY_KWH),
        dry_run=call.data[ATTR_DRY_RUN],
    )
    summary["target_entity"] = target_entity
    return summary


async def _async_handle_repair_statistics_reset(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    """Handler für den Service `lutarym_ppc_smgw.repair_statistics_reset`.

    Korrigiert einen Statistik-Reset (sum auf 0 gefallen, state aber
    korrekt weitergelaufen) EXAKT - keine Schätzung, nur Verschiebung der
    bereits vorhandenen Werte um den richtigen Offset (siehe
    repair_statistics.py). Empfehlung an den Nutzer: Recorder vor dem
    Ausführen pausieren (Aktion "Recorder: Deaktivieren"), danach wieder
    aktivieren - steht auch in der Service-Beschreibung (strings.json).
    """
    registry = er.async_get(hass)
    target_entity = call.data.get(ATTR_TARGET_ENTITY) or _find_target_1_8_0_entity(
        hass, registry
    )

    entity_entry = registry.async_get(target_entity)
    state = hass.states.get(target_entity)
    friendly_name = (
        (state.attributes.get("friendly_name") if state else None)
        or (entity_entry.name or entity_entry.original_name if entity_entry else None)
        or target_entity
    )

    summary = await repair_statistics_reset(
        hass,
        target_statistic_id=target_entity,
        target_name=friendly_name,
        since=call.data[ATTR_SINCE],
        dry_run=call.data[ATTR_DRY_RUN],
    )
    summary["target_entity"] = target_entity
    return summary


async def _async_handle_repair_erroneous_ramp(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    """Handler für den Service `lutarym_ppc_smgw.repair_erroneous_ramp`.

    Entfernt einen fälschlich eingefügten linearen Anstieg (Gegenstück zu
    repair_statistics_reset - siehe repair_statistics.py für Details).
    """
    registry = er.async_get(hass)
    target_entity = call.data.get(ATTR_TARGET_ENTITY) or _find_target_1_8_0_entity(
        hass, registry
    )

    entity_entry = registry.async_get(target_entity)
    state = hass.states.get(target_entity)
    friendly_name = (
        (state.attributes.get("friendly_name") if state else None)
        or (entity_entry.name or entity_entry.original_name if entity_entry else None)
        or target_entity
    )

    summary = await repair_erroneous_ramp(
        hass,
        target_statistic_id=target_entity,
        target_name=friendly_name,
        ramp_start=call.data[ATTR_RAMP_START],
        ramp_end=call.data[ATTR_RAMP_END],
        dry_run=call.data[ATTR_DRY_RUN],
    )
    summary["target_entity"] = target_entity
    return summary
