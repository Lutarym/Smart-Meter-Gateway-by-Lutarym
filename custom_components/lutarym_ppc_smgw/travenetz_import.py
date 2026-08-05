# Integrationsversion: 2.4.5
"""1:1-Import einer TraveNetz/iMSys-CSV-Exportdatei (stündliche

"Energie bezogen"-Werte) in die Langzeit-Statistik dieser Integration.

Anders als history_import.py (das eine ungenaue Quell-Entity über zwei
Ankerpunkte skaliert) übernimmt dieses Modul echte, vom Netzbetreiber
gelieferte Messwerte UNVERÄNDERT - keine Interpolation, keine Skalierung,
keine andere Entity nötig. Einzige Ausnahme: einzelne, vereinzelt
fehlende Stunden INNERHALB des Datenbereichs (Status "F"/"-" in der
Exportdatei) werden linear zwischen den beiden benachbarten echten Werten
interpoliert, damit die Reihe lückenlos bleibt - das ist keine Schätzung
der GRÖSSENORDNUNG, nur ein Lückenschluss zwischen zwei bekannten Punkten.

Erwartetes CSV-Format (TraveNetz-Kundenportal-Export):
    ;;"<Zählernummer> / Energie bezogen (stündlich)";"";"";
    "Uhrzeit von - (in Lokalzeit)";"Uhrzeit - bis (in Lokalzeit)";"Wert";"Einheit";"Status";
    "27.11.2025 - 00:00:00";"27.11.2025 - 01:00:00";"0,489460";"kW";"W";
    ...
Werte sind trotz Einheit "kW" tatsächlich kWh für die jeweilige volle
Stunde (Momentanleistungs-Mittelwert × 1h = Energie dieser Stunde).
Zeitstempel sind deutsche Lokalzeit (Europe/Berlin, inkl. Zeitumstellung)
und werden hier korrekt nach UTC konvertiert.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import HomeAssistant

from .history_import import HistoryImportError

_LOGGER = logging.getLogger(__name__)

_BERLIN = ZoneInfo("Europe/Berlin")
_UTC = ZoneInfo("UTC")


def _parse_value(raw: str) -> float | None:
    raw = raw.strip()
    if raw in ("-", ""):
        return None
    return float(raw.replace(",", "."))


def _parse_travenetz_csv_sync(path: str) -> list[tuple[datetime, float]]:
    """Blockierendes Datei-Parsing - MUSS im Executor laufen (siehe

    import_csv_history), nicht direkt im Event-Loop.

    WICHTIG zur Einheit: Der TraveNetz-Export liefert in Spalte 3 die
    mittlere LEISTUNG des Intervalls in kW (Spaltenkopf "Einheit" = "kW"),
    NICHT bereits die Energie in kWh. Die Energie eines Intervalls ergibt
    sich physikalisch als Leistung x Dauer:

        kWh = kW x Intervalldauer_in_Stunden

    Die Intervalldauer wird aus den Spalten "von" (r[0]) und "bis" (r[1])
    berechnet, statt sie anzunehmen - dadurch funktioniert der Import
    unverändert für den Tages-Export (24 h pro Zeile) UND für einen
    15-Minuten-Export (0,25 h pro Zeile), ohne Code-Änderung. Jede Zeile
    wird als (Startzeitpunkt_UTC, Energie_kWh) zurückgegeben.
    """
    rows: list[tuple[datetime, float]] = []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f, delimiter=";")
            for i, r in enumerate(reader):
                if i < 2 or len(r) < 5:
                    continue  # Kopfzeilen / leere/kurze Zeilen überspringen
                power_kw = _parse_value(r[2])
                if power_kw is None:
                    continue  # "-" (Status F) - Lücke, wird unten interpoliert
                try:
                    start_local_naive = datetime.strptime(
                        r[0].strip(), "%d.%m.%Y - %H:%M:%S"
                    )
                    end_local_naive = datetime.strptime(
                        r[1].strip(), "%d.%m.%Y - %H:%M:%S"
                    )
                except ValueError:
                    continue
                # Intervalldauer in Stunden aus von/bis. Fällt auf 24 h
                # zurück, falls bis <= von (defekte Zeile) - der
                # Tages-Export ist der Normalfall.
                duration_h = (end_local_naive - start_local_naive).total_seconds() / 3600.0
                if duration_h <= 0:
                    duration_h = 24.0
                energy_kwh = power_kw * duration_h
                start_local = start_local_naive.replace(tzinfo=_BERLIN)
                rows.append((start_local.astimezone(_UTC), energy_kwh))
    except OSError as err:
        raise HistoryImportError(
            f"CSV-Datei '{path}' konnte nicht gelesen werden: {err}"
        ) from err

    if not rows:
        raise HistoryImportError(
            f"CSV-Datei '{path}' enthält keine verwertbaren Datenzeilen "
            "(erwartetes Format: TraveNetz-Kundenportal-Export, siehe Moduldocstring)."
        )
    rows.sort(key=lambda item: item[0])
    return rows


def _median_step(timestamps: list[datetime]) -> timedelta:
    """Ermittelt den typischen Abstand zwischen aufeinanderfolgenden

    Datenpunkten (Median der Differenzen) - so passt sich das Raster
    automatisch an Tages-, Stunden- oder 15-Minuten-Export an, statt eine
    feste Stunde anzunehmen. Fällt auf 1 Tag zurück, wenn sich kein
    Abstand bestimmen lässt.
    """
    if len(timestamps) < 2:
        return timedelta(days=1)
    diffs = sorted(
        (timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)),
        key=lambda d: d.total_seconds(),
    )
    mid = diffs[len(diffs) // 2]
    return mid if mid.total_seconds() > 0 else timedelta(days=1)


def _fill_internal_gaps(rows: list[tuple[datetime, float]]) -> dict[datetime, float]:
    """Baut ein lückenloses Raster zwischen dem ersten und letzten echten

    Datenpunkt - einzelne fehlende Intervalle DAZWISCHEN (Status "F")
    werden linear zwischen den beiden umgebenden echten Werten
    interpoliert. Der Rasterabstand wird aus den Daten selbst abgeleitet
    (Median der Zeilenabstände), damit Tages-, Stunden- und
    15-Minuten-Exporte gleichermassen korrekt behandelt werden. Lücken vor
    dem ersten bzw. nach dem letzten echten Punkt werden NICHT erzeugt (das
    wäre Extrapolation, nicht Lückenschluss).
    """
    by_ts = dict(rows)
    timestamps_sorted = sorted(by_ts)
    step = _median_step(timestamps_sorted)
    first_ts, last_ts = timestamps_sorted[0], timestamps_sorted[-1]

    filled: dict[datetime, float] = {}
    cur = first_ts
    pending_gap_start: datetime | None = None
    # Toleranz: Zeitstempel muss nur nahe am Raster liegen (DST-Sprünge,
    # kleine Rundungen), exakter Treffer wird über die nächstgelegene
    # echte Zeit gesucht.
    while cur <= last_ts + step / 2:
        match = None
        for ts in by_ts:
            if abs((ts - cur).total_seconds()) < step.total_seconds() / 2:
                match = ts
                break
        if match is not None:
            if pending_gap_start is not None:
                gap_slots = filled_pending_range(pending_gap_start, cur, step)
                before_val = filled.get(pending_gap_start - step, 0.0)
                after_val = by_ts[match]
                n = len(gap_slots) + 1
                for i, gts in enumerate(gap_slots, start=1):
                    filled[gts] = before_val + (after_val - before_val) * (i / n)
                pending_gap_start = None
            filled[cur] = by_ts[match]
        else:
            if pending_gap_start is None:
                pending_gap_start = cur
        cur += step

    return filled


def filled_pending_range(
    start: datetime, stop_exclusive: datetime, step: timedelta = timedelta(hours=1)
) -> list[datetime]:
    out = []
    cur = start
    while cur < stop_exclusive:
        out.append(cur)
        cur += step
    return out


async def import_csv_history(
    hass: HomeAssistant,
    *,
    target_statistic_id: str,
    target_name: str,
    csv_path: str,
    start_value_kwh: float = 0.0,
    extend_to_now_value_kwh: float | None = None,
    anchor_end_value_kwh: float | None = None,
    dry_run: bool = False,
) -> dict:
    """Liest eine TraveNetz-CSV-Exportdatei und schreibt die Werte in die

    Langzeit-Statistik der Ziel-Entity. Kein Skalieren, keine andere
    Entity - reine Übernahme echter Messwerte.

    Zwei Kumulierungs-Modi:

    * VORWÄRTS (Standard, anchor_end_value_kwh is None): Die Reihe beginnt
      bei start_value_kwh und addiert Zeile für Zeile die Deltas auf. Der
      ANFANG ist damit fixiert - problematisch, wenn unklar ist, welcher
      realen Zeit die erste CSV-Zeile entspricht (dann verschiebt sich die
      ganze Kurve).

    * RÜCKWÄRTS (anchor_end_value_kwh gesetzt): Der verlässliche ENDwert
      (z.B. der aktuelle HAN-Live-Zählerstand von 1-0:1.8.0) wird auf die
      LETZTE CSV-Zeile gelegt und von dort Delta für Delta rückwärts
      abgezogen. Der Startwert ergibt sich rechnerisch und wird NICHT mehr
      vorgegeben. Das ist robuster, weil der Zählerendstand die sichere
      Größe ist, nicht der Aufzeichnungsbeginn. start_value_kwh wird in
      diesem Modus ignoriert; extend_to_now_value_kwh ebenfalls (der Anker
      IST bereits der aktuelle Wert - es gibt nichts mehr zu überbrücken).

    WICHTIG (nur VORWÄRTS-Modus): Falls extend_to_now_value_kwh angegeben
    ist, wird die Reihe zusätzlich vom letzten CSV-Zeitpunkt bis zur
    aktuellen Stunde linear aufgefüllt und überschreibt dabei alte, evtl.
    noch vorhandene Statistik-Einträge in diesem Fenster.
    """
    rows = await hass.async_add_executor_job(_parse_travenetz_csv_sync, csv_path)
    filled = _fill_internal_gaps(rows)
    timestamps = sorted(filled)

    stats: list[StatisticData] = []
    month_summary: dict[str, float] = {}

    if anchor_end_value_kwh is not None:
        # RÜCKWÄRTS: letzte CSV-Zeile trägt exakt den Ankerwert, davor wird
        # Delta für Delta abgezogen. cumulative[i] = anchor - sum(delta[j]
        # für j > i). Das delta EINER Zeile ist der Zuwachs, der zu DIESER
        # Zeile hin passiert ist, bleibt also in ihrem Stand enthalten.
        total_energy = sum(filled.values())
        implied_start = anchor_end_value_kwh - total_energy
        if implied_start < 0:
            # Allgemeiner Plausibilitätsfall (nicht nutzerspezifisch): Die
            # Summe der CSV-Energie ist größer als der als Anker gesetzte
            # aktuelle Zählerstand. Rückwärts gerechnet würde der Beginn
            # negativ - ein Zählerstand kann nicht unter 0 liegen. Ursache
            # ist meist ein Zählerwechsel im CSV-Zeitraum oder ein zu
            # niedriger/fehlerhafter Live-Wert. Klarer Fehler statt
            # stillschweigend unsinniger (negativer) Stände.
            raise HistoryImportError(
                f"Rückwärts-Import nicht möglich: Die CSV summiert sich auf "
                f"{total_energy:.1f} kWh, der aktuelle Zählerstand (Anker) ist "
                f"aber nur {anchor_end_value_kwh:.1f} kWh. Der Reihenbeginn würde "
                f"dadurch negativ ({implied_start:.1f} kWh). Das deutet auf einen "
                "Zählerwechsel im Exportzeitraum oder einen zu kurzen/fehlerhaften "
                "aktuellen Zählerstand hin. Bitte einen kürzeren CSV-Zeitraum "
                "wählen oder den Zählerstand prüfen."
            )
        cumulative = anchor_end_value_kwh
        stats_reversed: list[StatisticData] = []
        for ts in reversed(timestamps):
            stats_reversed.append(
                StatisticData(
                    start=ts, state=round(cumulative, 4), sum=round(cumulative, 4)
                )
            )
            key = f"{ts.year:04d}-{ts.month:02d}"
            month_summary[key] = month_summary.get(key, 0.0) + filled[ts]
            # Für die VORHERIGE (ältere) Zeile den Zuwachs dieser Zeile
            # abziehen.
            cumulative -= filled[ts]
        stats = list(reversed(stats_reversed))
        computed_start_value = round(cumulative + filled[timestamps[0]], 4) if timestamps else anchor_end_value_kwh
        # Hinweis: cumulative ist jetzt (anchor - Summe ALLER Deltas) =
        # der Stand VOR der ersten Zeile. Der "Startwert" im Sinne des
        # Standes AN der ersten Zeile ist computed_start_value.
        cumulative_final = anchor_end_value_kwh
    else:
        # VORWÄRTS (unverändertes bisheriges Verhalten).
        cumulative = start_value_kwh
        for ts in timestamps:
            delta = filled[ts]
            cumulative += delta
            stats.append(
                StatisticData(start=ts, state=round(cumulative, 4), sum=round(cumulative, 4))
            )
            key = f"{ts.year:04d}-{ts.month:02d}"
            month_summary[key] = month_summary.get(key, 0.0) + delta
        computed_start_value = start_value_kwh
        cumulative_final = cumulative

    bridged_hours = 0
    bridge_skipped_reason: str | None = None
    if extend_to_now_value_kwh is not None and anchor_end_value_kwh is None:
        last_ts = timestamps[-1]
        now_hour = datetime.now(tz=last_ts.tzinfo).replace(minute=0, second=0, microsecond=0)
        bridge_slots = []
        cur = last_ts + timedelta(hours=1)
        while cur <= now_hour:
            bridge_slots.append(cur)
            cur += timedelta(hours=1)
        if bridge_slots:
            base = cumulative  # CSV-Endstand, bevor die Brücke beginnt
            span = extend_to_now_value_kwh - base
            n = len(bridge_slots)
            implied_hourly_rate = span / n

            # Plausibilitätsprüfung: der aktuelle Live-Wert ist ein EINZELNER
            # Schnappschuss vom HAN-Interface - bei einem Auslesefehler/
            # Glitch würde er blind einen falschen Sprung (oder Einbruch!)
            # festschreiben. Drei Fälle werden unterschieden:
            #  a) span nur LEICHT negativ (< 1 kWh): typisches Mess-Rauschen
            #     beim Reconnect nach einer Verbindungsstörung, kein echter
            #     Reset - wird auf 0 geklemmt (flache Fortsetzung) statt
            #     die ganze Brücke zu verwerfen.
            #  b) span DEUTLICH negativ (>= 1 kWh): für einen
            #     total_increasing-Zähler nicht plausibel, wird blockiert.
            #  c) span positiv, aber unrealistisch hoch (siehe unten).
            NEGATIVE_NOISE_TOLERANCE_KWH = 1.0
            real_hourly_values = list(filled.values())
            max_real_hourly = max(real_hourly_values) if real_hourly_values else 0.0
            suspicious_threshold = max(max_real_hourly * 5, 15.0)

            if -NEGATIVE_NOISE_TOLERANCE_KWH <= span < 0:
                _LOGGER.info(
                    "SMGW Historien-Import: aktueller Wert liegt %.3f kWh unter dem "
                    "CSV-Endstand - im Toleranzbereich für Mess-Rauschen, Brücke "
                    "wird flach (ohne Rückgang) fortgesetzt statt verworfen.",
                    -span,
                )
                span = 0.0
                implied_hourly_rate = 0.0

            if span < 0:
                bridge_skipped_reason = (
                    f"Aktueller Wert ({extend_to_now_value_kwh:.2f} kWh) liegt UNTER "
                    f"dem CSV-Endstand ({base:.2f} kWh) - ein Zählerstand kann nicht "
                    "sinken. Wahrscheinlich ein fehlerhafter Einzel-Messwert vom "
                    "HAN-Interface (oder eine falsche/verwaiste Entity abgefragt), "
                    "keine Brücke geschrieben - bitte den aktuellen Wert manuell prüfen."
                )
                _LOGGER.warning("SMGW Historien-Import: %s", bridge_skipped_reason)
            elif implied_hourly_rate > suspicious_threshold:
                bridge_skipped_reason = (
                    f"Sprung zu 'jetzt' wäre {implied_hourly_rate:.1f} kWh/Stunde "
                    f"({span:.1f} kWh über {n} Std.) - das ist deutlich mehr als die "
                    f"höchste je in der CSV gesehene Stundenrate "
                    f"({max_real_hourly:.2f} kWh/h) bzw. der Sicherheits-Schwellwert "
                    f"({suspicious_threshold:.1f} kWh/h). Wahrscheinlich ein "
                    "fehlerhafter Einzel-Messwert vom HAN-Interface, keine echte "
                    "Brücke geschrieben - bitte den aktuellen Wert manuell prüfen."
                )
                _LOGGER.warning("SMGW Historien-Import: %s", bridge_skipped_reason)
            else:
                for i, ts in enumerate(bridge_slots, start=1):
                    cumulative = base + span * (i / n)
                    stats.append(
                        StatisticData(
                            start=ts, state=round(cumulative, 4), sum=round(cumulative, 4)
                        )
                    )
                    key = f"{ts.year:04d}-{ts.month:02d}"
                    month_summary[key] = month_summary.get(key, 0.0) + (span / n)
                bridged_hours = n
                timestamps.append(bridge_slots[-1])

    summary = {
        "hourly_points": len(stats),
        "csv_path": csv_path,
        "first_timestamp": timestamps[0].isoformat(),
        "last_timestamp": timestamps[-1].isoformat(),
        "bridged_hours_to_now": bridged_hours,
        "bridge_skipped_reason": bridge_skipped_reason,
        "mode": "backward_from_anchor" if anchor_end_value_kwh is not None else "forward_from_start",
        "start_value_kwh": computed_start_value,
        "anchor_end_value_kwh": anchor_end_value_kwh,
        "final_computed_kwh": round(cumulative_final, 4),
        "monthly_breakdown_kwh": {k: round(v, 2) for k, v in sorted(month_summary.items())},
        "dry_run": dry_run,
    }

    if dry_run or not stats:
        return summary

    metadata = StatisticMetaData(
        has_mean=False,
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=target_name,
        source="recorder",
        statistic_id=target_statistic_id,
        unit_of_measurement="kWh",
        unit_class="energy",
    )
    # WICHTIG: async_import_statistics ist mit @callback markiert (siehe
    # homeassistant/components/recorder/statistics.py) - SYNCHRON, reiht nur
    # einen Job in die Recorder-Warteschlange ein und gibt None zurück. NICHT
    # awaiten, sonst "TypeError: 'NoneType' object can't be awaited".
    async_import_statistics(hass, metadata, stats)
    return summary
