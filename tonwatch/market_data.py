from __future__ import annotations

"""
market_data.py — внешние данные: ключевая ставка ЦБ РФ, курс TON/USD/RUB.
Кеш хранится в tonwatch_data/market_cache.json и обновляется раз в сутки.
"""

import json
import time
from pathlib import Path
from typing import Any
from datetime import datetime, timezone
import re

import httpx


# ─── helpers ────────────────────────────────────────────────────────────────

def _now_ts() -> float:
    return time.time()


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_fresh(cache: dict[str, Any], key: str, ttl_s: float = 86400.0) -> bool:
    entry = cache.get(key)
    if not isinstance(entry, dict):
        return False
    ts = entry.get("ts", 0)
    return (_now_ts() - float(ts)) < ttl_s


def _fetch_cbr_key_rate() -> float | None:
    try:
        r = httpx.get(
            "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx/KeyRate",
            timeout=10.0,
        )
        if r.status_code == 200:
            text = r.text
            # <Rate>21.00</Rate>  или  <KeyRate>21.00</KeyRate>
            m = re.search(r"<(?:Rate|KeyRate|rate)>([\d.,]+)</(?:Rate|KeyRate|rate)>", text)
            if m:
                return float(m.group(1).replace(",", "."))
    except Exception:
        pass

    try:
        r = httpx.get("https://www.cbr.ru/", timeout=10.0, follow_redirects=True)
        if r.status_code == 200:
            text = r.text
            m = re.search(
                r"(\d{1,2}[.,]\d{2})\s*%?[^%]*ключевая|ключевая[^%]*?(\d{1,2}[.,]\d{2})\s*%",
                text,
                flags=re.IGNORECASE,
            )
            if m:
                val = m.group(1) or m.group(2)
                return float(val.replace(",", "."))
    except Exception:
        pass

    return None


def get_cbr_key_rate(cache_path: Path) -> tuple[float | None, str]:
    cache = _load_cache(cache_path)
    if _is_fresh(cache, "cbr_key_rate", ttl_s=86400.0):
        entry = cache["cbr_key_rate"]
        return entry.get("value"), entry.get("updated", "")

    rate = _fetch_cbr_key_rate()
    updated = datetime.now().strftime("%Y-%m-%d %H:%M")
    if rate is not None:
        cache["cbr_key_rate"] = {"value": rate, "ts": _now_ts(), "updated": updated}
        _save_cache(cache_path, cache)
        return rate, updated

    old = cache.get("cbr_key_rate")
    if isinstance(old, dict):
        return old.get("value"), old.get("updated", "")
    return None, ""


def _fetch_ton_prices() -> dict[str, float] | None:
    """
    CoinGecko public API (без ключа, rate-limit ~30 req/min).
    Возвращает {"usd": ..., "rub": ...}
    """
    try:
        r = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "the-open-network", "vs_currencies": "usd,rub"},
            timeout=10.0,
            headers={"Accept": "application/json"},
        )
        if r.status_code == 200:
            data = r.json()
            ton = data.get("the-open-network", {})
            usd = ton.get("usd")
            rub = ton.get("rub")
            if usd and rub:
                return {"usd": float(usd), "rub": float(rub)}
    except Exception:
        pass
    return None


def get_ton_prices(cache_path: Path, ttl_s: float = 300.0) -> tuple[float | None, float | None, str]:
    cache = _load_cache(cache_path)
    if _is_fresh(cache, "ton_prices", ttl_s=ttl_s):
        entry = cache["ton_prices"]
        return entry.get("usd"), entry.get("rub"), entry.get("updated", "")

    prices = _fetch_ton_prices()
    updated = datetime.now().strftime("%Y-%m-%d %H:%M")
    if prices:
        cache["ton_prices"] = {**prices, "ts": _now_ts(), "updated": updated}
        _save_cache(cache_path, cache)
        return prices["usd"], prices["rub"], updated

    old = cache.get("ton_prices")
    if isinstance(old, dict):
        return old.get("usd"), old.get("rub"), old.get("updated", "")
    return None, None, ""


_MAX_HISTORY = 30


def record_balance(cache_path: Path, address: str, balance_ton: float) -> None:
    cache = _load_cache(cache_path)
    key = f"balance_history_{address}"
    history: list[dict[str, Any]] = cache.get(key, [])
    if not isinstance(history, list):
        history = []
    history.append({"ts": _now_ts(), "bal": balance_ton})
    history = history[-_MAX_HISTORY:]
    cache[key] = history
    _save_cache(cache_path, cache)


def get_balance_history(cache_path: Path, address: str) -> list[float]:
    cache = _load_cache(cache_path)
    key = f"balance_history_{address}"
    history = cache.get(key, [])
    if not isinstance(history, list):
        return []
    return [float(h["bal"]) for h in history if isinstance(h, dict) and "bal" in h]


_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float]) -> str:
    """Строит однострочный sparkline из списка float."""
    if not values:
        return "—"
    if len(values) == 1:
        return _SPARK_CHARS[4]
    mn, mx = min(values), max(values)
    rng = mx - mn
    result = []
    for v in values:
        if rng == 0:
            idx = 4
        else:
            idx = int((v - mn) / rng * (len(_SPARK_CHARS) - 1))
        result.append(_SPARK_CHARS[idx])
    return "".join(result)
