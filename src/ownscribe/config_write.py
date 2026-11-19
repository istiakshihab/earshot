"""Change one setting in config.toml, keeping everything else as the user wrote it.

The single writer for config.toml (`ownscribe config set`), shared by the terminal and Earshot:

- comments, order and unrelated keys survive (tomlkit);
- a key's trailing comment is dropped when its value changes, since it described the old value;
- the write is atomic (temp file + rename) and keeps the file's mode (600 for a new file, since the file
  may hold tokens);
- the first change makes a one-time backup, config.toml.bak.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import tomlkit

from ownscribe.config import CONFIG_PATH, DEFAULT_CONFIG_TOML, Config

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


class ConfigWriteError(Exception):
    """A key that doesn't exist, or a value of the wrong type."""


def field_type(key: str) -> type:
    """The Python type of SECTION.KEY in the Config schema; raises ConfigWriteError for unknown keys."""
    section, _, name = key.partition(".")
    if not section or not name or "." in name:
        raise ConfigWriteError(f"Expected SECTION.KEY, got {key!r}")
    sections = {f.name: f for f in dataclasses.fields(Config)}
    if section not in sections or section == "templates":
        raise ConfigWriteError(f"Unknown section [{section}]")
    section_cls = type(getattr(Config(), section))
    fields = {f.name: f for f in dataclasses.fields(section_cls)}
    if name not in fields:
        raise ConfigWriteError(f"Unknown setting {name!r} in [{section}]")
    default = getattr(section_cls(), name)
    return type(default)


def coerce(key: str, raw: Any) -> bool | int | float | str:
    """Turn a command-line string (or a JSON value) into the type the schema expects."""
    kind = field_type(key)
    if kind is bool:
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ConfigWriteError(f"{key} takes true or false, not {raw!r}")
    if kind is int:
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ConfigWriteError(f"{key} takes a whole number, not {raw!r}") from None
    if kind is float:
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise ConfigWriteError(f"{key} takes a number, not {raw!r}") from None
    return str(raw)


def set_value(key: str, raw: Any, path: Path | None = None, *, if_missing: bool = False) -> dict[str, Any]:
    """Set SECTION.KEY in config.toml (created from the defaults if missing). Returns what happened.

    With `if_missing`, a key already written in the file is left as it is."""
    path = path or CONFIG_PATH
    value = coerce(key, raw)
    section, _, name = key.partition(".")

    existed = path.exists()
    text = path.read_text() if existed else DEFAULT_CONFIG_TOML
    doc = tomlkit.parse(text)

    table = doc.get(section)
    if table is None:
        table = tomlkit.table()
        doc.add(section, table)
    old = table.get(name)
    old_value = old.unwrap() if hasattr(old, "unwrap") else old
    changed = old is None or old_value != value or type(old_value) is not type(value)
    if if_missing and old is not None:
        changed = False
    if changed:
        table[name] = value
        if old is not None:
            # The old trailing comment described the old value ("# faster: …").
            item = table.item(name)
            item.trivia.comment = ""
            item.trivia.comment_ws = ""

    if changed or not existed:
        _write(path, tomlkit.dumps(doc), existed)
    return {"key": key, "value": value, "previous": old_value, "changed": changed, "path": str(path)}


def _write(path: Path, text: str, existed: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = (path.stat().st_mode & 0o777) if existed else 0o600
    if existed:
        backup = path.with_name(path.name + ".bak")
        if not backup.exists():
            shutil.copy2(path, backup)
            os.chmod(backup, mode)
    fd, tmp = tempfile.mkstemp(prefix=".config.", suffix=".toml", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
