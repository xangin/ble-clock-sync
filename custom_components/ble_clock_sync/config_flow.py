"""Add a BLE clock, then choose when it is synchronized."""

from __future__ import annotations

import logging
import secrets
from typing import Any

import voluptuous as vol
from bluetooth_data_tools import human_readable_name
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CRON,
    CONF_SCHEDULE,
    CONF_TIME,
    CONF_WEEKDAY,
    DEFAULT_OPTIONS,
    DEFAULT_TIME,
    DEFAULT_WEEKDAY,
    DOMAIN,
    SCHEDULE_CRON,
    SCHEDULE_DAILY,
    SCHEDULE_WEEKLY,
    SCHEDULES,
    WEEKDAYS,
)
from .discovery import MODELS, candidate_reason, is_cgd1, model_from_info
from .protocols.cgd1 import CONF_AUTH_TOKEN, validate_token
from .schedule import cron_error

_LOGGER = logging.getLogger(__name__)


def _title(info: BluetoothServiceInfoBleak) -> str:
    return human_readable_name(None, info.name, info.address)


class BLEClockConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a clock found by Home Assistant Bluetooth."""

    VERSION = 1

    def __init__(self) -> None:
        """Start without a selected clock."""
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> BLEClockOptionsFlow:
        """Return the schedule options flow."""
        return BLEClockOptionsFlow()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Offer a discovered clock."""
        if not candidate_reason(discovery_info):
            return self.async_abort(reason="not_supported")
        _LOGGER.debug("Device found advertisement matcher=%s", candidate_reason(discovery_info))
        await self.async_set_unique_id(discovery_info.address.upper())
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": _title(discovery_info)}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the discovered clock."""
        assert self._discovery is not None
        if user_input is not None:
            if is_cgd1(self._discovery):
                return await self.async_step_cgd1_auth()
            return self._async_create(self._discovery)
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": _title(self._discovery)},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick one of the clocks currently in range."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            info = self._discovered.get(address) or bluetooth.async_last_service_info(
                self.hass, address, connectable=False
            )
            if info is None:
                return self.async_show_form(
                    step_id="user",
                    data_schema=vol.Schema({vol.Required(CONF_ADDRESS): str}),
                    errors={"base": "not_known"},
                )
            await self.async_set_unique_id(info.address.upper(), raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._discovery = info
            if is_cgd1(info):
                return await self.async_step_cgd1_auth()
            return self._async_create(info)

        configured = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=False):
            if info.address not in configured and candidate_reason(info):
                self._discovered[info.address] = info
        if not self._discovered:
            return self.async_show_form(
                step_id="user", data_schema=vol.Schema({vol.Required(CONF_ADDRESS): str})
            )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {address: _title(info) for address, info in self._discovered.items()}
                    )
                }
            ),
        )

    async def async_step_cgd1_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pair only after explicit user submission; existing tokens are optional."""
        assert self._discovery is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            supplied = user_input.get(CONF_AUTH_TOKEN, "").strip()
            try:
                token = validate_token(supplied).hex() if supplied else secrets.token_hex(16)
            except ValueError:
                errors[CONF_AUTH_TOKEN] = "invalid_token"
            else:
                return self._async_create(self._discovery, token)
        return self.async_show_form(
            step_id="cgd1_auth",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_AUTH_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    @callback
    def _async_create(
        self, info: BluetoothServiceInfoBleak, token: str | None = None
    ) -> ConfigFlowResult:
        data: dict[str, Any] = {CONF_ADDRESS: info.address.upper(), "model": model_from_info(info)}
        if token is not None:
            data[CONF_AUTH_TOKEN] = token
            data["credential_revision"] = 1
        return self.async_create_entry(
            title=_title(info),
            data=data,
            options={**DEFAULT_OPTIONS, "model": model_from_info(info)},
        )


class BLEClockOptionsFlow(OptionsFlow):
    """Choose a frequency first, then only the settings it needs."""

    def __init__(self) -> None:
        """Start from the saved options."""
        self._options: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose how often the clock is synchronized."""
        if user_input is not None:
            self._options = {**self.config_entry.options, **user_input}
            if user_input.get("change_auth_token"):
                self._options.pop("change_auth_token", None)
                return await self.async_step_cgd1_auth()
            self._options.pop("change_auth_token", None)
            schedule = user_input[CONF_SCHEDULE]
            if schedule == SCHEDULE_DAILY:
                return await self.async_step_daily()
            if schedule == SCHEDULE_WEEKLY:
                return await self.async_step_weekly()
            if schedule == SCHEDULE_CRON:
                return await self.async_step_cron()
            return self.async_create_entry(data=self._options)

        credential_fields: dict[Any, Any] = {}
        if (
            self.config_entry.data.get("model") == "CGD1"
            or CONF_AUTH_TOKEN in self.config_entry.data
            or getattr(getattr(self.config_entry, "runtime_data", None), "protocol", None)
            == "qingping_cgd1"
        ):
            credential_fields[vol.Optional("change_auth_token", default=False)] = bool
        current = self.config_entry.options.get(CONF_SCHEDULE, SCHEDULE_DAILY)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    **credential_fields,
                    vol.Optional(
                        "model",
                        default=self.config_entry.options.get(
                            "model", self.config_entry.data.get("model", "unknown")
                        ),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=model, label=model) for model in MODELS
                            ],
                            translation_key="model",
                        )
                    ),
                    vol.Required(CONF_SCHEDULE, default=current): SelectSelector(
                        SelectSelectorConfig(
                            options=SCHEDULES,
                            translation_key=CONF_SCHEDULE,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_cgd1_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update a pairing credential without displaying the saved token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            supplied = user_input.get(CONF_AUTH_TOKEN, "").strip()
            try:
                token = validate_token(supplied).hex() if supplied else secrets.token_hex(16)
            except ValueError:
                errors[CONF_AUTH_TOKEN] = "invalid_token"
            else:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={
                        **self.config_entry.data,
                        CONF_AUTH_TOKEN: token,
                        "credential_revision": self.config_entry.data.get("credential_revision", 0)
                        + 1,
                    },
                )
                # Token-only change: preserve the existing schedule in this operation.
                return self.async_create_entry(data=dict(self.config_entry.options))
        return self.async_show_form(
            step_id="cgd1_auth",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_AUTH_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_daily(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose the time of the daily sync."""
        if user_input is not None:
            return self.async_create_entry(data={**self._options, **user_input})
        return self.async_show_form(
            step_id="daily",
            data_schema=vol.Schema({vol.Required(CONF_TIME, default=self._time()): TimeSelector()}),
        )

    async def async_step_weekly(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose the day and time of the weekly sync."""
        if user_input is not None:
            return self.async_create_entry(data={**self._options, **user_input})
        return self.async_show_form(
            step_id="weekly",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_WEEKDAY, default=self._options.get(CONF_WEEKDAY, DEFAULT_WEEKDAY)
                    ): SelectSelector(
                        SelectSelectorConfig(options=list(WEEKDAYS), translation_key=CONF_WEEKDAY)
                    ),
                    vol.Required(CONF_TIME, default=self._time()): TimeSelector(),
                }
            ),
        )

    async def async_step_cron(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Enter a cron expression."""
        errors: dict[str, str] = {}
        if user_input is not None:
            expression = user_input[CONF_CRON].strip()
            if error := cron_error(expression, dt_util.now()):
                errors[CONF_CRON] = error
            else:
                return self.async_create_entry(data={**self._options, CONF_CRON: expression})
        return self.async_show_form(
            step_id="cron",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CRON, default=self._options.get(CONF_CRON, "0 4 * * *")
                    ): TextSelector()
                }
            ),
            errors=errors,
        )

    def _time(self) -> str:
        return self._options.get(CONF_TIME, DEFAULT_TIME)
