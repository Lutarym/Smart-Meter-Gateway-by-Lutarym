# Integrationsversion: 2.4.5
"""Button-Plattform für die PPC Smart Meter Gateway Integration.

Stellt einen "Gateway neu starten"-Button bereit (action=selftest). In der
Praxis hat sich gezeigt, dass ein hängendes HAN-Register (bei dem "Letzte
Synchronisation" nicht mehr fortschreitet und Sensoren dadurch eingefroren
wirken) sich damit zuverlässig wieder zum Laufen bringen lässt, ohne dass
der Netzbetreiber eingreifen muss.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PPCSmgwCoordinator, build_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Legt den "Gateway neu starten"-Button für einen Config Entry an."""
    coordinator: PPCSmgwCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PPCSmgwRestartButton(coordinator, entry)])


class PPCSmgwRestartButton(CoordinatorEntity[PPCSmgwCoordinator], ButtonEntity):
    """Löst einen Neustart/Selbsttest des Gateways aus.

    Nützlich, wenn Sensoren eingefroren wirken (Zeitstempel bewegt sich
    nicht mehr) - das betrifft nur die lokale HAN-Schnittstelle des
    Gateways, nicht den Zähler selbst oder die Backend-Anbindung beim
    Netzbetreiber.
    """

    _attr_has_entity_name = True
    _attr_name = "Gateway neu starten"
    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(self, coordinator: PPCSmgwCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_restart_gateway"
        self._attr_device_info = build_device_info(coordinator, entry)

    async def async_press(self) -> None:
        _LOGGER.info("SMGW: Neustart/Selbsttest wird ausgelöst (Button gedrückt).")
        token = await self.coordinator.client.login()
        await self.coordinator.client.selftest(token)
        # Bewusst kein logout() - siehe Docstring von PPCSmgwClient.selftest().
