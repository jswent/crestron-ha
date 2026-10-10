"""Home Assistant lifecycle and mapping callbacks for one XSIG bridge."""

import asyncio
import logging
from copy import deepcopy

import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import Context, callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import TrackTemplate, async_track_template_result
from homeassistant.helpers.script import Script
from homeassistant.helpers.template import Template

from .const import DOMAIN, EVENT_JOIN_CHANGED
from .xsig import XSigServer

_LOGGER = logging.getLogger(__name__)


class _ActionLogger(logging.LoggerAdapter):
    """Keep HA's script error/step logging from revealing rendered user data."""

    def log(self, level, msg, *args, **kwargs):
        if level >= logging.WARNING:
            self.logger.log(
                level,
                "An action runtime warning occurred on bridge %s (details omitted)",
                self.extra["entry_id"],
            )


class Bridge(XSigServer):
    """One listener plus its Home Assistant mappings; never shared across entries."""

    def __init__(self, hass, entry):
        super().__init__()
        self.hass = hass
        self.entry = entry
        self.device_id: str  # Registered before any entity platform is forwarded.
        self.applied_config: dict | None = None
        self.loaded_platforms: set[Platform] = set()
        self.refresh_task: asyncio.Task | None = None
        self.configuration_lock = asyncio.Lock()
        self.listener_config = (entry.data["host"], entry.data["port"])
        self._mapping_unsubs = []
        self._mapping_tasks = set()
        self._exports = []
        self._scripts = []
        self._actions_by_join = {}
        self._sync_task = None
        self._export_pending = {}
        self._export_tasks = {}

    def _schedule(self, coro):
        task = self.hass.async_create_task(coro)
        self._mapping_tasks.add(task)
        task.add_done_callback(self._mapping_tasks.discard)
        return task

    @callback
    def _queue_sync(self):
        """A blocked peer can have at most one full resynchronization pending."""
        if self._sync_task is None or self._sync_task.done():
            self._sync_task = self._schedule(self._sync_exports())

    @callback
    def _queue_export(self, data, result):
        """Keep only the latest unsent template value for each outbound join."""
        if not self.available:
            return
        key = (data["join_type"], data["join"])
        self._export_pending[key] = (data, result)
        if key not in self._export_tasks or self._export_tasks[key].done():
            self._export_tasks[key] = self._schedule(self._drain_export(key))

    async def _drain_export(self, key):
        while key in self._export_pending:
            data, result = self._export_pending.pop(key)
            await self._export_value(data, result)

    @callback
    def setup_mappings(self):
        """Install change listeners, and resend exports after peer requests sync."""
        for subentry in self.entry.subentries.values():
            data = subentry.data
            if subentry.subentry_type == "export":
                template = Template(data["value_template"], self.hass)
                self._exports.append((data, template))

                @callback
                def changed(event, updates, data=data):
                    for update in updates:
                        self._queue_export(data, update.result)

                tracker = async_track_template_result(
                    self.hass,
                    [TrackTemplate(template, None)],
                    changed,
                    log_fn=self._log_template_error,
                )
                self._mapping_unsubs.append(tracker.async_remove)
                tracker.async_refresh()
            elif subentry.subentry_type == "action":
                script = Script(
                    self.hass,
                    cv.SCRIPT_SCHEMA(deepcopy(data["actions"])),
                    subentry.title,
                    DOMAIN,
                    script_mode="queued",
                    max_runs=10,
                    logger=_ActionLogger(_LOGGER, {"entry_id": self.entry.entry_id}),
                    log_exceptions=False,
                )
                self._scripts.append((data, script))
                self._actions_by_join.setdefault(
                    (data["join_type"], data["join"]), []
                ).append((data, script))
        self._mapping_unsubs.append(self.subscribe(self._message))

    @callback
    def _log_template_error(self, level, message):
        """Template diagnostics must not contain template bodies or values."""
        _LOGGER.log(
            level, "An export template failed on bridge %s", self.entry.entry_id
        )

    @callback
    def _cancel_exports(self):
        """Discard old-session work before a new peer can request a snapshot."""
        if self._sync_task is not None:
            self._sync_task.cancel()
        for task in self._export_tasks.values():
            task.cancel()
        self._sync_task = None
        self._export_pending.clear()
        self._export_tasks.clear()

    @callback
    def _message(self, kind, join, value):
        if kind == "available" and not value:
            self._cancel_exports()
        if kind == "sync" or (kind == "available" and value):
            self._queue_sync()
        if kind not in ("digital", "analog", "serial"):
            return
        variables = {
            "entry_id": self.entry.entry_id,
            "join_type": kind,
            "join": join,
            "value": value,
        }
        context = Context()
        self.hass.bus.async_fire(EVENT_JOIN_CHANGED, variables, context=context)
        for data, script in self._actions_by_join.get((kind, join), ()):
            if kind == "digital" and not value and data.get("ignore_release", True):
                continue
            self._schedule(self._run_script(script, variables, context))

    async def _run_script(self, script, variables, context):
        try:
            await script.async_run(variables, context=context)
        except Exception:
            _LOGGER.warning("An action failed on bridge %s", self.entry.entry_id)

    async def _sync_exports(self):
        for data, template in self._exports:
            try:
                result = template.async_render(log_fn=self._log_template_error)
            except TemplateError:
                _LOGGER.warning(
                    "An export template failed on bridge %s", self.entry.entry_id
                )
                continue
            await self._export_value(data, result)

    async def _export_value(self, data, result):
        if (
            not self.available
            or result is None
            or isinstance(result, TemplateError)
            or str(result) in ("unknown", "unavailable", "None")
        ):
            return
        try:
            kind = data["join_type"]
            if kind == "digital":
                value = cv.boolean(result)
            elif kind == "analog":
                value = int(result)
            else:
                value = str(result)
            await getattr(self, f"set_{kind}")(data["join"], value)
        except (ValueError, TypeError, OverflowError, vol.Invalid, OSError):
            _LOGGER.warning(
                "Invalid or unavailable %s export at join %s on bridge %s",
                data["join_type"],
                data["join"],
                self.entry.entry_id,
            )

    async def async_stop_mappings(self):
        """Release mapping-owned work while retaining the processor connection."""
        for unsubscribe in self._mapping_unsubs:
            unsubscribe()
        self._mapping_unsubs.clear()
        tasks = tuple(self._mapping_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._mapping_tasks.clear()
        for _, script in self._scripts:
            await script.async_unload()
        self._scripts.clear()
        self._actions_by_join.clear()
        self._exports.clear()
        self._export_pending.clear()
        self._export_tasks.clear()
        self._sync_task = None

    async def stop(self):
        """Remove mappings before closing the listener and processor connection."""
        await self.async_stop_mappings()
        await super().stop()
