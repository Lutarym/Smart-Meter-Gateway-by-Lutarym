# Integrationsversion: 2.5.1
"""Client für die HAN-Schnittstelle eines PPC LTE Smart Meter Gateways (SMGW).

Das Gateway bietet keine "echte" REST-API, sondern nur das interne
HTML-Formular unter /cgi-bin/hanservice.cgi, abgesichert per HTTP Digest
Auth + Session-Cookie. Dieser Client bildet den bekannten Formular-Flow
nach:

  1. GET  hanservice.cgi                          (Digest-Auth) -> Token + Cookie
  2. POST tkn=<token>&action=meterform             -> Liste der Zähler
  3. POST tkn=<token>&action=showMeterProfile&mid=<id> -> Messwert-Tabelle

WICHTIG (Version 0.13.0): Nutzt `httpx` statt `aiohttp`, mit Digest-Auth
auf JEDEM einzelnen Request (nicht nur einmalig beim Login) und dem
automatischen Cookie-Jar von httpx statt manueller Cookie-Header-Verwaltung.
Grund: Bei ausführlichen Vergleichstests (parallel, mit Sekunden Abstand,
gegen dasselbe Gateway) lieferte ein strukturell ansonsten identischer
aiohttp-Client (einmaliger Digest-Login, danach nur noch Cookie-basierte
Folge-Requests) wiederholt einen alten/eingefrorenen Zählerstand, während
ein httpx-basierter Client mit Digest-Auth auf jedem Request zuverlässig
frische Werte bekam. Die genaue Ursache auf Gateway-Seite ist nicht
abschließend geklärt, aber das Verhalten ist reproduzierbar - deshalb bildet
dieser Client jetzt bewusst exakt dieses Anfrage-Muster nach.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime

import httpx

from .const import HAN_PATH

_LOGGER = logging.getLogger(__name__)


async def async_check_host_reachable(host: str, port: int = 443, timeout: float = 5.0) -> bool:
    """Prüft per reinem TCP-Connect, ob unter host:port etwas antwortet.

    Es wird bewusst kein TLS-Handshake durchgeführt (das selbstsignierte
    Zertifikat des Gateways würde das sonst unnötig verkomplizieren) -
    ein erfolgreicher TCP-Connect genügt, um zu wissen, dass auf Port 443
    etwas lauscht und antwortet.
    """
    import asyncio

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except (OSError, asyncio.TimeoutError):
        return False

    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


class PPCSmgwError(Exception):
    """Basisfehler für alle Fehler des Clients.

    `details` enthält technische Diagnoseinformationen (Status-Codes,
    gesendete/empfangene Header - NIE das Passwort), die im Config-Flow-
    Formular angezeigt werden können, damit man sie nicht erst aus den
    Logs heraussuchen muss.
    """

    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(message)
        self.details = details


class PPCSmgwAuthError(PPCSmgwError):
    """Echter Authentifizierungsfehler: Gateway lehnt die Digest-Auth ab

    (HTTP 401 trotz korrekt gesendetem Authorization-Header). Das ist
    tatsächlich meist falscher Benutzername/Passwort.
    """


class PPCSmgwParsingError(PPCSmgwError):
    """Login war erfolgreich (HTTP 200), aber die HTML-Antwort des Gateways

    konnte nicht wie erwartet ausgelesen werden (z.B. Token nicht gefunden).
    Das ist AUSDRÜCKLICH KEIN Zugangsdaten-Problem - die Zugangsdaten waren
    korrekt, sonst hätte das Gateway gar nicht mit 200 geantwortet.
    """


class PPCSmgwConnectionError(PPCSmgwError):
    """Fehler bei der Verbindung zum Gateway."""


class PPCSmgwClient:
    """Asynchroner Client für die HAN-Schnittstelle eines PPC SMGW.

    Öffentliche Methoden (jede macht genau einen HTTP-Request gegen das
    Gateway, siehe jeweiligen Docstring für Details):

    - `login()` - authentifiziert sich, liefert den CSRF-Token für alle
      Folge-Requests dieses Zyklus.
    - `list_meters(token)` - Liste der verfügbaren Zähler.
    - `get_meter_readings(token, mid)` - Messwerte + Metadaten eines Zählers.
    - `list_tariff_profiles(token)` - Liste der Auswertungsprofile.
    - `get_tariff_profile_value(token, tid)` - Konfigurationsdaten eines
      Auswertungsprofils.
    - `selftest(token)` - löst einen Gateway-Neustart aus.
    - `logout(token)` - beendet die Session.

    Ein typischer Update-Zyklus (siehe coordinator.py):
    `login()` → `list_meters()` → `get_meter_readings()` (je Zähler) →
    `list_tariff_profiles()` → `get_tariff_profile_value()` (je Profil) →
    `logout()`.
    """

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        host: str,
        username: str,
        password: str,
    ) -> None:
        self._client = httpx_client
        self._host = host
        self._username = username
        self._password = password
        self._url = f"https://{host}{HAN_PATH}"
        self.firmware_version: str | None = None
        # Wird in login() pro Zyklus NEU erzeugt - siehe Moduldocstring.
        self._auth: httpx.DigestAuth | None = None
        # Nur für Diagnose/Vergleich mit der Referenz-Implementierung.
        self._last_request_headers: httpx.Headers | None = None

    async def _raw_request(
        self,
        method: str,
        headers: dict[str, str] | None = None,
        data: str | None = None,
    ) -> tuple[int, httpx.Headers, str]:
        """Führt einen einzelnen HTTP-Request gegen die HAN-URL aus.

        Zentrale, einzige Stelle, an der tatsächlich Netzwerk-I/O
        stattfindet - alle öffentlichen Methoden dieser Klasse rufen
        letztlich diese Funktion auf. `auth=self._auth` sorgt dafür, dass
        JEDER Request (nicht nur der Login-GET) den vollen
        Digest-Auth-Handshake durchläuft, siehe Moduldocstring. Extrahiert
        nebenbei die Gateway-Firmware-Version aus dem Seiten-Footer (steht
        auf jeder Seite) und merkt sich die tatsächlich gesendeten
        Request-Header für Diagnose-Zwecke.

        Gibt (HTTP-Status, Antwort-Header, Antwort-Body als Text) zurück.
        Wirft PPCSmgwConnectionError bei Timeout/Verbindungsfehlern -
        HTTP-Fehlerstatuscodes (401, 500, ...) werden NICHT hier, sondern
        von den aufrufenden Methoden ausgewertet, da die Bedeutung je nach
        Aufrufkontext unterschiedlich ist (z.B. 401 beim Login = falsches
        Passwort, 401 später = abgelaufene Session).
        """
        try:
            response = await self._client.request(
                method,
                self._url,
                headers=headers,
                content=data,
                auth=self._auth,
                timeout=15,
            )
        except httpx.TimeoutException as err:
            raise PPCSmgwConnectionError(
                "Zeitüberschreitung bei Verbindung zum Gateway",
                details=f"URL: {self._url}\nMethode: {method}",
            ) from err
        except httpx.HTTPError as err:
            raise PPCSmgwConnectionError(
                f"Verbindungsfehler: {err}",
                details=f"URL: {self._url}\nMethode: {method}\nException: {err!r}",
            ) from err

        body = response.text
        self._last_request_headers = response.request.headers
        fw_match = re.search(r"""id=["']div_fwversion["'][^>]*>([^<]*)<""", body, re.IGNORECASE)
        if fw_match:
            self.firmware_version = fw_match.group(1).strip()
        return response.status_code, response.headers, body

    @staticmethod
    def _extract_token(html_body: str) -> str | None:
        """Extrahiert den CSRF-Token (tkn) aus der Login-Seite.

        Sucht gezielt nach einem <input>-Tag mit name="tkn" (unabhängig von
        der Attribut-Reihenfolge und davon, ob einfache oder doppelte
        Anführungszeichen verwendet werden - manche Firmware-Versionen
        dieses Gateways nutzen einfache Anführungszeichen im HTML).
        """
        for tag_match in re.finditer(r"<input\b[^>]*>", html_body, re.IGNORECASE):
            tag = tag_match.group(0)
            if re.search(r"""name=["']tkn["']""", tag, re.IGNORECASE):
                value_match = re.search(r"""value=["']([^"']*)["']""", tag, re.IGNORECASE)
                if value_match:
                    return value_match.group(1)

        # Fallback: falls kein Feld namens "tkn" existiert, wie beim
        # Referenzskript das allererste Input-Feld mit value= nehmen.
        fallback_match = re.search(
            r"""<input\b[^>]*\bvalue=["']([^"']*)["']""", html_body, re.IGNORECASE
        )
        return fallback_match.group(1) if fallback_match else None

    @staticmethod
    def _extract_meter_options(html_body: str) -> list[dict[str, str]]:
        """Extrahiert die Zählerliste.

        WICHTIG: Der `value`-Wert der <option> (die "mid") ist NICHT
        stabil - er rotiert bei jeder Session/jedem Login neu (vermutlich
        eine Art Session-Token pro Zähler-Auswahl, kein fester Zähler-Bezug).
        Das eigentliche, über die Zeit stabile Merkmal ist der sichtbare
        Options-Text (z.B. "01005e318002.1lgz0081554715.sm"). Daher liefert
        diese Methode BEIDES: "label" (stabil, zur Identifikation/Speicherung
        geeignet) und "mid" (nur für den aktuellen Request-Zyklus gültig).
        """
        select_match = re.search(
            r"""id=["']meterform_select_meter["'][\s\S]*?</select>""",
            html_body,
            re.IGNORECASE,
        )
        if not select_match:
            return []
        options = []
        for match in re.finditer(
            r"""<option\b[^>]*\bvalue=["']([^"']*)["'][^>]*>([^<]*)<""",
            select_match.group(0),
            re.IGNORECASE,
        ):
            label = html.unescape(match.group(2).strip())
            options.append({"label": label, "mid": match.group(1)})
        return options

    @staticmethod
    def _extract_by_id(html_body: str, element_id: str) -> str | None:
        match = re.search(
            rf"""id=["']{element_id}["'][^>]*>([^<]*)<""", html_body, re.IGNORECASE
        )
        return html.unescape(match.group(1).strip()) if match else None

    @staticmethod
    def _extract_meter_info_field(html_body: str, label: str) -> str | None:
        """Extrahiert ein Feld aus einer einfachen Label/Wert-Tabelle

        (Zähler-Metadaten, Auswertungsprofil-Basisprofil, ...), Zeilenformat
        <tr><td [Attribute]>Label</td><td [Attribute]>Wert</td></tr>. Beide
        <td>-Tags können zusätzliche Attribute wie `width=20%` tragen (z.B.
        bei den Auswertungsprofil-Basisprofil-Tabellen) - die Regex muss
        das tolerieren, statt nur exaktes `<td>` zu erwarten.
        """
        match = re.search(
            rf"""<tr>\s*<td\b[^>]*>{re.escape(label)}</td>\s*<td\b[^>]*>([^<]*)</td>\s*</tr>""",
            html_body,
            re.IGNORECASE,
        )
        if not match:
            return None
        value = html.unescape(match.group(1).strip())
        return value or None

    async def login(self) -> str:
        """Führt den Digest-Auth-Login durch und liefert den CSRF-Token.

        Erzeugt bei JEDEM Aufruf ein FRISCHES httpx.DigestAuth-Objekt, das
        danach für ALLE weiteren Requests dieses Zyklus wiederverwendet
        wird (login -> Folge-Requests -> logout, siehe Moduldocstring).
        Verwendet KEINEN manuell verwalteten Cookie-Header mehr - die
        Session-Cookie-Weitergabe übernimmt der httpx-Client automatisch
        über seinen eingebauten Cookie-Jar.
        """
        # Evtl. von einem vorherigen (abgebrochenen) Zyklus übrig gebliebenes
        # Session-Cookie entfernen, damit garantiert eine frische Session
        # entsteht (analog zu einem bekannten, verifiziert funktionierenden
        # Referenz-Client für dasselbe Gateway).
        if self._client.cookies.get("session") is not None:
            self._client.cookies.delete("session")

        self._auth = httpx.DigestAuth(username=self._username, password=self._password)

        status, headers, body = await self._raw_request("GET")
        _LOGGER.debug("SMGW: Login-GET (Digest-Auth) -> Status %s", status)

        if status == 401:
            raise PPCSmgwAuthError(
                "Benutzername oder Passwort falsch.",
                details=f"Status=401\nWWW-Authenticate: {headers.get('WWW-Authenticate', '(keine)')}",
            )
        if status != 200:
            raise PPCSmgwConnectionError(
                f"Unerwarteter Status beim Login: {status}",
                details=f"Status={status}\nAntwort (erste 500 Zeichen): {body[:500]}",
            )

        if "session" not in self._client.cookies:
            raise PPCSmgwConnectionError(
                "Login erfolgreich, aber kein Session-Cookie in der Antwort enthalten.",
                details=f"Status={status}\nAntwort-Header: {dict(headers)}",
            )

        token = self._extract_token(body)
        if not token:
            found_inputs = re.findall(r"<input\b[^>]*>", body, re.IGNORECASE)
            raise PPCSmgwParsingError(
                "Login erfolgreich, aber Seite konnte nicht ausgelesen werden.",
                details=(
                    "Token nicht gefunden.\n\nGefundene <input>-Tags auf der Seite:\n"
                    + "\n".join(found_inputs[:20])
                ),
            )
        return token

    async def logout(self, token: str) -> None:
        """Meldet die aktuelle Session am Gateway ab.

        WICHTIG: Das SMGW erlaubt laut Beobachtung nur eine aktive Session
        gleichzeitig. Fehler beim Logout werden bewusst nur geloggt, nicht
        als Exception nach oben gereicht, damit ein fehlgeschlagenes Logout
        nicht den gesamten Update-Zyklus scheitern lässt.
        """
        post_data = f"tkn={token}&action=logout"
        try:
            await self._raw_request(
                "POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=post_data,
            )
        except PPCSmgwError as err:
            _LOGGER.warning("SMGW: Logout fehlgeschlagen (unkritisch): %s", err)

    async def selftest(self, token: str) -> None:
        """Löst den "Selbsttest" des Gateways aus (action=selftest).

        Das ist praktisch ein Neustart des SMGW. Bewusst KEIN Logout danach
        - das Gateway startet neu, eine noch offene Session wird dadurch
        ohnehin hinfällig.
        """
        post_data = f"tkn={token}&action=selftest"
        status, _headers, _body = await self._raw_request(
            "POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=post_data,
        )
        if status != 200:
            raise PPCSmgwConnectionError(
                f"Selbsttest/Neustart konnte nicht ausgelöst werden (HTTP {status})."
            )

    async def list_meters(self, token: str) -> list[dict[str, str]]:
        """Liefert die Liste der am Gateway verfügbaren Zähler.

        Jeder Eintrag enthält "label" (stabiler Zählername) und "mid"
        (nur für den aktuellen Request-Zyklus gültige Session-ID - siehe
        Hinweis in _extract_meter_options).
        """
        post_data = f"tkn={token}&action=meterform"
        status, _headers, body = await self._raw_request(
            "POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=post_data,
        )
        if status != 200:
            raise PPCSmgwConnectionError(f"Zählerliste konnte nicht geladen werden (HTTP {status}).")
        return self._extract_meter_options(body)

    async def get_meter_readings(self, token: str, mid: str) -> list[dict[str, str | None]]:
        """Liefert ALLE aktuellen Messwerte eines Zählers über "Zählerprofil"

        (action=showMeterProfile). Liest Messwerte UND Metadaten in EINEM
        einzigen Request (beide stehen auf derselben Seite).

        `mid` MUSS aus einem list_meters()-Aufruf STAMMEN, DER MIT DEMSELBEN
        `token` GEMACHT WURDE (mid ist an die aktuelle Session gebunden und
        wird bei jedem neuen Login ungültig).
        """
        post_data = f"tkn={token}&action=showMeterProfile&mid={mid}"
        status, _headers, body = await self._raw_request(
            "POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=post_data,
        )
        if status != 200:
            raise PPCSmgwConnectionError(f"Messwerte konnten nicht geladen werden (HTTP {status}).")

        table_match = re.search(
            r"""<table[^>]*id=["']metervalue["'][\s\S]*?</table>""", body, re.IGNORECASE
        )
        _LOGGER.debug(
            "RAW METERVALUE HTML: %s",
            table_match.group(0) if table_match else "(keine metervalue-Tabelle gefunden)",
        )
        _LOGGER.debug(
            "Gesendete Request-Header: %s | Ziel-URL: %s",
            dict(self._last_request_headers) if self._last_request_headers else "(keine)",
            self._url,
        )

        readings = self._extract_meter_readings(body)
        if not readings:
            last_sync = self._extract_last_sync_timestamp(body)
            _LOGGER.warning(
                "SMGW: Keine Messwert-Zeilen in der 'Zählerprofil'-Antwort "
                "gefunden.%s Gesendete POST-Daten: %s. Voller HTML-Body "
                "(Länge=%d): %s",
                f" Letzte Synchronisation laut Gateway: {last_sync}." if last_sync else "",
                post_data,
                len(body),
                body,
            )
            raise PPCSmgwConnectionError(
                "Keine Messwert-Zeilen im HTML gefunden (evtl. hat sich das Layout des Gateways geändert).",
                details=f"Gesendete POST-Daten: {post_data}\n\nVoller HTML-Body (Länge={len(body)}):\n{body}",
            )

        meta = self._extract_meter_metadata(body)
        for reading in readings:
            reading.update(meta)
        return readings

    @staticmethod
    def _extract_meter_metadata(body: str) -> dict[str, str | None]:
        """Liest ALLE statischen Zähler-Metadaten aus dem HTML-Body der

        "Zählerprofil"-Seite (Zähler-ID, Name, Beschreibung,
        Kommunikationstyp, Protokoll-Typ, Protokoll-Version,
        Ausleseintervall, Abfrageversuche, Zähleradresse, Medium - siehe
        PPC-Handbuch Kapitel 4.3, Abbildung 13).
        """

        def field(*labels: str) -> str | None:
            for label in labels:
                result = PPCSmgwClient._extract_meter_info_field(body, label)
                if result is not None:
                    return result
            return None

        return {
            "zaehler_id": field("Z&auml;hler-ID", "Zähler-ID"),
            "zaehler_name": field("Name"),
            "beschreibung": field("Beschreibung"),
            "kommunikationstyp": field("Kommunikationstyp"),
            "protokoll_typ": field("Protokoll-Typ"),
            "protokoll_version": field("Protokoll-Version"),
            "ausleseintervall_sekunden": field("Ausleseintervall"),
            "abfrageversuche": field("Abfrageversuche"),
            "zaehleradresse": field("Z&auml;hleradresse", "Zähleradresse"),
            "medium": field("Medium"),
        }

    @staticmethod
    def _extract_last_sync_timestamp(html_body: str) -> str | None:
        """Extrahiert "Letzte Synchronisation" aus dem Seiten-Footer

        (`<p id='div_timestamp'>...(Letzte Synchronisation: JJJJ-MM-TT
        HH:MM:SS)</p>`). Rein informativ fürs Logging.
        """
        match = re.search(
            r"""Letzte\s+Synchronisation:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})""",
            html_body,
            re.IGNORECASE,
        )
        return match.group(1) if match else None

    @staticmethod
    def _extract_meter_readings(html_body: str) -> list[dict[str, str | None]]:
        """Extrahiert ALLE Werte-Zeilen aus der metervalue-Tabelle.

        Jede Zeile hat die Form <tr id='table_metervalues_lineN'>...</tr>
        und enthält ihre eigenen Spalten mit denselben (technisch nicht
        eindeutigen) IDs wie jede andere Zeile. Es wird daher pro Zeile
        extrahiert (Suche innerhalb des Zeilen-Ausschnitts), nicht global
        über das gesamte Dokument.
        """
        readings = []
        for row_match in re.finditer(
            r"""<tr\b[^>]*\bid=["']table_metervalues_line\d+["'][^>]*>[\s\S]*?</tr>""",
            html_body,
            re.IGNORECASE,
        ):
            row_html = row_match.group(0)
            obis = PPCSmgwClient._extract_by_id(row_html, "table_metervalues_col_obis")
            if not obis:
                continue
            sign_value = PPCSmgwClient._extract_by_id(row_html, "table_metervalues_col_sign")
            readings.append(
                {
                    "obis": obis,
                    "value": PPCSmgwClient._extract_by_id(row_html, "table_metervalues_col_wert"),
                    "unit": PPCSmgwClient._extract_by_id(row_html, "table_metervalues_col_einheit"),
                    "timestamp": PPCSmgwClient._extract_by_id(
                        row_html, "table_metervalues_col_timestamp"
                    ),
                    "isvalid": PPCSmgwClient._extract_by_id(
                        row_html, "table_metervalues_col_istvalide"
                    ),
                    "name": PPCSmgwClient._extract_by_id(row_html, "table_metervalues_col_name"),
                    "signiert": "Ja" if sign_value else "Nein",
                }
            )

        # Manche Firmware-Versionen tragen den Zeitstempel nur in der ERSTEN
        # Zeile ein (z.B. 1.8.0 Bezug) und lassen ihn bei nachfolgenden
        # Zeilen derselben Ablesung (z.B. 2.8.0 Einspeisung) leer, weil er
        # identisch wäre. Fehlt er, wird er von der vorherigen Zeile
        # übernommen (reine Anzeige-/Vollständigkeits-Korrektur, ändert
        # NICHT den gelesenen Wert selbst).
        previous_timestamp: str | None = None
        for reading in readings:
            if not reading.get("timestamp"):
                reading["timestamp"] = previous_timestamp
            else:
                previous_timestamp = reading["timestamp"]
        return readings

    async def list_tariff_profiles(self, token: str) -> list[dict[str, str]]:
        """Liefert die Liste der Auswertungsprofile (z.B. "Bezug 15-Minuten",

        "Bezug Monat") des Gateways. Jeder Eintrag enthält "label" (stabiler
        Profilname) und "tid" (nur für den aktuellen Request-Zyklus gültige
        Session-ID - rotiert wie die "mid" der Zähler bei jedem Login neu).
        """
        post_data = f"tkn={token}&action=tariffform"
        status, _headers, body = await self._raw_request(
            "POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=post_data,
        )
        if status != 200:
            raise PPCSmgwConnectionError(
                f"Auswertungsprofile konnten nicht geladen werden (HTTP {status})."
            )
        select_match = re.search(
            r"""id=["']tarifform_select_profile["'][\s\S]*?</select>""",
            body,
            re.IGNORECASE,
        )
        if not select_match:
            return []
        profiles = []
        for match in re.finditer(
            r"""<option\b[^>]*\bvalue=["']([^"']*)["'][^>]*>([^<]*)<""",
            select_match.group(0),
            re.IGNORECASE,
        ):
            label = html.unescape(match.group(2).strip())
            profiles.append({"label": label, "tid": match.group(1)})
        return profiles

    async def get_tariff_profile_value(self, token: str, tid: str) -> dict[str, str | None]:
        """Liefert ALLE Konfigurationsdaten eines Auswertungsprofils.

        WICHTIG: Die Auswertungsprofil-Seite (showTariffProfile) enthält
        KEINE Messwert-Tabelle - sondern ausschließlich Konfigurationsdaten:
        welche Messgröße verarbeitet wird, an wen sie zugestellt wird, und
        das "Basisprofil" mit Gültigkeitszeitraum (siehe PPC-Handbuch
        Kapitel 4.5). Diese Methode liefert daher keinen "value" im
        klassischen Sinn, sondern alle diese Konfigurationsfelder - inkl.
        einer Prüfung, ob das Profil bereits ABGELAUFEN ist (Ende
        Validierungsperiode in der Vergangenheit).

        `tid` MUSS aus einem list_tariff_profiles()-Aufruf STAMMEN, DER MIT
        DEMSELBEN `token` GEMACHT WURDE (tid ist wie die Zähler-"mid" an die
        aktuelle Session gebunden).
        """
        post_data = f"tkn={token}&action=showTariffProfile&tid={tid}"
        status, _headers, body = await self._raw_request(
            "POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=post_data,
        )
        if status != 200:
            raise PPCSmgwConnectionError(
                f"Auswertungsprofil konnte nicht geladen werden (HTTP {status})."
            )

        def field(*labels: str) -> str | None:
            for label in labels:
                result = PPCSmgwClient._extract_meter_info_field(body, label)
                if result is not None:
                    return result
            return None

        ende_validierung = field("Ende Validierungsperiode")
        abgelaufen: bool | None = None
        if ende_validierung and ende_validierung.strip().lower() != "unbegrenzt":
            try:
                ende_dt = datetime.strptime(ende_validierung.strip(), "%Y-%m-%d %H:%M:%S")
                abgelaufen = ende_dt < datetime.now()
            except ValueError:
                abgelaufen = None
        elif ende_validierung and ende_validierung.strip().lower() == "unbegrenzt":
            abgelaufen = False

        return {
            # Kein "value" im klassischen Sinn (siehe Docstring) - Sensor
            # zeigt daher "unbekannt", aber alle Attribute sind gefüllt.
            "value": None,
            "unit": None,
            "profil_id": field("Profil-ID"),
            "profilname": field("Profilname"),
            "taf_typ": field("TAF-Typ"),
            "registerperiode_sekunden": field("Registerperiode (sek.)"),
            "abrechnungsperiode_sekunden": field("Abrechnungsperiode (sek.)"),
            "vorhaltezeit_tage": field("Vorhaltezeit (Tage)"),
            "beginn_validierungsperiode": field("Beginn Validierungsperiode"),
            "ende_validierungsperiode": ende_validierung,
            "abgelaufen": abgelaufen,
            "alias": field("Alias"),
            "zaehlpunktbezeichnung": field("Z&auml;hlpunktbezeichnung", "Zählpunktbezeichnung"),
            "tarifstufen": field("Tarifstufen"),
            "tag_beginn": field("Tag Beginn"),
            "obis": PPCSmgwClient._extract_by_id(body, "table_tafvalues_col_obis"),
            "messgroesse": PPCSmgwClient._extract_by_id(body, "table_tafvalues_col_messgroesse"),
        }
