from urllib.parse import urlsplit

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from . import DOMAIN
from .api import CannotConnect, Client, InvalidAuth


class BackupStewardFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input:
            url = user_input["url"].rstrip("/")
            parsed = urlsplit(url)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in ("", "/")
            ):
                errors["url"] = "invalid_url"
            else:
                try:
                    status = await Client(
                        async_get_clientsession(self.hass), url, user_input["token"]
                    ).status()
                    await self.async_set_unique_id(status["state"]["instance_id"])
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title="Backup Steward", data={"url": url, "token": user_input["token"]}
                    )
                except InvalidAuth:
                    errors["base"] = "invalid_auth"
                except CannotConnect, KeyError, TypeError:
                    errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("url"): str,
                    vol.Required("token"): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )
