from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

import httpx


@dataclass(frozen=True)
class Tx:
    hash: str
    utime: int
    direction: Literal["in", "out", "unknown"]
    counterparty: str
    amount_ton: float
    comment: str | None

    @property
    def time_str(self) -> str:
        dt = datetime.fromtimestamp(self.utime, tz=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")


def _nano_to_ton(nano: int | float | str | None) -> float:
    try:
        return float(nano) / 1e9
    except Exception:
        return 0.0


class TonApiClient:

    def __init__(self, *, api_key: str | None = None, timeout_s: float = 20.0) -> None:
        self._base = "https://tonapi.io"
        headers: dict[str, str] = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(headers=headers, timeout=timeout_s)

    def close(self) -> None:
        self._client.close()

    def get_balance_ton(self, address: str) -> float | None:
        url = f"{self._base}/v2/blockchain/accounts/{address}"
        r = self._client.get(url)
        if r.status_code != 200:
            return None
        data = r.json()
        bal = data.get("balance")
        return _nano_to_ton(bal)

    def get_transactions(self, address: str, *, limit: int = 20) -> list[Tx]:
        url = f"{self._base}/v2/blockchain/accounts/{address}/transactions"
        r = self._client.get(url, params={"limit": limit})
        r.raise_for_status()
        data = r.json()
        items = data.get("transactions") or data.get("txs") or data.get("data") or data
        if not isinstance(items, list):
            return []
        out: list[Tx] = []
        for it in items:
            tx = _parse_tonapi_tx(address, it)
            if tx:
                out.append(tx)
        return out


def _safe_get(d: Any, *path: str) -> Any:
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _parse_tonapi_tx(address: str, it: Any) -> Tx | None:
    if not isinstance(it, dict):
        return None
    h = str(it.get("hash") or it.get("tx_hash") or "").strip()
    utime = it.get("utime") or it.get("timestamp") or _safe_get(it, "now")
    try:
        utime_i = int(utime)
    except Exception:
        utime_i = 0

    # Common tonapi schema: in_msg + out_msgs
    in_msg = it.get("in_msg") if isinstance(it.get("in_msg"), dict) else {}
    out_msgs = it.get("out_msgs") if isinstance(it.get("out_msgs"), list) else []

    # Determine direction and amount:
    # - If in_msg destination is our address => incoming.
    # - Else if any out_msg source is our address => outgoing (take first matching).
    addr_lower = address.lower()

    direction: Literal["in", "out", "unknown"] = "unknown"
    counterparty = ""
    amount_ton = 0.0
    comment: str | None = None

    in_dest = str(in_msg.get("destination") or in_msg.get("dest") or "").strip()
    in_src = str(in_msg.get("source") or in_msg.get("src") or "").strip()
    in_value = in_msg.get("value") or in_msg.get("amount")
    in_comment = in_msg.get("comment") or _safe_get(in_msg, "decoded_body", "text")

    if in_dest and in_dest.lower() == addr_lower:
        direction = "in"
        counterparty = in_src or "(unknown)"
        amount_ton = _nano_to_ton(in_value)
        if isinstance(in_comment, str) and in_comment.strip():
            comment = in_comment.strip()
    else:
        # outgoing: try find first out message with source = our address
        chosen: dict[str, Any] | None = None
        for m in out_msgs:
            if not isinstance(m, dict):
                continue
            src = str(m.get("source") or m.get("src") or "").strip()
            if src and src.lower() == addr_lower:
                chosen = m
                break
        if chosen:
            direction = "out"
            counterparty = str(chosen.get("destination") or chosen.get("dest") or "").strip() or "(unknown)"
            amount_ton = _nano_to_ton(chosen.get("value") or chosen.get("amount"))
            out_comment = chosen.get("comment") or _safe_get(chosen, "decoded_body", "text")
            if isinstance(out_comment, str) and out_comment.strip():
                comment = out_comment.strip()

    if not h:
        # fallback: some schemas use lt + account
        lt = str(it.get("lt") or "").strip()
        if lt:
            h = f"lt:{lt}"
        else:
            h = f"tx@{utime_i}"

    return Tx(
        hash=h,
        utime=utime_i,
        direction=direction,
        counterparty=counterparty,
        amount_ton=amount_ton,
        comment=comment,
    )

