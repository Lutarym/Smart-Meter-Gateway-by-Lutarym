# Integrationsversion: 2.4.7
"""Repariert einen Statistik-Reset (sum auf 0 gefallen, state aber

korrekt weitergelaufen) UND füllt eine davorliegende echte Lücke
(gar keine Datenpunkte, z.B. durch eine Verbindungsstörung) linear auf.

Zwei Teile, in einem Rutsch:
  1. Lücke (letzter guter Punkt bis zum ersten "kaputten" Punkt): keine
     echten Daten vorhanden - wird linear zwischen den beiden bekannten
     Randwerten interpoliert (wie bei travenetz_import.py, aber hier für
     eine reine Zeit-Lücke statt für eine Quell-Entity ohne Abdeckung).
  2. Bereits vorhandene, aber falsch referenzierte Punkte (fälschlich bei
     0 gestartet): werden EXAKT um den richtigen Offset (letzter gültiger
     Stand vor der Lücke) verschoben - keine Schätzung, da die relativen
     Zuwächse seit dem Reset bereits real und korrekt aufgezeichnet sind.

WICHTIG (siehe Empfehlung an den Nutzer in der Service-Beschreibung):
Vor dem Ausführen sollte der Recorder pausiert werden
(Aktion "Recorder: Deaktivieren"), damit während der Korrektur kein
neuer, live geschriebener Punkt für dieselbe Stunde dazwischenfunkt -
danach wieder aktivieren.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import HomeAssistant

from .history_import import HistoryImportError, _fetch_hourly_sum

_LOGGER = logging.getLogger(__name__)


async def repair_erroneous_ramp(
    hass: HomeAssistant,
    *,
    target_statistic_id: str,
    target_name: str,
    ramp_start: datetime,
    ramp_end: datetime,
    dry_run: bool = False,
) -> dict:
    """Entfernt einen fälschlich eingefügten LINEAREN ANSTIEG zwischen

    `ramp_start` und `ramp_end` (z.B. durch eine "Brücke bis jetzt" mit
    einem falschen, zu hohen Zielwert - typisches Muster: über N Stunden
    hinweg jede Stunde um denselben Betrag steigend, bis ein Plateau
    erreicht wird, das deutlich über dem plausiblen Wert liegt).

    Vorgehen:
      1. Letzter guter Punkt VOR `ramp_start` = Referenzwert.
      2. Erster Punkt BEI/NACH `ramp_end` = Plateau-Wert - die Differenz
         zum Referenzwert ist der fälschlich hinzugekommene Überschuss.
      3. Punkte WÄHREND der Rampe werden verworfen und durch eine flache
         Fortführung vom Referenzwert ersetzt (keine Schätzung möglich,
         da die echten Werte durch die Rampe überschrieben wurden - siehe
         Docstring-Hinweis in history_import.py zu Lücken-Interpolation:
         gleiches Prinzip, konservativ statt erfunden hoch).
      4. Alle Punkte AB `ramp_end` werden um den Überschuss nach UNTEN
         korrigiert (Gegenstück zu repair_statistics_reset, das nach OBEN
         korrigiert).
    """
    if ramp_start.tzinfo is None:
        ramp_start = ramp_start.replace(tzinfo=timezone.utc)
    if ramp_end.tzinfo is None:
        ramp_end = ramp_end.replace(tzinfo=timezone.utc)
    if ramp_end <= ramp_start:
        raise HistoryImportError("'ramp_end' muss nach 'ramp_start' liegen.")

    lookback_start = ramp_start - timedelta(hours=48)
    now = datetime.now(tz=timezone.utc)
    points = await _fetch_hourly_sum(hass, target_statistic_id, lookback_start, now)

    before = [(t, v) for t, v in points if t < ramp_start]
    during = [(t, v) for t, v in points if ramp_start <= t < ramp_end]
    at_or_after_end = [(t, v) for t, v in points if t >= ramp_end]

    if not before:
        raise HistoryImportError(
            f"Kein gültiger Statistik-Punkt vor {ramp_start.isoformat()} gefunden."
        )
    if not at_or_after_end:
        raise HistoryImportError(
            f"Kein Statistik-Punkt bei/nach {ramp_end.isoformat()} gefunden - "
            "'ramp_end' prüfen."
        )

    good_val = before[-1][1]
    plateau_val = at_or_after_end[0][1]
    excess = plateau_val - good_val

    if excess <= 0:
        raise HistoryImportError(
            f"Wert bei ramp_end ({plateau_val:.2f} kWh) liegt nicht über dem "
            f"Referenzwert vor ramp_start ({good_val:.2f} kWh) - kein Überschuss "
            "zum Entfernen gefunden. 'ramp_start'/'ramp_end' prüfen."
        )

    corrected: list[tuple[datetime, float]] = []

    # Während der Rampe: flache Fortführung vom Referenzwert (die echten
    # Werte sind durch die fälschliche Rampe überschrieben und nicht mehr
    # rekonstruierbar - konservativ bei good_val bleiben, statt zu raten).
    for t, _v in during:
        corrected.append((t, good_val))

    # Ab ramp_end: Überschuss dauerhaft abziehen.
    for t, v in at_or_after_end:
        corrected.append((t, round(v - excess, 4)))

    stats: list[StatisticData] = [
        StatisticData(start=t, state=v, sum=v) for t, v in corrected
    ]

    summary = {
        "hourly_points_flattened": len(during),
        "hourly_points_corrected": len(at_or_after_end),
        "excess_removed_kwh": round(excess, 4),
        "reference_value_kwh": good_val,
        "reference_timestamp": before[-1][0].isoformat(),
        "plateau_value_before_repair": plateau_val,
        "value_after_repair": corrected[-1][1] if corrected else None,
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
    async_import_statistics(hass, metadata, stats)
    return summary


async def repair_statistics_reset(
    hass: HomeAssistant,
    *,
    target_statistic_id: str,
    target_name: str,
    since: datetime,
    dry_run: bool = False,
) -> dict:
    """Füllt die Lücke vor `since` linear auf UND korrigiert alle bereits

    vorhandenen Punkte AB `since` um den exakten Offset (letzter gültiger
    Punkt VOR der Lücke) - ergibt einen durchgängigen, sauber ansteigenden
    Verlauf ohne Zeitloch und ohne Sprung.
    """
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    lookback_start = since - timedelta(hours=48)
    now = datetime.now(tz=timezone.utc)
    points = await _fetch_hourly_sum(hass, target_statistic_id, lookback_start, now)

    before = [(t, v) for t, v in points if t < since]
    broken = [(t, v) for t, v in points if t >= since]

    if not before:
        raise HistoryImportError(
            f"Kein gültiger Statistik-Punkt vor {since.isoformat()} gefunden - "
            "Offset kann nicht ermittelt werden. Bitte 'since' prüfen oder einen "
            "früheren Zeitpunkt angeben."
        )
    if not broken:
        raise HistoryImportError(
            f"Keine Statistik-Punkte ab {since.isoformat()} gefunden - nichts zu "
            "reparieren. Bitte 'since' prüfen."
        )

    last_good_ts, last_good_val = before[-1]
    offset = last_good_val
    first_broken_ts = broken[0][0]

    # Teil 1: bereits vorhandene "kaputte" Punkte um den Offset verschieben.
    corrected: list[tuple[datetime, float]] = [
        (ts, round(v + offset, 4)) for ts, v in broken
    ]

    # Teil 2: echte Zeit-Lücke zwischen letztem gutem Punkt und dem ersten
    # (jetzt korrigierten) Punkt linear auffüllen - nur falls dazwischen
    # tatsächlich Stunden OHNE jeden Eintrag liegen.
    gap_slots: list[datetime] = []
    cur = last_good_ts + timedelta(hours=1)
    while cur < first_broken_ts:
        gap_slots.append(cur)
        cur += timedelta(hours=1)

    interpolated: list[tuple[datetime, float]] = []
    if gap_slots:
        target_val = corrected[0][1]
        span = target_val - last_good_val
        n = len(gap_slots) + 1  # +1: der Schritt bis zum ersten korrigierten Punkt zählt mit
        for i, ts in enumerate(gap_slots, start=1):
            interpolated.append((ts, round(last_good_val + span * (i / n), 4)))

    all_points = sorted(interpolated + corrected, key=lambda item: item[0])
    stats: list[StatisticData] = [
        StatisticData(start=ts, state=v, sum=v) for ts, v in all_points
    ]

    summary = {
        "hourly_points_corrected": len(corrected),
        "hourly_points_interpolated": len(interpolated),
        "offset_kwh": offset,
        "offset_reference_timestamp": last_good_ts.isoformat(),
        "gap_filled_from": interpolated[0][0].isoformat() if interpolated else None,
        "gap_filled_to": interpolated[-1][0].isoformat() if interpolated else None,
        "repaired_from": broken[0][0].isoformat(),
        "repaired_to": broken[-1][0].isoformat(),
        "value_before_repair": broken[-1][1],
        "value_after_repair": stats[-1]["sum"] if stats else None,
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
    # WICHTIG: async_import_statistics ist @callback (synchron), siehe
    # travenetz_import.py/history_import.py für Details - nicht awaiten.
    async_import_statistics(hass, metadata, stats)
    return summary

