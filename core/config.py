"""App configuration — load/save stocks_ledger.ini.

Config resolution order:
    1. ~/.stocks_ledger/stocks_ledger.ini   (user-home — preferred)
    2. <cwd>/stocks_ledger.ini              (project root — fallback)
    3. Create ~/.stocks_ledger/stocks_ledger.ini with defaults (first run)
"""
from __future__ import annotations

import configparser
import os
from pathlib import Path
from typing import Optional, Union

DEFAULT_CONFIG = {
    "db_path": "stocks_ledger.db",
    "default_venue": "",
    "export_dir": "exports",
}

_LEDGER_KEYS = {"db_path", "default_venue", "export_dir"}


def get_default_config_path() -> Path:
    home_cfg = Path.home() / ".stocks_ledger" / "stocks_ledger.ini"
    home_cfg.parent.mkdir(parents=True, exist_ok=True)
    return home_cfg


def get_resolved_config_path(
    config_path: Optional[Union[str, Path]] = None,
) -> Path:
    if config_path is not None:
        return Path(config_path)

    home_cfg = get_default_config_path()
    if home_cfg.exists():
        return home_cfg

    project_cfg = Path.cwd() / "stocks_ledger.ini"
    if project_cfg.exists():
        return project_cfg

    _write_defaults(home_cfg)
    return home_cfg


def _write_defaults(path: Path) -> None:
    parser = configparser.ConfigParser()
    parser["stocks_ledger"] = {
        "db_path": str(path.parent / "stocks_ledger.db"),
        "export_dir": str(path.parent / "exports"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        parser.write(f)


def load_config(
    config_path: Optional[Union[str, Path]] = None,
) -> dict:
    resolved = get_resolved_config_path(config_path)
    config = dict(DEFAULT_CONFIG)

    if resolved.exists():
        parser = configparser.ConfigParser()
        parser.read(resolved, encoding="utf-8")
        section = "stocks_ledger"
        if section in parser:
            for key in _LEDGER_KEYS:
                if key in parser[section]:
                    val = parser[section][key].strip()
                    if val:
                        config[key] = val

    return config


def set_db_path(
    new_db_path: str,
    config_path: Optional[Union[str, Path]] = None,
) -> None:
    resolved = get_resolved_config_path(config_path)
    parser = configparser.ConfigParser()
    if resolved.exists():
        parser.read(resolved, encoding="utf-8")
    section = "stocks_ledger"
    if section not in parser:
        parser[section] = {}
    parser[section]["db_path"] = new_db_path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with open(resolved, "w", encoding="utf-8") as f:
        parser.write(f)
