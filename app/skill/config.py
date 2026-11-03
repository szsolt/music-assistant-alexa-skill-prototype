# -*- coding: utf-8 -*-
"""The skill's settings: its data directory, next to the device mapping, and its env switches."""

import os


def device_mapping_path():
    return os.environ.get("DEVICE_MAPPING_PATH", "/app/instance_data/device_players.json")


def data_path(name):
    """The path of the data file name."""
    return os.path.join(os.path.dirname(device_mapping_path()), name)


def apl_enabled():
    return os.environ.get("ENABLE_APL", "false").lower() in ("true", "1", "yes")


def ma_hostname(raise_on_http_scheme=True):
    """MA's public https host from MA_HOSTNAME ("https://host"), or ""; an http one raises ValueError("http_scheme")."""
    hostname = os.environ.get("MA_HOSTNAME", "").strip()
    if len(hostname) >= 2 and hostname[0] == hostname[-1] and hostname[0] in "\"'":
        hostname = hostname[1:-1].strip()
    hostname = hostname.strip("\"' ").rstrip("/")
    if not hostname:
        return ""
    if hostname.startswith("https://"):
        return hostname
    if hostname.startswith("http://"):
        if raise_on_http_scheme:
            raise ValueError("http_scheme")
        return ""
    return f"https://{hostname}"
