"""Config flow for FrontDeskAgent integration."""

from __future__ import annotations

from homeassistant import config_entries

from .const import DOMAIN


class FrontDeskAgentConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for FrontDeskAgent."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title="FrontDeskAgent", data={})
