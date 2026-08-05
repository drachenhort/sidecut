"""Interactive text UI for `sidecut.py --configure` - lets a headless/SSH
user set the AcoustID/Lidarr API keys without ever launching the GUI.
Writes straight to config.py's CONFIG_PATH (config.ini)."""

from __future__ import annotations

import os

import config

_MASK_VISIBLE = 4


def _mask(value: str) -> str:
    if len(value) <= _MASK_VISIBLE:
        return "*" * len(value)
    return value[:_MASK_VISIBLE] + "*" * (len(value) - _MASK_VISIBLE)


def _prompt_field(field: str, current: str, env_value: str) -> str | None:
    """Returns the new value to save, or None if it should stay unchanged
    (field left blank, or overridden by an env var so editing is moot)."""
    label = config.FIELD_LABELS[field]
    if env_value:
        print(f"{label:<18} [env {config.ENV_VARS[field]}={env_value} - overrides file, editing here has no effect]")
        input("> ")
        return None

    shown = _mask(current) if current else "not set"
    print(f"{label:<18} [{shown}]")
    typed = input("> ").strip()
    return typed or None


def _effective(field: str, merged: dict[str, str]) -> str:
    """What will actually be used at runtime for `field`: env var wins
    over whatever's in the file."""
    return os.environ.get(config.ENV_VARS[field], "") or merged.get(field, "")


def _verify_lidarr(url: str, api_key: str) -> tuple[bool | None, str]:
    """Returns (ok, message). ok is True/False for a real answer, None if
    the check couldn't be attempted at all (e.g. `requests` not installed
    on this bare box)."""
    try:
        import lidarr
    except ImportError as exc:
        return None, f"skipped - lidarr module unavailable ({exc})"

    try:
        version = lidarr.check_connection(url, api_key)
    except lidarr.LidarrError as exc:
        return False, str(exc)
    return True, f"connected - Lidarr v{version}"


def _verify(merged: dict[str, str]) -> bool:
    """Runs whatever checks are possible against the values that will
    actually be effective (env vars included). Returns False only when a
    check ran and definitively failed - never for skipped/unattempted
    checks, since those aren't a reason to block saving."""
    url = _effective("lidarr_url", merged)
    api_key = _effective("lidarr_api_key", merged)
    if not url or not api_key:
        return True

    print("Verifying Lidarr connection...")
    ok, message = _verify_lidarr(url, api_key)
    if ok is None:
        print(f"  Lidarr: {message}")
        return True
    print(f"  Lidarr: {'OK' if ok else 'FAILED'} - {message}")
    return ok is not False


def run() -> int:
    path = config.resolve_config_path()

    print("=" * 44)
    print(" Sidecut - Headless Configuration")
    print(f" File: {path}")
    print("=" * 44)
    print()
    print("Leave blank to keep current value. Values in [brackets] show")
    print("what's currently set (env vars, if any, always win over this")
    print("file and are shown for reference, not editable here).")
    print()

    current = config.read_file(path)
    updates: dict[str, str] = {}
    for field in config.FIELDS:
        env_value = os.environ.get(config.ENV_VARS[field], "")
        new_value = _prompt_field(field, current.get(field, ""), env_value)
        if new_value is not None:
            updates[field] = new_value
        print()

    if not updates:
        print("Nothing changed.")
        return 0

    print("-" * 44)
    print(" Review")
    print("-" * 44)
    merged = {**current, **updates}
    for field in config.FIELDS:
        value = merged.get(field, "")
        if not value:
            continue
        tag = "" if field in updates else " (unchanged)"
        print(f"  {field:<17}: {_mask(value)}{tag}")
    print()

    verified = _verify(merged)
    print()
    if verified:
        prompt = f"Save to {path} ? [Y/n] > "
        default_yes = True
    else:
        prompt = f"Lidarr check failed - save to {path} anyway? [y/N] > "
        default_yes = False

    answer = input(prompt).strip().lower()
    proceed = answer in ("y", "yes") or (answer == "" and default_yes)
    if not proceed:
        print("Not saved.")
        return 1

    config.save_file(updates, path)
    print()
    print("Saved. lidarr_hook.py and the Settings dialog will pick this up.")
    print("=" * 44)
    return 0
