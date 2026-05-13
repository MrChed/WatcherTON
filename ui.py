from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

import httpx
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import FloatPrompt, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from .storage import Store, Wallet
from .ton_api import TonApiClient, Tx
from .market_data import (
    get_cbr_key_rate,
    get_ton_prices,
    record_balance,
    get_balance_history,
    sparkline,
)


console = Console()


def _short_addr(addr: str) -> str:
    if len(addr) <= 12:
        return addr
    return f"{addr[:6]}…{addr[-6:]}"


def _wallet_table(wallets: list[Wallet]) -> Table:
    t = Table(title="Кошельки", box=box.SIMPLE, show_lines=False)
    t.add_column("#", style="dim", width=4, justify="right")
    t.add_column("Название", style="bold")
    t.add_column("Адрес", style="cyan")
    for i, w in enumerate(wallets, start=1):
        t.add_row(str(i), w.name, w.address)
    return t


def _tx_table(txs: Iterable[Tx], *, title: str) -> Table:
    t = Table(title=title, box=box.SIMPLE, show_lines=False)
    t.add_column("Время", style="dim", no_wrap=True)
    t.add_column("Напр.", no_wrap=True)
    t.add_column("Сумма (TON)", justify="right")
    t.add_column("Контрагент", style="cyan")
    t.add_column("Хэш", style="dim")
    t.add_column("Комментарий", overflow="fold")

    for tx in txs:
        if tx.direction == "in":
            dir_txt = Text("IN", style="green bold")
        elif tx.direction == "out":
            dir_txt = Text("OUT", style="red bold")
        else:
            dir_txt = Text("?", style="yellow bold")
        t.add_row(
            tx.time_str,
            dir_txt,
            f"{tx.amount_ton:.6f}".rstrip("0").rstrip("."),
            _short_addr(tx.counterparty),
            tx.hash[:12] + "…" if len(tx.hash) > 13 else tx.hash,
            tx.comment or "",
        )
    return t


@dataclass(frozen=True)
class MenuItem:
    key: str
    label: str


MAIN_MENU: list[MenuItem] = [
    MenuItem("1", "Кошельки: список / добавить / изменить / удалить"),
    MenuItem("2", "Анализ кошелька (транзакции + быстрые итоги)"),
    MenuItem("3", "Мониторинг (алерты по новым транзакциям)"),
    MenuItem("4", "Настройки (ключ, интервал, порог)"),
    MenuItem("5", "Дашборд: курс TON, ставка ЦБ РФ, балансы"),
    MenuItem("0", "Выход"),
]


def run_app() -> None:
    store = Store()
    while True:
        console.clear()
        console.print(Panel.fit("TON Watch (CLI)", style="bold white on blue"))
        for item in MAIN_MENU:
            console.print(f"[bold]{item.key}[/bold]  {item.label}")
        choice = Prompt.ask("\nВыбор", default="1").strip()

        if choice == "0":
            return
        if choice == "1":
            _wallets_menu(store)
        elif choice == "2":
            _analyze_menu(store)
        elif choice == "3":
            _watch_menu(store)
        elif choice == "4":
            _settings_menu(store)
        elif choice == "5":
            _dashboard_menu(store)
        else:
            console.print("[red]Неизвестный пункт[/red]")
            time.sleep(1.0)


def _wallets_menu(store: Store) -> None:
    while True:
        console.clear()
        wallets = store.list_wallets()
        console.print(_wallet_table(wallets))
        console.print("\n[bold]a[/bold] Добавить  [bold]e[/bold] Изменить  [bold]d[/bold] Удалить  [bold]b[/bold] Назад")
        action = Prompt.ask("Действие", default="b").strip().lower()
        if action == "b":
            return
        if action == "a":
            name = Prompt.ask("Название кошелька").strip()
            address = Prompt.ask("TON address").strip()
            if name and address:
                store.upsert_wallet(name=name, address=address)
        elif action == "e":
            if not wallets:
                continue
            idx = IntPrompt.ask("Номер кошелька", default=1)
            if 1 <= idx <= len(wallets):
                w = wallets[idx - 1]
                name = Prompt.ask("Новое название", default=w.name).strip()
                address = Prompt.ask("Новый адрес", default=w.address).strip()
                if name and address:
                    # if address changed, delete old then add new
                    if address != w.address:
                        store.delete_wallet(address=w.address)
                    store.upsert_wallet(name=name, address=address)
        elif action == "d":
            if not wallets:
                continue
            idx = IntPrompt.ask("Номер для удаления", default=1)
            if 1 <= idx <= len(wallets):
                store.delete_wallet(address=wallets[idx - 1].address)


def _pick_wallet(store: Store) -> Wallet | None:
    wallets = store.list_wallets()
    if not wallets:
        console.print("[yellow]Пока нет сохранённых кошельков. Сначала добавь.[/yellow]")
        time.sleep(1.2)
        return None
    console.print(_wallet_table(wallets))
    idx = IntPrompt.ask("Номер кошелька", default=1)
    if 1 <= idx <= len(wallets):
        return wallets[idx - 1]
    return None


def _analyze_menu(store: Store) -> None:
    console.clear()
    w = _pick_wallet(store)
    if not w:
        return

    settings = store.settings()
    key = (settings.get("tonapi_key") or "").strip() or None
    client = TonApiClient(api_key=key)
    try:
        console.print(Panel.fit(f"[bold]{w.name}[/bold]\n{w.address}", title="Кошелёк", style="cyan"))
        balance = client.get_balance_ton(w.address)
        if balance is not None:
            console.print(f"[bold]Баланс:[/bold] {balance:.6f} TON".rstrip("0").rstrip("."))
            record_balance(store.market_cache_path, w.address, balance)

        txs = client.get_transactions(w.address, limit=20)
        console.print(_tx_table(txs, title="Последние 20 транзакций"))

        total_in = sum(t.amount_ton for t in txs if t.direction == "in")
        total_out = sum(t.amount_ton for t in txs if t.direction == "out")
        console.print(
            Panel.fit(
                f"[green]IN[/green]: {total_in:.6f} TON\n[red]OUT[/red]: {total_out:.6f} TON",
                title="Итоги (по последним 20)",
            )
        )
        Prompt.ask("Enter — назад", default="")
    except httpx.HTTPError as e:
        console.print(f"[red]HTTP ошибка:[/red] {e}")
        Prompt.ask("Enter — назад", default="")
    finally:
        client.close()


def _watch_menu(store: Store) -> None:
    console.clear()
    wallets = store.list_wallets()
    if not wallets:
        console.print("[yellow]Пока нет сохранённых кошельков. Сначала добавь.[/yellow]")
        time.sleep(1.2)
        return

    settings = store.settings()
    poll = int(settings.get("poll_seconds") or 20)
    min_alert = float(settings.get("min_alert_ton") or 0.0)
    key = (settings.get("tonapi_key") or "").strip() or None

    state = store.state()
    last_seen: dict[str, str] = state.get("last_seen", {}) if isinstance(state.get("last_seen"), dict) else {}

    console.print(Panel.fit("Мониторинг. Ctrl+C чтобы остановить.", style="bold white on blue"))
    console.print(_wallet_table(wallets))
    console.print("\nВведи номера через запятую (например 1,3) или оставь пусто для всех.")
    raw = Prompt.ask("Какие кошельки мониторить", default="").strip()
    if raw:
        wanted: set[int] = set()
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                wanted.add(int(part))
            except Exception:
                pass
        wallets = [w for i, w in enumerate(wallets, start=1) if i in wanted] or wallets

    console.print(f"\nИнтервал: {poll}s | Порог: {min_alert} TON\n")

    client = TonApiClient(api_key=key, timeout_s=max(10.0, poll))
    try:
        while True:
            for w in wallets:
                try:
                    txs = client.get_transactions(w.address, limit=10)
                    # попутно обновляем историю баланса
                    bal = client.get_balance_ton(w.address)
                    if bal is not None:
                        record_balance(store.market_cache_path, w.address, bal)
                except Exception as e:
                    console.print(f"[red]{w.name}[/red] error: {e}")
                    continue

                prev = last_seen.get(w.address)
                new: list[Tx] = []
                for tx in txs:
                    if prev and tx.hash == prev:
                        break
                    new.append(tx)

                if new:
                    # mark newest as seen
                    last_seen[w.address] = new[0].hash
                    # display in chronological order (oldest first)
                    for tx in reversed(new):
                        if tx.amount_ton < min_alert:
                            continue
                        tag = f"[bold]{w.name}[/bold]"
                        dir_col = "[green]IN[/green]" if tx.direction == "in" else "[red]OUT[/red]" if tx.direction == "out" else "[yellow]?[/yellow]"
                        console.print(
                            f"{tag} {tx.time_str} {dir_col} {tx.amount_ton:.6f} TON -> {_short_addr(tx.counterparty)}  [dim]{tx.hash}[/dim]"
                        )
                        if tx.comment:
                            console.print(f"  [dim]коммент:[/dim] {tx.comment}")

            state["last_seen"] = last_seen
            store.save_state(state)
            time.sleep(max(3, poll))
    except KeyboardInterrupt:
        console.print("\n[bold]Остановлено.[/bold]")
        time.sleep(0.6)
    finally:
        client.close()


def _dashboard_menu(store: Store) -> None:
    """
    Дашборд: курс TON, ключевая ставка ЦБ РФ, балансы всех кошельков со sparkline.
    """
    from rich.columns import Columns
    from rich.align import Align

    console.clear()
    console.print(Panel.fit("📊  Дашборд", style="bold white on blue"))

    cache = store.market_cache_path
    settings = store.settings()
    key = (settings.get("tonapi_key") or "").strip() or None

    # ── Курс TON ────────────────────────────────────────────────────────────
    with console.status("[cyan]Загружаю курс TON…[/cyan]", spinner="dots"):
        usd, rub, ton_updated = get_ton_prices(cache)

    ton_lines: list[str] = []
    if usd is not None:
        ton_lines.append(f"[bold yellow]TON/USD[/bold yellow]  [green]{usd:.4f}[/green] $")
    if rub is not None:
        ton_lines.append(f"[bold yellow]TON/RUB[/bold yellow]  [green]{rub:.2f}[/green] ₽")
    if ton_updated:
        ton_lines.append(f"[dim]обновлено: {ton_updated}[/dim]")
    ton_text = "\n".join(ton_lines) if ton_lines else "[red]недоступно[/red]"
    console.print(Panel(ton_text, title="Курс TON", style="yellow", width=36))

    # ── Ключевая ставка ЦБ РФ ───────────────────────────────────────────────
    with console.status("[cyan]Загружаю ставку ЦБ РФ…[/cyan]", spinner="dots"):
        cbr_rate, cbr_updated = get_cbr_key_rate(cache)

    if cbr_rate is not None:
        cbr_text = (
            f"[bold red]{cbr_rate:.2f}%[/bold red]\n"
            f"[dim]обновлено: {cbr_updated}[/dim]\n"
            f"[dim](кеш обновляется раз в сутки)[/dim]"
        )
    else:
        cbr_text = "[red]недоступно[/red]"
    console.print(Panel(cbr_text, title="Ключевая ставка ЦБ РФ", style="red", width=36))

    # ── Балансы кошельков со sparkline ──────────────────────────────────────
    wallets = store.list_wallets()
    if wallets:
        console.print()
        client = TonApiClient(api_key=key)
        try:
            t = Table(title="Балансы кошельков", box=box.SIMPLE, show_lines=False)
            t.add_column("Кошелёк", style="bold")
            t.add_column("Адрес", style="cyan")
            t.add_column("Баланс (TON)", justify="right")
            t.add_column("≈ USD", justify="right", style="yellow")
            t.add_column("История (30 точек)", no_wrap=True)

            for w in wallets:
                with console.status(f"[dim]{w.name}…[/dim]", spinner="line"):
                    bal = client.get_balance_ton(w.address)
                if bal is not None:
                    record_balance(cache, w.address, bal)

                history = get_balance_history(cache, w.address)
                spark = sparkline(history)

                bal_str = f"{bal:.4f}" if bal is not None else "—"
                usd_str = (
                    f"{bal * usd:.2f}" if (bal is not None and usd is not None) else "—"
                )
                spark_colored = f"[green]{spark}[/green]"
                t.add_row(w.name, _short_addr(w.address), bal_str, usd_str, spark_colored)

            console.print(t)
        finally:
            client.close()
    else:
        console.print("[yellow]Нет сохранённых кошельков.[/yellow]")

    console.print()
    Prompt.ask("Enter — назад", default="")


def _settings_menu(store: Store) -> None:
    while True:
        console.clear()
        s = store.settings()
        t = Table(title="Настройки", box=box.SIMPLE)
        t.add_column("Key", style="dim")
        t.add_column("Value", style="bold")
        t.add_row("provider", str(s.get("provider")))
        t.add_row("tonapi_key", ("(пусто)" if not (s.get("tonapi_key") or "").strip() else "********"))
        t.add_row("poll_seconds", str(s.get("poll_seconds")))
        t.add_row("min_alert_ton", str(s.get("min_alert_ton")))
        console.print(t)

        console.print("\n[bold]1[/bold] Ключ TonAPI  [bold]2[/bold] Интервал (сек)  [bold]3[/bold] Порог (TON)  [bold]b[/bold] Назад")
        ch = Prompt.ask("Выбор", default="b").strip().lower()
        if ch == "b":
            store.save_settings(s)
            return
        if ch == "1":
            s["tonapi_key"] = Prompt.ask("TonAPI key (пусто чтобы очистить)", default=(s.get("tonapi_key") or "")).strip()
        elif ch == "2":
            s["poll_seconds"] = max(3, IntPrompt.ask("Интервал (сек)", default=int(s.get("poll_seconds") or 20)))
        elif ch == "3":
            s["min_alert_ton"] = max(0.0, FloatPrompt.ask("Порог (TON)", default=float(s.get("min_alert_ton") or 0.0)))

