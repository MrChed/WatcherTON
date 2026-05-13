from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    # tonwatch/ is created inside the user's workspace root.
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    d = _project_root() / "tonwatch_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass(frozen=True)
class Wallet:
    name: str
    address: str


class Store:
    def __init__(self) -> None:
        self._wallets_path = data_dir() / "wallets.json"
        self._settings_path = data_dir() / "settings.json"
        self._state_path = data_dir() / "state.json"
        self.market_cache_path = data_dir() / "market_cache.json"

    # ---- wallets ----
    def list_wallets(self) -> list[Wallet]:
        raw = _read_json(self._wallets_path, default=[])
        wallets: list[Wallet] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                address = str(item.get("address", "")).strip()
                if name and address:
                    wallets.append(Wallet(name=name, address=address))
        return wallets

    def upsert_wallet(self, *, name: str, address: str) -> None:
        name = name.strip()
        address = address.strip()
        wallets = self.list_wallets()
        updated: list[dict[str, str]] = []
        replaced = False
        for w in wallets:
            if w.address == address:
                updated.append({"name": name, "address": address})
                replaced = True
            else:
                updated.append({"name": w.name, "address": w.address})
        if not replaced:
            updated.append({"name": name, "address": address})
        _write_json(self._wallets_path, updated)

    def delete_wallet(self, *, address: str) -> bool:
        address = address.strip()
        wallets = self.list_wallets()
        kept = [w for w in wallets if w.address != address]
        if len(kept) == len(wallets):
            return False
        _write_json(self._wallets_path, [{"name": w.name, "address": w.address} for w in kept])
        # also clear state
        state = self.state()
        if "last_seen" in state and isinstance(state["last_seen"], dict):
            state["last_seen"].pop(address, None)
            self.save_state(state)
        return True

    # ---- settings ----
    def settings(self) -> dict[str, Any]:
        default = {
            "provider": "tonapi",
            "tonapi_key": "",
            "poll_seconds": 20,
            "min_alert_ton": 0.0,
        }
        raw = _read_json(self._settings_path, default=default)
        if not isinstance(raw, dict):
            return default
        merged = {**default, **raw}
        return merged

    def save_settings(self, settings: dict[str, Any]) -> None:
        _write_json(self._settings_path, settings)

    # ---- state ----
    def state(self) -> dict[str, Any]:
        default = {"last_seen": {}}
        raw = _read_json(self._state_path, default=default)
        if not isinstance(raw, dict):
            return default
        if "last_seen" not in raw or not isinstance(raw["last_seen"], dict):
            raw["last_seen"] = {}
        return raw

    def save_state(self, state: dict[str, Any]) -> None:
        _write_json(self._state_path, state)

