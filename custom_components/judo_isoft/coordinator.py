"""DataUpdateCoordinator for judo_isoft."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    JudoISoftApiClientAuthenticationError,
    JudoISoftApiClientError,
)

if TYPE_CHECKING:
    from .data import JudoISoftConfigEntry


# https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
class JudoISoftDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    config_entry: JudoISoftConfigEntry

    async def _async_update_data(self) -> Any:
        """Update data via library."""
        try:
            regular_poll = self.data is not None
            return await self.config_entry.runtime_data.client.async_get_data(
                include_extended=regular_poll,
                local_date=(lambda: dt_util.now().date()) if regular_poll else None,
            )
        except JudoISoftApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(exception) from exception
        except JudoISoftApiClientError as exception:
            raise UpdateFailed(exception) from exception
