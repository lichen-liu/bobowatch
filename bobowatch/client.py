"""Runtime-file adapter settings, reloaded by the existing background sender."""

import json
import logging
from pathlib import Path

from .adapter import Monitor, destination
from .protocol import identifier

log = logging.getLogger(__name__)


def client_settings(path):
    path = Path(path)
    settings = json.loads(path.read_text())
    if not isinstance(settings, dict):
        raise ValueError("Expected an application settings object")
    config = settings.get("monitoring", {"enabled": False})
    if not isinstance(config, dict) or type(config.get("enabled", False)) is not bool:
        raise ValueError("monitoring.enabled must be a boolean")
    if not config.get("enabled", False):
        return None
    replica = identifier(config.get("replica"))
    token = config.get("token_file")
    if not isinstance(token, str) or not token:
        raise ValueError("Monitoring token_file is required")
    endpoint, token_file = destination(config.get("endpoint"), path.parent / token)
    interval = config.get("interval", 15)
    if type(interval) not in (float, int) or not 0.1 <= interval <= 3600:
        raise ValueError("Monitoring interval must be between 0.1 and 3600 seconds")
    return {
        "endpoint": endpoint,
        "token_file": token_file,
        "replica": replica,
        "interval": interval,
    }


class ConfiguredMonitor(Monitor):
    def __init__(self, settings_path, *, routes=(), revision="unknown"):
        self.settings_path = Path(settings_path)
        config = client_settings(self.settings_path)
        if config is None:
            raise ValueError("Monitoring is disabled")
        super().__init__(**config, routes=routes, revision=revision)
        self._reload_failed = False

    def flush(self):
        try:
            config = client_settings(self.settings_path)
            if config is None:
                return True
            if config["replica"] != self.replica:
                raise ValueError("Changing replica identity requires an app restart")
            self.set_destination(config["endpoint"], config["token_file"])
            self.interval = config["interval"]
            self._reload_failed = False
        except (OSError, ValueError, KeyError, TypeError) as error:
            if not self._reload_failed:
                log.warning(
                    "Monitoring config rejected (%s); using prior settings", type(error).__name__
                )
            self._reload_failed = True
        return super().flush()
