# Integrationsversion: 2.9.0
"""Config Flow für die PPC Smart Meter Gateway (iMSys) Integration."""

from __future__ import annotations

import html
import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
import httpx

from .api import (
    PPCSmgwAuthError,
    PPCSmgwClient,
    PPCSmgwConnectionError,
    PPCSmgwParsingError,
    async_check_host_reachable,
)
from .const import (
    ATTR_CSV_PATH,
    ATTR_CSV_UPLOAD,
    ATTR_CSV_UPLOAD_EXPORT,
    ATTR_HISTORY_IMPORT,
    ATTR_HISTORY_IMPORT_EXPORT,
    ATTR_START_VALUE,
    ATTR_START_VALUE_EXPORT,
    ATTR_CLEAR_EXISTING,
    CONF_PASSWORD_EXPORT,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME_EXPORT,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    CONF_METER_IDS,
    CONF_TARIFF_IDS,
    DOMAIN,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)

# Feldname der Mehrfachauswahl im Reconfigure-Dialog (welche Historie importiert werden soll).
CONF_IMPORT_OBIS = "import_obis"

# Antwortwerte der Frage "Vorhandene Statistik vor dem Import löschen?".
# Lesbare Feldbeschriftungen direkt aus dem Code. Sie dienen als Feldname und
# werden von Home Assistant 1:1 angezeigt, auch wenn die Übersetzungsdateien
# (noch) nicht geladen sind. So ist immer klar, wofür welcher Upload ist.
LABEL_UPLOAD_IMPORT = "CSV Datei für 1.8.0 (Netzbezug, Energie bezogen)"
LABEL_START_IMPORT = "Startwert 1.8.0 in kWh (nur Fallback, optional)"
LABEL_UPLOAD_EXPORT = "CSV Datei für 2.8.0 (Einspeisung, Energie geliefert)"
LABEL_START_EXPORT = "Startwert 2.8.0 in kWh (nur Fallback, optional)"

_FIELD_LABELS = {
    ATTR_CSV_UPLOAD: LABEL_UPLOAD_IMPORT,
    ATTR_START_VALUE: LABEL_START_IMPORT,
    ATTR_CSV_UPLOAD_EXPORT: LABEL_UPLOAD_EXPORT,
    ATTR_START_VALUE_EXPORT: LABEL_START_EXPORT,
}


def _field(user_input: dict[str, Any], key: str) -> Any:
    """Wert eines Import-Felds lesen (lesbarer Feldname oder interner Schlüssel)."""
    value = user_input.get(_FIELD_LABELS[key])
    if value is None:
        value = user_input.get(key)
    return value


# Auswahl des Abrufintervalls (Minuten). Die Beschriftungen kommen direkt aus
# dem Code und sind daher immer lesbar.
_INTERVAL_CHOICES_MIN = (
    (5, "alle 5 Minuten"),
    (15, "alle 15 Minuten"),
    (30, "alle 30 Minuten"),
    (60, "jede Stunde"),
    (720, "alle 12 Stunden"),
    (1440, "alle 24 Stunden"),
)


def _interval_selector(current_minutes: int) -> selector.SelectSelector:
    """Auswahlfeld für das Abrufintervall (ein bisher gesetzter Sonderwert bleibt wählbar)."""
    choices = list(_INTERVAL_CHOICES_MIN)
    if current_minutes not in [m for m, _ in choices]:
        choices.append((current_minutes, f"alle {current_minutes} Minuten (bisheriger Wert)"))
        choices.sort()
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value=str(m), label=label)
                for m, label in choices
            ],
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _current_interval_minutes(entry: Any) -> int:
    return int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS)) // 60


_CLEAR_YES = "yes"
_CLEAR_NO = "no"


def _clear_question_schema() -> vol.Schema:
    """Auswahlfeld Ja/Nein. Die Texte kommen direkt aus dem Code, daher ist
    die Frage auch ohne Übersetzungsdatei eindeutig lesbar."""
    return vol.Schema(
        {
            vol.Required(ATTR_CLEAR_EXISTING, default=_CLEAR_NO): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=_CLEAR_NO,
                            label="Nein, vorhandene Werte nur überschreiben",
                        ),
                        selector.SelectOptionDict(
                            value=_CLEAR_YES,
                            label="Ja, ALLE vorhandenen Werte dieses Sensors vorher löschen (nicht rückgängig zu machen)",
                        ),
                    ],
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        }
    )


async def _async_test_export_login(
    host: str, username: str, password: str
) -> tuple[str | None, str, int]:
    """Testet den zweiten Login (2.8.0) mit einer EIGENEN, kurzen Session.

    Gibt (Fehlerschlüssel | None, Debug-Info, Anzahl gefundener Zähler)
    zurück. Der Fehlerschlüssel ist einer von "invalid_auth",
    "parsing_error", "cannot_connect", "no_meters_found"; der Aufrufer
    stellt ihm "export_" voran, damit die Fehlermeldung klar auf den
    zweiten Login verweist. Meldet sich immer wieder ab (das Gateway
    erlaubt nur eine Session gleichzeitig), darf daher erst aufgerufen
    werden, wenn die Session des ersten Logins bereits beendet ist.
    """
    httpx_client = httpx.AsyncClient(verify=False)
    client = PPCSmgwClient(httpx_client, host, username, password)
    token: str | None = None
    try:
        token = await client.login()
        meters = await client.list_meters(token)
    except PPCSmgwAuthError as err:
        return "invalid_auth", html.escape(err.details), 0
    except PPCSmgwParsingError as err:
        return "parsing_error", html.escape(err.details), 0
    except PPCSmgwConnectionError as err:
        return "cannot_connect", html.escape(err.details), 0
    finally:
        if token is not None:
            await client.logout(token)
        await httpx_client.aclose()
    if not meters:
        return "no_meters_found", "", 0
    return None, "", len(meters)


def _copy_uploaded_csv(hass: HomeAssistant, uploaded_id: str, dest_path: str) -> None:
    """Kopiert eine über FileSelector hochgeladene Datei an einen

    dauerhaften Ort. MUSS im Executor laufen (Datei-I/O) - siehe Aufrufer.
    Der process_uploaded_file-Kontext löscht die temporäre Datei beim
    Verlassen des "with"-Blocks, daher muss die Kopie INNERHALB davon
    passieren.
    """
    import shutil

    from homeassistant.components.file_upload import process_uploaded_file

    with process_uploaded_file(hass, uploaded_id) as src_path:
        shutil.copy(src_path, dest_path)


class PPCSmgwConfigFlow(ConfigFlow, domain=DOMAIN):
    """Führt den Benutzer durch die Einrichtung:

    1. Host eingeben -> Erreichbarkeit auf Port 443 prüfen
    2. Zugangsdaten eingeben -> NUR Login testen (Digest-Auth)
    3. Zähler abrufen und auswählen -> eigener Schritt mit eigener
       Fehleranzeige, damit ein Zähler-Problem nicht fälschlich als
       "Zugangsdaten falsch" im Login-Formular erscheint.
    4. Auswertungsprofile abrufen und auswählen -> zeigt ALLE über diese
       HAN-Zugangsdaten sichtbaren Profile (das können je nach
       Messstellenbetreiber auch abgelaufene/historische Profile früherer
       Lieferantenwechsel sein) und lässt den Nutzer wählen, welche als
       Sensoren angelegt werden sollen.
    5. Optionaler Historien-Import (TraveNetz-CSV).
    6. Optionaler zweiter Login nur für 2.8.0 (Einspeisung), z.B. wenn der
       Netzbetreiber dafür getrennte Zugangsdaten vergibt. Beide Felder
       leer lassen überspringt den Schritt: 2.8.0 kommt dann wie bisher
       über den ersten Login (sofern dieser ihn liefert).
    """

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._username: str | None = None
        self._password: str | None = None
        # Optionaler zweiter Login nur für 2.8.0 (leer = übersprungen).
        self._export_username: str = ""
        self._export_password: str = ""
        self._token: str | None = None
        self._meters: list[dict[str, str]] = []
        self._tariff_profiles: list[dict[str, str]] = []
        self._selected_meter_ids: list[str] = []
        self._selected_tariff_ids: list[str] = []
        # Fortschritts-/Statuszeilen für die zweisprachige Häkchen-Liste,
        # die zwischen den Einrichtungsschritten angezeigt wird (siehe
        # _status_block). Jeder Eintrag: (de, en).
        self._status_lines: list[tuple[str, str]] = []
        # Gemerkte Entry-Daten für den abschließenden summary-Schritt.
        self._entry_data: dict[str, Any] = {}
        # Reconfigure: neue Daten und Warteschlange der gewählten Import-Schritte.
        self._reconfigure_data: dict[str, Any] = {}
        self._reconfigure_import_queue: list[str] = []
        self._reconfigure_clear_asked: bool = False
        self._reconfigure_scan_interval: int | None = None
        # WICHTIG: Dieselbe httpx-Client-/PPCSmgwClient-Instanz wird über
        # ALLE Einrichtungsschritte hinweg wiederverwendet (nicht pro
        # Schritt neu erzeugt!). Anders als beim früheren aiohttp-Ansatz
        # (wo Cookie+Token als einfache Strings zwischen unabhängigen
        # Kurz-Sessions weitergereicht werden konnten) steckt die Session
        # bei httpx (Cookie-Jar + Digest-Auth-Zustand) im Client-Objekt
        # selbst - ein neuer Client pro Schritt hätte eine leere Session
        # und würde "keine Zähler gefunden" liefern, obwohl der Login davor
        # erfolgreich war.
        self._httpx_client: httpx.AsyncClient | None = None
        self._client: PPCSmgwClient | None = None

    async def _async_close_client(self) -> None:
        """Meldet eine evtl. noch offene Gateway-Session sauber ab und

        schließt danach den httpx-Client. Wird sowohl bei Fehlern (damit
        kein Client offen hängen bleibt) als auch am erfolgreichen Ende
        des Flows (nach dem letzten Request) aufgerufen.

        WICHTIG: Ohne das Logout bliebe die Session aus Sicht des Gateways
        aktiv (siehe coordinator.py-Klassendocstring: nur eine Session
        gleichzeitig, unzuverlässige Registersynchronisation ohne sauberes
        Logout) - der erste reguläre Update-Zyklus direkt nach Setup/
        Reconfigure/Options-Speichern könnte sonst noch auf die alte,
        offene Session treffen. `logout()` schluckt selbst bereits alle
        PPCSmgwError (siehe api.py) - ein fehlgeschlagenes Logout darf das
        Aufräumen hier nicht verhindern.
        """
        if self._client is not None and self._token is not None:
            await self._client.logout(self._token)
            self._token = None
        if self._httpx_client is not None:
            await self._httpx_client.aclose()
            self._httpx_client = None
            self._client = None

    def _add_status(self, de: str, en: str) -> None:
        """Fügt eine erledigte (grüne) Statuszeile hinzu."""
        self._status_lines.append((de, en))

    def _status_block(self, pending: list[tuple[str, str]] | None = None) -> str:
        """Baut den zweisprachigen Fortschritts-Block (Markdown) für die

        Anzeige über einem Einrichtungsschritt: bereits erledigte Prüfungen
        mit grünem Haken, noch offene Schritte mit leerem Kästchen. Die
        Sprache richtet sich nach der aktiven Home-Assistant-Sprache; kann
        sie nicht bestimmt werden, wird Deutsch genutzt.

        `pending` sind optionale, noch offene Schritte (de, en), die unter
        den erledigten mit ⬜ angezeigt werden.

        Vollständig gegen Fehler abgesichert: Diese Methode darf unter
        keinen Umständen eine Exception werfen, da sie in jedem
        Formularschritt aufgerufen wird - ein Fehler hier würde den ganzen
        Einrichtungsdialog blockieren (leere Seite mit Ladeanzeige).
        """
        try:
            lang = "de"
            try:
                lang = (self.hass.config.language or "de").split("-")[0].lower()
            except (AttributeError, TypeError):
                lang = "de"
            idx = 1 if lang == "en" else 0

            if not self._status_lines and not pending:
                return ""

            header = "**Setup progress**" if idx == 1 else "**Einrichtungs-Fortschritt**"
            lines = [header, ""]
            for entry in self._status_lines:
                lines.append(f"✅ {entry[idx]}")
            if pending:
                for entry in pending:
                    lines.append(f"⬜ {entry[idx]}")
            lines.append("")
            lines.append("---")
            return "\n".join(lines)
        except Exception:  # noqa: BLE001 - Statusanzeige darf den Flow nie brechen
            return ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> "ConfigFlowResult":
        """Schritt 1: Nur die Host-Adresse abfragen und Erreichbarkeit prüfen."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]

            if await async_check_host_reachable(host, port=443):
                # Duplikat-Prüfung HIER, vor dem ersten Login: Ein Abbruch
                # nach erfolgreichem Login würde die Gateway-Session offen
                # lassen (kein Logout), und das Gateway erlaubt nur EINE
                # Session gleichzeitig, die laufende Integration würde
                # dadurch gestört.
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                self._host = host
                self._add_status(
                    f"Gateway erreichbar ({host}:443, TLS)",
                    f"Gateway reachable ({host}:443, TLS)",
                )
                return await self.async_step_credentials()

            errors["base"] = "cannot_connect"
            self._host = host  # Eingabe im Formular erhalten

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._host or "172.20.0.1"): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={"version": VERSION},
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> "ConfigFlowResult":
        """Schritt 2: Benutzername/Passwort abfragen und NUR den Login testen.

        Hier wird bewusst NICHT die Zählerliste abgerufen - ein Fehler beim
        Auslesen der Zähler ist kein Zugangsdaten-Problem und soll den
        Nutzer hier nicht fälschlich glauben lassen, sein Passwort sei
        falsch.
        """
        errors: dict[str, str] = {}
        debug_info = ""

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]

            await self._async_close_client()  # falls ein vorheriger Versuch noch offen war
            self._httpx_client = httpx.AsyncClient(verify=False)
            self._client = PPCSmgwClient(
                self._httpx_client, self._host, self._username, self._password
            )

            try:
                self._token = await self._client.login()
            except PPCSmgwAuthError as err:
                # Echte Ablehnung durch das Gateway (HTTP 401) -> tatsächlich
                # (meist) falsche Zugangsdaten.
                errors["base"] = "invalid_auth"
                debug_info = html.escape(err.details)
                await self._async_close_client()
            except PPCSmgwParsingError as err:
                # Login war erfolgreich (HTTP 200) - die Zugangsdaten waren
                # also richtig! Es gab nur ein Problem beim Auslesen der
                # Antwortseite. Eigene, korrekte Fehlermeldung dafür.
                errors["base"] = "parsing_error"
                debug_info = html.escape(err.details)
                await self._async_close_client()
            except PPCSmgwConnectionError as err:
                errors["base"] = "cannot_connect"
                debug_info = html.escape(err.details)
                await self._async_close_client()
            else:
                fw = getattr(self._client, "firmware_version", None)
                if fw:
                    self._add_status(
                        f"Zugangsdaten gültig · Firmware {fw}",
                        f"Credentials valid · firmware {fw}",
                    )
                else:
                    self._add_status(
                        "Zugangsdaten gültig", "Credentials valid"
                    )
                return await self.async_step_meters()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=self._username or ""): str,
                vol.Required(CONF_PASSWORD, default=self._password or ""): str,
            }
        )
        return self.async_show_form(
            step_id="credentials",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "host": self._host or "",
                "debug_info": debug_info,
                "version": VERSION,
                "status": self._status_block(
                    pending=[
                        ("Zugangsdaten prüfen", "Verify credentials"),
                        ("Zähler auswählen", "Select meters"),
                    ]
                ),
            },
        )

    async def async_step_meters(
        self, user_input: dict[str, Any] | None = None
    ) -> "ConfigFlowResult":
        """Schritt 3: Zählerliste abrufen (eigener Schritt, eigene Fehleranzeige)

        und die gewünschten Zähler auswählen lassen.
        """
        errors: dict[str, str] = {}
        debug_info = ""

        if not self._meters:
            try:
                self._meters = await self._client.list_meters(self._token)
                if self._meters and not any(
                    "Zähler gefunden" in s[0] for s in self._status_lines
                ):
                    self._add_status(
                        f"{len(self._meters)} Zähler am Gateway gefunden",
                        f"{len(self._meters)} meter(s) found on gateway",
                    )
            except (PPCSmgwConnectionError, PPCSmgwAuthError) as err:
                # Kann bei PPCSmgwAuthError passieren, wenn die Session
                # zwischen Login-Schritt und diesem Schritt abgelaufen ist.
                errors["base"] = "cannot_connect"
                debug_info = html.escape(err.details)

        if user_input is not None and self._meters and not errors:
            self._selected_meter_ids = user_input[CONF_METER_IDS]
            return await self.async_step_tariffs()

        if not self._meters:
            if not errors:
                errors["base"] = "no_meters_found"
            return self.async_show_form(
                step_id="meters",
                data_schema=vol.Schema({}),
                errors=errors,
                description_placeholders={
                    "debug_info": debug_info,
                    "version": VERSION,
                    "status": self._status_block(),
                },
            )

        options_list = [
            selector.SelectOptionDict(value=m["label"], label=m["label"])
            for m in self._meters
        ]
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_METER_IDS, default=[m["label"] for m in self._meters]
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options_list, multiple=True)
                )
            }
        )
        return self.async_show_form(
            step_id="meters",
            data_schema=schema,
            description_placeholders={
                "debug_info": "",
                "version": VERSION,
                "status": self._status_block(
                    pending=[("Zähler auswählen", "Select meters")]
                ),
            },
        )

    async def async_step_tariffs(
        self, user_input: dict[str, Any] | None = None
    ) -> "ConfigFlowResult":
        """Schritt 4: Auswertungsprofile abrufen und auswählen.

        Zeigt ALLE über diese HAN-Zugangsdaten sichtbaren Auswertungsprofile
        an (inkl. Profil-ID/Gültigkeitszeitraum in der Anzeige, damit man
        abgelaufene/historische Profile früherer Lieferantenwechsel erkennt
        und bei Bedarf abwählen kann). Falls keine Profile gefunden werden,
        wird der Schritt übersprungen (nicht jeder Zähler hat welche).
        """
        errors: dict[str, str] = {}
        debug_info = ""

        if not self._tariff_profiles:
            try:
                self._tariff_profiles = await self._client.list_tariff_profiles(self._token)
            except (PPCSmgwConnectionError, PPCSmgwAuthError) as err:
                errors["base"] = "cannot_connect"
                debug_info = html.escape(err.details)

        if user_input is not None and not errors:
            self._selected_tariff_ids = user_input.get(CONF_TARIFF_IDS, [])
            return await self.async_step_history()

        if not self._tariff_profiles:
            # Kein Fehlerfall - manche Zähler haben schlicht keine
            # Auswertungsprofile über HAN sichtbar. Direkt weiter zum
            # optionalen Historien-Schritt.
            if not errors:
                self._selected_tariff_ids = []
                return await self.async_step_history()
            return self.async_show_form(
                step_id="tariffs",
                data_schema=vol.Schema({}),
                errors=errors,
                description_placeholders={
                    "debug_info": debug_info,
                    "version": VERSION,
                    "status": self._status_block(),
                },
            )

        options_list = [
            selector.SelectOptionDict(value=p["label"], label=p["label"])
            for p in self._tariff_profiles
        ]
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_TARIFF_IDS, default=[p["label"] for p in self._tariff_profiles]
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options_list, multiple=True)
                )
            }
        )
        return self.async_show_form(
            step_id="tariffs",
            data_schema=schema,
            description_placeholders={
                "debug_info": "",
                "version": VERSION,
                "status": self._status_block(
                    pending=[
                        ("Auswertungsprofile wählen", "Select evaluation profiles"),
                        ("Historien-Import (optional)", "History import (optional)"),
                    ]
                ),
            },
        )

    async def _async_prepare_csv_job(
        self, uploaded_id: str | None, start_value: float | None, suffix: str
    ) -> dict[str, Any] | None:
        """Kopiert eine hochgeladene CSV dauerhaft und liefert den Auftrag
        für die spätere Verarbeitung (None, wenn nichts hochgeladen wurde
        oder das Speichern fehlschlug).

        Die hochgeladene Datei liegt nur TEMPORÄR (bis der
        process_uploaded_file-Kontext verlassen wird) und muss daher HIER
        an einen dauerhaften Ort kopiert werden, da der eigentliche Import
        erst SPÄTER läuft (nach dem Anlegen der Entities, siehe
        __init__.py). `suffix` hält die Dateien für 1.8.0 und 2.8.0 getrennt.
        """
        if not uploaded_id:
            return None
        dest_path = self.hass.config.path(
            f"lutarym_ppc_smgw_import_{self._host.replace('.', '_')}{suffix}.csv"
        )
        try:
            await self.hass.async_add_executor_job(
                _copy_uploaded_csv, self.hass, uploaded_id, dest_path
            )
        except OSError as err:
            _LOGGER.error(
                "SMGW Einrichtung: hochgeladene CSV konnte nicht gespeichert "
                "werden: %s",
                err,
            )
            return None
        return {
            "mode": "csv",
            ATTR_CSV_PATH: dest_path,
            "start_value": start_value if start_value is not None else 0.0,
        }

    async def async_step_history(
        self, user_input: dict[str, Any] | None = None
    ) -> "ConfigFlowResult":
        """Schritt 5 (optional): 1:1-Import einer TraveNetz-CSV-Exportdatei

        für OBIS 1-0:1.8.0 (siehe travenetz_import.py) - hochgeladen direkt
        über ein Datei-Upload-Fenster (kein manueller Dateipfad mehr).

        Beide Felder sind optional: bleibt die Datei leer, wird kein
        Import durchgeführt - die Integration wird ganz normal ohne
        Historien-Import fertig eingerichtet.
        """
        if user_input is not None:
            await self._async_close_client()

            _LOGGER.debug("SMGW Einrichtung: history-Schritt user_input=%s", user_input)

            history_payload = await self._async_prepare_csv_job(
                _field(user_input, ATTR_CSV_UPLOAD),
                _field(user_input, ATTR_START_VALUE),
                suffix="",
            )
            history_payload_export = await self._async_prepare_csv_job(
                _field(user_input, ATTR_CSV_UPLOAD_EXPORT),
                _field(user_input, ATTR_START_VALUE_EXPORT),
                suffix="_export",
            )

            entry_data: dict[str, Any] = {
                CONF_HOST: self._host,
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
            }
            # Wird von __init__.py:_async_process_pending_history_import
            # EINMALIG verarbeitet und danach wieder aus entry.data
            # entfernt - daher hier als reiner "Auftrag", nicht als
            # Dauerkonfiguration.
            if history_payload:
                entry_data[ATTR_HISTORY_IMPORT] = history_payload
                self._add_status(
                    "Historien-Import vorbereitet (wird nach Einrichtung ausgeführt)",
                    "History import prepared (runs after setup)",
                )
            if history_payload_export:
                entry_data[ATTR_HISTORY_IMPORT_EXPORT] = history_payload_export
                self._add_status(
                    "Historien-Import für 2.8.0 vorbereitet (wird nach Einrichtung ausgeführt)",
                    "History import for 2.8.0 prepared (runs after setup)",
                )
            if not history_payload and not history_payload_export:
                self._add_status(
                    "Kein Historien-Import gewählt (übersprungen)",
                    "No history import selected (skipped)",
                )

            # Daten für die folgenden Schritte merken (zweiter Login, dann
            # Zusammenfassung mit der vollständigen Häkchen-Liste, die die
            # Einrichtung per Klick abschließt).
            self._entry_data = entry_data
            if history_payload or history_payload_export:
                return await self.async_step_history_clear()
            return await self.async_step_export_credentials()

        if not any("ausgewählt" in s[0] for s in self._status_lines):
            meter_n = len(self._selected_meter_ids)
            tariff_n = len(self._selected_tariff_ids)
            self._add_status(
                f"{meter_n} Zähler, {tariff_n} Auswertungsprofil(e) ausgewählt",
                f"{meter_n} meter(s), {tariff_n} evaluation profile(s) selected",
            )

        kwh_selector = selector.NumberSelector(
            selector.NumberSelectorConfig(
                mode=selector.NumberSelectorMode.BOX, unit_of_measurement="kWh"
            )
        )
        schema = vol.Schema(
            {
                vol.Optional(LABEL_UPLOAD_IMPORT): selector.FileSelector(
                    selector.FileSelectorConfig(accept=".csv,text/csv")
                ),
                vol.Optional(LABEL_START_IMPORT): kwh_selector,
                # Zweiter, getrennter Import für 2.8.0 (Einspeisung).
                vol.Optional(LABEL_UPLOAD_EXPORT): selector.FileSelector(
                    selector.FileSelectorConfig(accept=".csv,text/csv")
                ),
                vol.Optional(LABEL_START_EXPORT): kwh_selector,
            }
        )
        return self.async_show_form(
            step_id="history",
            data_schema=schema,
            description_placeholders={
                "version": VERSION,
                "status": self._status_block(
                    pending=[("Historien-Import (optional)", "History import (optional)")]
                ),
            },
        )

    async def async_step_history_clear(
        self, user_input: dict[str, Any] | None = None
    ) -> "ConfigFlowResult":
        """Frage: vorhandene Statistik vor dem Import löschen?"""
        if user_input is not None:
            clear = user_input.get(ATTR_CLEAR_EXISTING) == _CLEAR_YES
            for key in (ATTR_HISTORY_IMPORT, ATTR_HISTORY_IMPORT_EXPORT):
                if key in self._entry_data:
                    self._entry_data[key]["clear_existing"] = clear
            self._add_status(
                "Vorhandene Statistik wird vor dem Import gelöscht"
                if clear
                else "Vorhandene Statistik bleibt, Werte werden überschrieben",
                "Existing statistics are deleted before the import"
                if clear
                else "Existing statistics are kept, values are overwritten",
            )
            return await self.async_step_export_credentials()

        targets = [
            label
            for key, label in (
                (ATTR_HISTORY_IMPORT, "1.8.0 (Netzbezug)"),
                (ATTR_HISTORY_IMPORT_EXPORT, "2.8.0 (Einspeisung)"),
            )
            if key in self._entry_data
        ]
        return self.async_show_form(
            step_id="history_clear",
            data_schema=_clear_question_schema(),
            description_placeholders={
                "targets": ", ".join(targets),
                "version": VERSION,
                "status": self._status_block(),
            },
        )

    async def async_step_export_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> "ConfigFlowResult":
        """Schritt 6 (optional): zweiter Login nur für 2.8.0 (Einspeisung).

        Manche Netzbetreiber vergeben für Bezug (1.8.0) und Einspeisung
        (2.8.0) getrennte HAN-Zugangsdaten. Beide Felder leer lassen
        überspringt den Schritt, dann kommt 2.8.0 wie bisher über den
        ersten Login. Ist nur EIN Feld gefüllt, erscheint ein Fehler.
        Der Test läuft mit einer eigenen Session, die vorherige (erste)
        Session wurde im history-Schritt bereits beendet.
        """
        errors: dict[str, str] = {}
        debug_info = ""

        if user_input is not None:
            self._export_username = (user_input.get(CONF_USERNAME_EXPORT) or "").strip()
            self._export_password = user_input.get(CONF_PASSWORD_EXPORT) or ""

            if not self._export_username and not self._export_password:
                self._add_status(
                    "Zweiter Login für 2.8.0 übersprungen",
                    "Second login for 2.8.0 skipped",
                )
                return await self.async_step_summary()

            if not self._export_username or not self._export_password:
                errors["base"] = "export_credentials_incomplete"
            else:
                error_key, debug_info, meter_count = await _async_test_export_login(
                    self._host, self._export_username, self._export_password
                )
                if error_key is not None:
                    errors["base"] = f"export_{error_key}"
                else:
                    self._entry_data[CONF_USERNAME_EXPORT] = self._export_username
                    self._entry_data[CONF_PASSWORD_EXPORT] = self._export_password
                    self._add_status(
                        f"Zweiter Login für 2.8.0 gültig · {meter_count} Zähler",
                        f"Second login for 2.8.0 valid · {meter_count} meter(s)",
                    )
                    return await self.async_step_summary()

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_USERNAME_EXPORT,
                    description={"suggested_value": self._export_username},
                ): str,
                vol.Optional(
                    CONF_PASSWORD_EXPORT,
                    description={"suggested_value": self._export_password},
                ): str,
            }
        )
        return self.async_show_form(
            step_id="export_credentials",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "debug_info": debug_info,
                "version": VERSION,
                "status": self._status_block(
                    pending=[
                        (
                            "Zweiter Login für 2.8.0 (optional)",
                            "Second login for 2.8.0 (optional)",
                        )
                    ]
                ),
            },
        )

    async def async_step_summary(
        self, user_input: dict[str, Any] | None = None
    ) -> "ConfigFlowResult":
        """Abschluss-Schritt: zeigt die vollständige Häkchen-Liste aller

        erfolgreich durchlaufenen Schritte und legt die Integration erst
        an, wenn der Nutzer aktiv bestätigt (Button "Absenden"). So sieht
        man am Ende auf einen Blick, dass jeder Schritt funktioniert hat -
        statt dass die Häkchen zwischen den Eingabeformularen "durchhuschen".
        """
        if user_input is not None:
            return self.async_create_entry(
                title=f"PPC SMGW ({self._host})",
                data=self._entry_data,
                options={
                    CONF_METER_IDS: self._selected_meter_ids,
                    CONF_TARIFF_IDS: self._selected_tariff_ids,
                },
            )

        # Ein sichtbares Bestätigungsfeld (statt leerem Schema): Ein
        # feldloses Formular wird vom HA-Frontend nicht zuverlässig mit
        # Beschreibungstext und Absenden-Button gerendert (es erscheint nur
        # der runde Button, der Text/Häkchen bleiben leer). Mit einem
        # optionalen bestätigten-Feld rendert HA das Formular normal - der
        # Nutzer sieht die Häkchen-Liste und einen klaren Absenden-Button.
        return self.async_show_form(
            step_id="summary",
            data_schema=vol.Schema(
                {vol.Optional("confirm", default=True): bool}
            ),
            description_placeholders={
                "version": VERSION,
                "status": self._status_block(),
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> "ConfigFlowResult":
        """Wird von Home Assistant gestartet, wenn der Coordinator
        ConfigEntryAuthFailed meldet (Gateway lehnt den ersten Login ab,
        z.B. nach Passwortwechsel beim Messstellenbetreiber). Ohne diesen
        Schritt gäbe es keinen Weg über die Oberfläche, die Zugangsdaten zu
        korrigieren, und HA würde bei jedem Fehlschlag einen Fehler loggen.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> "ConfigFlowResult":
        """Fragt Benutzername/Passwort des ERSTEN Logins neu ab und testet
        sie mit einer eigenen Kurz-Session (Login, Logout). Ein optionaler
        zweiter Login (2.8.0) bleibt unverändert.
        """
        errors: dict[str, str] = {}
        debug_info = ""
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            httpx_client = httpx.AsyncClient(verify=False)
            client = PPCSmgwClient(
                httpx_client, reauth_entry.data[CONF_HOST], username, password
            )
            token: str | None = None
            try:
                token = await client.login()
            except PPCSmgwAuthError as err:
                errors["base"] = "invalid_auth"
                debug_info = html.escape(err.details)
            except PPCSmgwParsingError as err:
                errors["base"] = "parsing_error"
                debug_info = html.escape(err.details)
            except PPCSmgwConnectionError as err:
                errors["base"] = "cannot_connect"
                debug_info = html.escape(err.details)
            finally:
                if token is not None:
                    await client.logout(token)
                await httpx_client.aclose()

            if not errors:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_USERNAME: username, CONF_PASSWORD: password},
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME,
                    default=(user_input or {}).get(
                        CONF_USERNAME, reauth_entry.data.get(CONF_USERNAME, "")
                    ),
                ): str,
                vol.Required(CONF_PASSWORD, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "host": reauth_entry.data[CONF_HOST],
                "debug_info": debug_info,
                "version": VERSION,
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> "ConfigFlowResult":
        """Erlaubt das nachträgliche Ändern von Host/Benutzername/Passwort

        über "Neu konfigurieren" (⋮-Menü der Integration), OHNE den
        bestehenden Config Entry zu löschen. Dadurch bleiben alle
        Entity-IDs, der Verlauf und die importierten Langzeit-Statistiken
        erhalten (die hängen an entry_id, nicht an der unique_id) - anders
        als bei "löschen + neu einrichten". Ändert sich dabei der Host,
        wird die unique_id (= Host) mitgezogen, siehe Duplikat-Check
        weiter unten.

        Testet die neuen Zugangsdaten genau wie async_step_credentials
        (ein Login-Versuch), bevor sie tatsächlich übernommen werden.
        """
        errors: dict[str, str] = {}
        debug_info = ""
        reconfigure_entry = self._get_reconfigure_entry()

        if self._host is None:
            # Beim ersten Anzeigen des Formulars mit den bisherigen Werten
            # vorbefüllen, damit man nicht alles neu eintippen muss.
            self._host = reconfigure_entry.data[CONF_HOST]
            self._username = reconfigure_entry.data[CONF_USERNAME]
            self._password = reconfigure_entry.data[CONF_PASSWORD]
            self._export_username = reconfigure_entry.data.get(CONF_USERNAME_EXPORT) or ""
            self._export_password = reconfigure_entry.data.get(CONF_PASSWORD_EXPORT) or ""

        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            self._export_username = (user_input.get(CONF_USERNAME_EXPORT) or "").strip()
            self._export_password = user_input.get(CONF_PASSWORD_EXPORT) or ""

            await self._async_close_client()
            self._httpx_client = httpx.AsyncClient(verify=False)
            self._client = PPCSmgwClient(
                self._httpx_client, self._host, self._username, self._password
            )

            try:
                self._token = await self._client.login()
            except PPCSmgwAuthError as err:
                errors["base"] = "invalid_auth"
                debug_info = html.escape(err.details)
            except PPCSmgwParsingError as err:
                # Login war erfolgreich (Zugangsdaten korrekt), aber die
                # Antwortseite konnte nicht gelesen werden - wie bei
                # async_step_credentials trotzdem als Fehler anzeigen,
                # damit der Nutzer die Details sieht, statt es stillschweigend
                # zu übernehmen.
                errors["base"] = "parsing_error"
                debug_info = html.escape(err.details)
            except PPCSmgwConnectionError as err:
                errors["base"] = "cannot_connect"
                debug_info = html.escape(err.details)
            finally:
                await self._async_close_client()

            # Optionaler zweiter Login (2.8.0): erst NACH dem Ende der ersten
            # Session testen (das Gateway erlaubt nur eine Session
            # gleichzeitig). Beide Felder leer = kein zweiter Login (auch zum
            # nachträglichen Entfernen).
            if not errors:
                if bool(self._export_username) != bool(self._export_password):
                    errors["base"] = "export_credentials_incomplete"
                elif self._export_username:
                    error_key, debug_info, _count = await _async_test_export_login(
                        self._host, self._export_username, self._export_password
                    )
                    if error_key is not None:
                        errors["base"] = f"export_{error_key}"

            if not errors:
                # Host kann sich über diesen Flow ändern (siehe Docstring) -
                # die unique_id (= Host, siehe async_step_credentials) wird
                # beim Abschluss (_async_finish_reconfigure) mitgezogen,
                # damit entry.data und unique_id nicht auseinanderlaufen.
                # Manueller Duplikat-Check statt _abort_if_unique_id_configured():
                # ob dieser Helper innerhalb eines Reconfigure-Flows den
                # eigenen, gerade bearbeiteten Entry zuverlässig ausschließt,
                # ist nicht eindeutig, lieber explizit und nachvollziehbar.
                if self._host != reconfigure_entry.unique_id:
                    for other_entry in self.hass.config_entries.async_entries(DOMAIN):
                        if (
                            other_entry.entry_id != reconfigure_entry.entry_id
                            and other_entry.unique_id == self._host
                        ):
                            return self.async_abort(reason="already_configured")
                new_data = {
                    CONF_HOST: self._host,
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                }
                if self._export_username and self._export_password:
                    new_data[CONF_USERNAME_EXPORT] = self._export_username
                    new_data[CONF_PASSWORD_EXPORT] = self._export_password
                self._reconfigure_data = new_data
                self._reconfigure_scan_interval = int(
                    user_input.get(
                        CONF_SCAN_INTERVAL, _current_interval_minutes(reconfigure_entry)
                    )
                ) * 60
                # Optionaler (erneuter) Historien-Import: jeder gewählte Wert
                # bekommt einen eigenen Schritt mit eigenem Titel, damit
                # eindeutig ist, welche Datei wohin gehört.
                self._reconfigure_import_queue = [
                    obis
                    for obis in ("1.8.0", "2.8.0")
                    if obis in (user_input.get(CONF_IMPORT_OBIS) or [])
                ]
                return await self._async_next_reconfigure_import()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._host): str,
                vol.Required(CONF_USERNAME, default=self._username): str,
                vol.Required(CONF_PASSWORD, default=self._password or ""): str,
                # suggested_value statt default: bei default würde ein
                # geleertes Feld vom Validator wieder mit dem alten Wert
                # gefüllt, der zweite Login ließe sich nie entfernen.
                vol.Optional(
                    CONF_USERNAME_EXPORT,
                    description={"suggested_value": self._export_username},
                ): str,
                vol.Optional(
                    CONF_PASSWORD_EXPORT,
                    description={"suggested_value": self._export_password},
                ): str,
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=str(_current_interval_minutes(reconfigure_entry)),
                ): _interval_selector(_current_interval_minutes(reconfigure_entry)),
                # Optional: Historie (erneut) importieren. Leer = kein Import.
                # Die Optionstexte kommen direkt aus dem Code, daher ist die
                # Zuordnung immer eindeutig lesbar.
                vol.Optional(CONF_IMPORT_OBIS, default=[]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value="1.8.0",
                                label="Historie importieren für 1.8.0 (Netzbezug)",
                            ),
                            selector.SelectOptionDict(
                                value="2.8.0",
                                label="Historie importieren für 2.8.0 (Einspeisung)",
                            ),
                        ],
                        multiple=True,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
            description_placeholders={"debug_info": debug_info, "version": VERSION},
        )

    async def _async_next_reconfigure_import(self) -> "ConfigFlowResult":
        """Geht zum nächsten gewählten Import-Schritt oder schließt ab."""
        if self._reconfigure_import_queue:
            if self._reconfigure_import_queue[0] == "1.8.0":
                return await self.async_step_reconfigure_import_1_8_0()
            return await self.async_step_reconfigure_import_2_8_0()
        has_import = (
            ATTR_HISTORY_IMPORT in self._reconfigure_data
            or ATTR_HISTORY_IMPORT_EXPORT in self._reconfigure_data
        )
        if has_import and not self._reconfigure_clear_asked:
            return await self.async_step_reconfigure_clear()
        return await self._async_finish_reconfigure()

    async def async_step_reconfigure_clear(
        self, user_input: dict[str, Any] | None = None
    ) -> "ConfigFlowResult":
        """Frage: vorhandene Statistik vor dem Import löschen?"""
        if user_input is not None:
            clear = user_input.get(ATTR_CLEAR_EXISTING) == _CLEAR_YES
            for key in (ATTR_HISTORY_IMPORT, ATTR_HISTORY_IMPORT_EXPORT):
                if key in self._reconfigure_data:
                    self._reconfigure_data[key]["clear_existing"] = clear
            self._reconfigure_clear_asked = True
            return await self._async_finish_reconfigure()

        targets = [
            label
            for key, label in (
                (ATTR_HISTORY_IMPORT, "1.8.0 (Netzbezug)"),
                (ATTR_HISTORY_IMPORT_EXPORT, "2.8.0 (Einspeisung)"),
            )
            if key in self._reconfigure_data
        ]
        return self.async_show_form(
            step_id="reconfigure_clear",
            data_schema=_clear_question_schema(),
            description_placeholders={
                "targets": ", ".join(targets),
                "version": VERSION,
            },
        )

    async def _async_reconfigure_import_form(
        self, step_id: str, obis: str, user_input: dict[str, Any] | None
    ) -> "ConfigFlowResult":
        """Gemeinsame Logik der beiden Upload-Schritte (je EIN Upload-Feld)."""
        if user_input is not None:
            if obis == "1.8.0":
                payload = await self._async_prepare_csv_job(
                    _field(user_input, ATTR_CSV_UPLOAD),
                    _field(user_input, ATTR_START_VALUE),
                    suffix="",
                )
                if payload:
                    self._reconfigure_data[ATTR_HISTORY_IMPORT] = payload
            else:
                payload = await self._async_prepare_csv_job(
                    _field(user_input, ATTR_CSV_UPLOAD_EXPORT),
                    _field(user_input, ATTR_START_VALUE_EXPORT),
                    suffix="_export",
                )
                if payload:
                    self._reconfigure_data[ATTR_HISTORY_IMPORT_EXPORT] = payload
            self._reconfigure_import_queue.pop(0)
            return await self._async_next_reconfigure_import()

        upload_key = LABEL_UPLOAD_IMPORT if obis == "1.8.0" else LABEL_UPLOAD_EXPORT
        start_key = LABEL_START_IMPORT if obis == "1.8.0" else LABEL_START_EXPORT
        schema = vol.Schema(
            {
                vol.Optional(upload_key): selector.FileSelector(
                    selector.FileSelectorConfig(accept=".csv,text/csv")
                ),
                vol.Optional(start_key): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.BOX, unit_of_measurement="kWh"
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            description_placeholders={"version": VERSION},
        )

    async def async_step_reconfigure_import_1_8_0(
        self, user_input: dict[str, Any] | None = None
    ) -> "ConfigFlowResult":
        """Upload der TraveNetz-CSV für 1.8.0 (Netzbezug)."""
        return await self._async_reconfigure_import_form(
            "reconfigure_import_1_8_0", "1.8.0", user_input
        )

    async def async_step_reconfigure_import_2_8_0(
        self, user_input: dict[str, Any] | None = None
    ) -> "ConfigFlowResult":
        """Upload der TraveNetz-CSV für 2.8.0 (Einspeisung)."""
        return await self._async_reconfigure_import_form(
            "reconfigure_import_2_8_0", "2.8.0", user_input
        )

    async def _async_finish_reconfigure(self) -> "ConfigFlowResult":
        """Übernimmt die neuen Daten (inkl. optionaler Import-Aufträge)."""
        reconfigure_entry = self._get_reconfigure_entry()
        if self._host != reconfigure_entry.unique_id:
            self.hass.config_entries.async_update_entry(
                reconfigure_entry, unique_id=self._host
            )
        if self._reconfigure_scan_interval is not None:
            self.hass.config_entries.async_update_entry(
                reconfigure_entry,
                options={
                    **reconfigure_entry.options,
                    CONF_SCAN_INTERVAL: self._reconfigure_scan_interval,
                },
            )
        return self.async_update_reload_and_abort(
            reconfigure_entry, data=self._reconfigure_data
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "PPCSmgwOptionsFlow":
        """Verknüpft den "Konfigurieren"-Dialog einer bestehenden Integration

        mit PPCSmgwOptionsFlow (Zähler-/Auswertungsprofil-Auswahl ändern,
        ohne die Integration neu einrichten zu müssen).
        """
        return PPCSmgwOptionsFlow(config_entry)


class PPCSmgwOptionsFlow(OptionsFlow):
    """Erlaubt es, die ausgewählten Zähler und Auswertungsprofile

    nachträglich zu ändern.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> "ConfigFlowResult":
        """Zeigt/verarbeitet das Formular zur nachträglichen Zähler-/

        Auswertungsprofil-Auswahl. Ruft dafür einmalig Zählerliste UND
        Auswertungsprofile frisch vom Gateway ab (ein Login, ein Client,
        siehe PPCSmgwClient-Klassendocstring in api.py). Schlägt der Abruf
        fehl (z.B. Gateway kurzzeitig nicht erreichbar), wird auf die
        BISHER gespeicherte Auswahl zurückgefallen, damit der Dialog trotz
        Verbindungsproblem nutzbar bleibt (nur mit Fehlermeldung).
        """
        errors: dict[str, str] = {}
        httpx_client = httpx.AsyncClient(verify=False)
        client = PPCSmgwClient(
            httpx_client,
            self._config_entry.data[CONF_HOST],
            self._config_entry.data[CONF_USERNAME],
            self._config_entry.data[CONF_PASSWORD],
        )

        token: str | None = None
        try:
            token = await client.login()
            meters = await client.list_meters(token)
            tariffs = await client.list_tariff_profiles(token)
        except (PPCSmgwAuthError, PPCSmgwParsingError, PPCSmgwConnectionError):
            errors["base"] = "cannot_connect"
            # Fallback: aus der bisher gespeicherten Auswahl "Fake"-Einträge
            # bauen (nur mit "label", ohne mid/tid), damit die
            # Auswahl-Checkboxen im Formular trotzdem etwas anzuzeigen haben.
            meters = [
                {"label": label}
                for label in self._config_entry.options.get(CONF_METER_IDS, [])
            ]
            tariffs = [
                {"label": label}
                for label in self._config_entry.options.get(CONF_TARIFF_IDS, [])
            ]
        finally:
            # Siehe PPCSmgwConfigFlow._async_close_client für die Begründung,
            # warum das Logout hier nicht fehlen darf (nur eine Gateway-
            # Session gleichzeitig). Wird bei JEDEM Öffnen dieses Dialogs
            # neu eingeloggt, also auch bei jedem Aufruf wieder ausgeloggt.
            if token is not None:
                await client.logout(token)
            await httpx_client.aclose()

        if user_input is not None and not errors:
            return self.async_create_entry(
                data={
                    CONF_METER_IDS: user_input[CONF_METER_IDS],
                    CONF_TARIFF_IDS: user_input.get(CONF_TARIFF_IDS, []),
                    # Minuten (UI) -> Sekunden (intern).
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]) * 60,
                }
            )

        current_meters = self._config_entry.options.get(
            CONF_METER_IDS, [m["label"] for m in meters]
        )
        current_tariffs = self._config_entry.options.get(
            CONF_TARIFF_IDS, [p["label"] for p in tariffs]
        )
        current_scan_interval_minutes = _current_interval_minutes(self._config_entry)
        meter_options = [
            selector.SelectOptionDict(value=m["label"], label=m["label"]) for m in meters
        ]
        tariff_options = [
            selector.SelectOptionDict(value=p["label"], label=p["label"]) for p in tariffs
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_METER_IDS, default=current_meters): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=meter_options, multiple=True)
                ),
                vol.Optional(CONF_TARIFF_IDS, default=current_tariffs): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=tariff_options, multiple=True)
                ),
                vol.Required(
                    CONF_SCAN_INTERVAL, default=str(current_scan_interval_minutes)
                ): _interval_selector(current_scan_interval_minutes),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
