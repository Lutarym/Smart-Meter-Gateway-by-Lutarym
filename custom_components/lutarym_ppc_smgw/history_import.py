# Integrationsversion: 2.5.1
"""Einmaliger Import einer korrigierten historischen Zeitreihe für den

OBIS 1-0:1.8.0 ("Bezug") Sensor dieser Integration - aufgerufen über den
Service `lutarym_ppc_smgw.import_history` (siehe __init__.py).

Kombiniert vier Datenquellen zu einer lückenlosen, stündlichen
Statistik-Reihe:
  1. Startanker: vom Nutzer angegebenes Datum + bekannter kWh-Stand.
  2. Endanker: der AKTUELLE Live-Wert von 1-0:1.8.0, automatisch ermittelt
     (nicht vom Nutzer abgefragt) - siehe __init__.py:
     _async_get_current_1_8_0_value.
  3. Quell-Entity: liefert die reale Tag-zu-Tag-"Form" des Verbrauchs für
     den gesamten Zeitraum (z.B. ein ungenauerer, aber lückenloser
     Alternativ-Zähler). Wo die Quell-Entity selbst Lücken hat (z.B. vor
     ihrer eigenen Installation), wird zeitanteilig interpoliert.
  4. Optionale, exakt bekannte Monatswerte (kWh) - haben für den
     jeweiligen Monat Vorrang vor der aus der Quell-Entity abgeleiteten
     Schätzung; die Quell-Entity liefert für so einen Monat weiterhin die
     Tagesform, nur die Gesamthöhe wird auf den vorgegebenen Wert fixiert.

Alle nicht fest vorgegebenen Monate teilen sich gemeinsam das nach Abzug
der festen Monate verbleibende Gesamt-Budget (End- minus Startanker),
proportional zu ihrem jeweiligen Anteil an der Quell-Entity-Kurve - nicht
gleichmässig pro Monat verteilt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass
class _MonthBudget:
    """Ein Kalendermonats-Abschnitt mit optionalem festem Ziel-kWh-Wert.

    fixed_kwh ist None, wenn für diesen Monat kein expliziter Wert
    vorgegeben wurde - dann wird der Monatsanteil aus der Quell-Entity
    abgeleitet (siehe Moduldocstring, Fall b).
    """

    start: datetime
    end: datetime
    fixed_kwh: float | None


def _month_ranges(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Zerlegt [start, end) in Kalendermonats-Abschnitte (UTC-Grenzen)."""
    ranges: list[tuple[datetime, datetime]] = []
    cur = start
    while cur < end:
        if cur.month == 12:
            next_month = cur.replace(
                year=cur.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
            )
        else:
            next_month = cur.replace(
                month=cur.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0
            )
        seg_end = min(next_month, end)
        ranges.append((cur, seg_end))
        cur = seg_end
    return ranges


async def _fetch_hourly_sum(
    hass: HomeAssistant, statistic_id: str, start: datetime, end: datetime
) -> list[tuple[datetime, float]]:
    """Holt stündliche kumulative 'sum'-Statistikpunkte einer Entity.

    Läuft im Executor, da statistics_during_period synchrone DB-Zugriffe
    macht (siehe homeassistant.components.recorder.statistics).
    """
    instance = get_instance(hass)
    result = await instance.async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end,
        {statistic_id},
        "hour",
        None,
        {"sum"},
    )
    rows = result.get(statistic_id, [])
    out: list[tuple[datetime, float]] = []
    for row in rows:
        ts = row["start"]
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts, tz=timezone.utc)
        val = row.get("sum")
        if val is not None:
            out.append((ts, float(val)))
    out.sort(key=lambda item: item[0])
    return out


def _hourly_deltas(
    points: list[tuple[datetime, float]]
) -> dict[datetime, float]:
    """Wandelt eine kumulative Punktreihe in stündliche Zuwächse um.

    Schlüssel ist der Stunden-Startzeitpunkt des jeweiligen Segments.
    Negative Zuwächse (Zähler-Reset o.ä.) werden konservativ auf 0
    begrenzt statt das Gesamt-Budget zu verfälschen.
    """
    deltas: dict[datetime, float] = {}
    for i in range(1, len(points)):
        t0, v0 = points[i - 1]
        _t1, v1 = points[i]
        deltas[t0] = max(v1 - v0, 0.0)
    return deltas


def _fill_leading_gap_with_mirror(
    source_deltas: dict[datetime, float], hour_slots: list[datetime]
) -> dict[datetime, float]:
    """Füllt eine Lücke am ANFANG der Quell-Daten (z.B. weil die Quell-

    Entity erst mitten im Zeitraum installiert wurde) mit der Form der
    unmittelbar darauffolgenden, gleich langen Periode, statt mit einer
    gleichmässigen Zeit-Interpolation - realistischer, ohne Rohdaten für
    die Lücke selbst zu erfinden. Betrifft nur eine Lücke GANZ AM ANFANG;
    Lücken mitten in der Reihe bleiben unverändert (fallen weiterhin auf
    den Platzhalter in import_history zurück).
    """
    if not hour_slots:
        return source_deltas

    first_covered = next((ts for ts in hour_slots if ts in source_deltas), None)
    if first_covered is None or first_covered == hour_slots[0]:
        return source_deltas  # keine Lücke am Anfang, oder gar keine Quelldaten

    gap_slots = [ts for ts in hour_slots if ts < first_covered]
    donor_slots = [ts for ts in hour_slots if ts >= first_covered][: len(gap_slots)]

    filled = dict(source_deltas)
    for gap_ts, donor_ts in zip(gap_slots, donor_slots):
        donor_val = source_deltas.get(donor_ts)
        if donor_val is not None:
            filled[gap_ts] = donor_val
    return filled


class HistoryImportError(Exception):
    """Fehler beim Aufbau/Import der historischen Reihe (nutzerlesbar)."""


async def import_history(
    hass: HomeAssistant,
    *,
    target_statistic_id: str,
    target_name: str,
    start_date: date,
    start_value_kwh: float,
    source_entity_id: str,
    end_value_kwh: float,
    monthly_kwh: dict[str, float] | None = None,
    dry_run: bool = False,
) -> dict:
    """Baut die korrigierte Stundenreihe und schreibt sie (ausser bei

    dry_run) über async_import_statistics in die Langzeit-Statistik der
    Ziel-Entity. Gibt eine Zusammenfassung zurück (u.a. Monats-
    Aufschlüsselung), geeignet als response_variable des Service.
    """
    monthly_kwh = monthly_kwh or {}
    tz = timezone.utc
    start_dt = datetime(start_date.year, start_date.month, start_date.day, tzinfo=tz)
    end_dt = datetime.now(tz=tz).replace(minute=0, second=0, microsecond=0)
    if end_dt <= start_dt:
        raise HistoryImportError(
            "Das Startdatum muss in der Vergangenheit liegen (Endpunkt ist immer 'jetzt')."
        )

    source_points = await _fetch_hourly_sum(hass, source_entity_id, start_dt, end_dt)
    if not source_points:
        _LOGGER.warning(
            "SMGW Historien-Import: Quell-Entity '%s' hat KEINE Statistik-Daten im "
            "Zeitraum %s bis %s - die komplette Reihe wird ohne Kurvenform (rein "
            "zeitanteilig je nicht fest vorgegebenem Monat) interpoliert.",
            source_entity_id,
            start_dt.isoformat(),
            end_dt.isoformat(),
        )
    source_deltas = _hourly_deltas(source_points)

    hour_slots: list[datetime] = []
    cur = start_dt
    while cur < end_dt:
        hour_slots.append(cur)
        cur += timedelta(hours=1)

    # Lücke vor dem ersten Quell-Datenpunkt (z.B. vor Installation der
    # Quell-Entity) mit der Form der direkt folgenden, gleich langen
    # Periode auffüllen - siehe _fill_leading_gap_with_mirror.
    source_deltas = _fill_leading_gap_with_mirror(source_deltas, hour_slots)

    month_ranges = _month_ranges(start_dt, end_dt)
    total_target = end_value_kwh - start_value_kwh
    fixed_total = 0.0
    budgets: list[_MonthBudget] = []
    for seg_start, seg_end in month_ranges:
        key = f"{seg_start.year:04d}-{seg_start.month:02d}"
        fixed = monthly_kwh.get(key)
        if fixed is not None:
            fixed_total += fixed
        budgets.append(_MonthBudget(seg_start, seg_end, fixed))
    remaining_target = total_target - fixed_total
    if remaining_target < -0.01:
        raise HistoryImportError(
            f"Die Summe der fest vorgegebenen Monatswerte ({fixed_total:.2f} kWh) ist "
            f"größer als das Gesamt-Budget zwischen Start- und Endwert "
            f"({total_target:.2f} kWh). Das würde für die übrigen Monate rechnerisch "
            "negative Verbrauchswerte ergeben - bitte monthly_kwh, start_value oder "
            "start_date prüfen."
        )

    def _budget_for(ts: datetime) -> _MonthBudget:
        for b in budgets:
            if b.start <= ts < b.end:
                return b
        return budgets[-1]

    # Rohgewicht je Stunde: reales Quell-Delta, oder 1.0 als Platzhalter in
    # Lücken (wird unten je nach Fall a/b passend normalisiert).
    raw_weights = [source_deltas.get(ts, 1.0) for ts in hour_slots]
    hour_budget = [_budget_for(ts) for ts in hour_slots]

    result_kwh = [0.0] * len(hour_slots)

    # Fall a: Monate mit festem Wert - NUR innerhalb des jeweiligen Monats
    # skalieren, damit die Tagesform der Quelle erhalten bleibt.
    for b in budgets:
        if b.fixed_kwh is None:
            continue
        idxs = [i for i, mb in enumerate(hour_budget) if mb is b]
        raw_sum = sum(raw_weights[i] for i in idxs)
        if raw_sum <= 0 or not idxs:
            per_hour = b.fixed_kwh / max(len(idxs), 1)
            for i in idxs:
                result_kwh[i] = per_hour
        else:
            factor = b.fixed_kwh / raw_sum
            for i in idxs:
                result_kwh[i] = raw_weights[i] * factor

    # Fall b: alle nicht fest vorgegebenen Monate GEMEINSAM auf das
    # verbleibende Gesamt-Budget skalieren (ein Faktor für alle zusammen -
    # verteilt den Rest weiter proportional zur echten Quellform, nicht
    # künstlich gleichmässig pro Monat).
    free_idxs = [i for i, mb in enumerate(hour_budget) if mb.fixed_kwh is None]
    free_raw_sum = sum(raw_weights[i] for i in free_idxs)
    if free_idxs:
        if free_raw_sum <= 0:
            per_hour = remaining_target / len(free_idxs)
            for i in free_idxs:
                result_kwh[i] = per_hour
        else:
            factor = remaining_target / free_raw_sum
            for i in free_idxs:
                result_kwh[i] = raw_weights[i] * factor
    elif abs(remaining_target) > 0.01:
        _LOGGER.warning(
            "SMGW Historien-Import: alle Monate haben feste Werte, deren Summe "
            "(%.2f kWh) weicht aber vom Gesamtziel (%.2f kWh) ab - Differenz "
            "%.2f kWh bleibt unberücksichtigt, der Endwert wird dadurch nicht "
            "exakt getroffen.",
            fixed_total,
            total_target,
            remaining_target,
        )

    cumulative = start_value_kwh
    stats: list[StatisticData] = []
    month_summary: dict[str, float] = {}
    for i, ts in enumerate(hour_slots):
        cumulative += result_kwh[i]
        # WICHTIG: 'state' MUSS mitgeschrieben werden, nicht nur 'sum'. Laut
        # offizieller Doku wird 'sum' "offset by the sensor's first valid
        # state" berechnet - Home Assistants eigener, laufender Statistik-
        # Compiler (der für die live weiterverfolgte 1.8.0-Entity jede
        # Stunde neu läuft) braucht das 'state' des letzten Punkts als
        # Referenz, um den nächsten Delta korrekt draufzurechnen. Ohne
        # 'state' hat er keinen Bezugspunkt und fängt faktisch bei 0 neu an
        # (genau das Symptom: Summe bricht nach dem Import scheinbar ein).
        # Da end_value_kwh der ECHTE Live-Gerätewert ist (kein synthetischer
        # Offset wie im Fronius-Fall), ist state=sum hier auch inhaltlich
        # korrekt - die importierte Reihe endet exakt auf dem echten Stand.
        stats.append(
            StatisticData(start=ts, state=round(cumulative, 4), sum=round(cumulative, 4))
        )
        key = f"{ts.year:04d}-{ts.month:02d}"
        month_summary[key] = month_summary.get(key, 0.0) + result_kwh[i]

    summary = {
        "hourly_points": len(stats),
        "start_date": start_date.isoformat(),
        "start_value_kwh": start_value_kwh,
        "end_value_kwh": end_value_kwh,
        "final_computed_kwh": round(cumulative, 4),
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
