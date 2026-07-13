"""Config flow and options (settings menu) for Gate Access."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ADMIN_ONLY,
    CONF_CLOSE_AFTER,
    CONF_CLOSE_MAP,
    CONF_DELETE_PASSWORD,
    CONF_GATE_ENTITY,
    CONF_LOG_CLOSINGS,
    CONF_LOG_PATH,
    CONF_RATE_LIMIT,
    CONF_SHOW_CLOSE,
    CONF_STATS,
    CONF_TARGETS,
    DEFAULT_ADMIN_ONLY,
    DEFAULT_CLOSE_AFTER,
    DEFAULT_DELETE_PASSWORD,
    DEFAULT_LOG_CLOSINGS,
    DEFAULT_LOG_PATH,
    DEFAULT_RATE_LIMIT,
    DEFAULT_SHOW_CLOSE,
    DEFAULT_STATS,
    DOMAIN,
    TARGET_DOMAINS,
)

_CLOSE_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0, max=3600, step=1, mode="box", unit_of_measurement="s"
    )
)
_TARGETS_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=TARGET_DOMAINS, multiple=True)
)


def _targets_schema(defaults: dict) -> vol.Schema:
    targets_default = defaults.get(CONF_TARGETS)
    if not targets_default and defaults.get(CONF_GATE_ENTITY):
        targets_default = [defaults[CONF_GATE_ENTITY]]
    return vol.Schema(
        {
            vol.Required(
                CONF_TARGETS, default=targets_default or []
            ): _TARGETS_SELECTOR,
            vol.Required(
                CONF_LOG_PATH, default=defaults.get(CONF_LOG_PATH, DEFAULT_LOG_PATH)
            ): selector.TextSelector(),
        }
    )


def _targets_of(cur: dict) -> list:
    return cur.get(CONF_TARGETS) or (
        [cur[CONF_GATE_ENTITY]] if cur.get(CONF_GATE_ENTITY) else []
    )


def _sharing_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_ADMIN_ONLY,
                default=defaults.get(CONF_ADMIN_ONLY, DEFAULT_ADMIN_ONLY),
            ): selector.BooleanSelector(),
        }
    )


def _logging_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_STATS, default=defaults.get(CONF_STATS, DEFAULT_STATS)
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_LOG_CLOSINGS,
                default=defaults.get(CONF_LOG_CLOSINGS, DEFAULT_LOG_CLOSINGS),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_RATE_LIMIT,
                default=defaults.get(CONF_RATE_LIMIT, DEFAULT_RATE_LIMIT),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=60, step=1, mode="box")
            ),
            vol.Optional(
                CONF_DELETE_PASSWORD,
                default=defaults.get(CONF_DELETE_PASSWORD, DEFAULT_DELETE_PASSWORD),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type="password")
            ),
        }
    )


class GateAccessConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial UI setup."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors = {}
        if user_input is not None:
            if not user_input.get(CONF_TARGETS):
                errors["base"] = "no_targets"
            else:
                user_input[CONF_ADMIN_ONLY] = DEFAULT_ADMIN_ONLY
                return self.async_create_entry(title="Gate Access", data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_targets_schema({}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return GateAccessOptionsFlow(config_entry)


class GateAccessOptionsFlow(OptionsFlow):
    """Settings menu: openings & log, or sharing."""

    def __init__(self, config_entry) -> None:
        self._entry = config_entry

    def _current(self) -> dict:
        return {**self._entry.data, **self._entry.options}

    def _save(self, updates: dict) -> ConfigFlowResult:
        cur = self._current()
        data = {
            CONF_TARGETS: cur.get(CONF_TARGETS)
            or ([cur[CONF_GATE_ENTITY]] if cur.get(CONF_GATE_ENTITY) else []),
            CONF_LOG_PATH: cur.get(CONF_LOG_PATH, DEFAULT_LOG_PATH),
            CONF_CLOSE_AFTER: int(cur.get(CONF_CLOSE_AFTER, DEFAULT_CLOSE_AFTER) or 0),
            CONF_CLOSE_MAP: cur.get(CONF_CLOSE_MAP, {}) or {},
            CONF_SHOW_CLOSE: cur.get(CONF_SHOW_CLOSE, DEFAULT_SHOW_CLOSE),
            CONF_ADMIN_ONLY: cur.get(CONF_ADMIN_ONLY, DEFAULT_ADMIN_ONLY),
            CONF_STATS: cur.get(CONF_STATS, DEFAULT_STATS),
            CONF_LOG_CLOSINGS: cur.get(CONF_LOG_CLOSINGS, DEFAULT_LOG_CLOSINGS),
            CONF_RATE_LIMIT: int(cur.get(CONF_RATE_LIMIT, DEFAULT_RATE_LIMIT) or 0),
            CONF_DELETE_PASSWORD: cur.get(
                CONF_DELETE_PASSWORD, DEFAULT_DELETE_PASSWORD
            ),
        }
        data.update(updates)
        if data.get(CONF_CLOSE_AFTER) is not None:
            data[CONF_CLOSE_AFTER] = int(data[CONF_CLOSE_AFTER])
        if data.get(CONF_RATE_LIMIT) is not None:
            data[CONF_RATE_LIMIT] = int(data[CONF_RATE_LIMIT])
        return self.async_create_entry(title="", data=data)

    def _autoclose_schema(self, cur: dict, targets: list) -> vol.Schema:
        cmap = cur.get(CONF_CLOSE_MAP, {}) or {}
        legacy = int(cur.get(CONF_CLOSE_AFTER, 0) or 0)
        fields: dict = {}
        for eid in targets:
            default = int(cmap.get(eid, legacy) or 0)
            fields[vol.Optional(f"close__{eid}", default=default)] = _CLOSE_SELECTOR
        fields[
            vol.Required(
                CONF_SHOW_CLOSE, default=cur.get(CONF_SHOW_CLOSE, DEFAULT_SHOW_CLOSE)
            )
        ] = selector.BooleanSelector()
        return vol.Schema(fields)

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["targets", "autoclose", "logging", "sharing"],
        )

    async def async_step_autoclose(self, user_input=None) -> ConfigFlowResult:
        cur = self._current()
        targets = _targets_of(cur)
        if user_input is not None:
            cmap = {}
            for eid in targets:
                try:
                    cmap[eid] = int(user_input.get(f"close__{eid}") or 0)
                except (TypeError, ValueError):
                    cmap[eid] = 0
            return self._save(
                {
                    CONF_CLOSE_MAP: cmap,
                    CONF_SHOW_CLOSE: bool(user_input.get(CONF_SHOW_CLOSE, False)),
                }
            )
        return self.async_show_form(
            step_id="autoclose", data_schema=self._autoclose_schema(cur, targets)
        )

    async def async_step_logging(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(user_input)
        return self.async_show_form(
            step_id="logging", data_schema=_logging_schema(self._current())
        )

    async def async_step_targets(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            if not user_input.get(CONF_TARGETS):
                errors["base"] = "no_targets"
            else:
                return self._save(user_input)
        return self.async_show_form(
            step_id="targets",
            data_schema=_targets_schema(self._current()),
            errors=errors,
        )

    async def async_step_sharing(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(user_input)
        return self.async_show_form(
            step_id="sharing", data_schema=_sharing_schema(self._current())
        )
