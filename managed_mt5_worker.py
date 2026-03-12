"""Process worker for managed MT5 trade execution.

This runs in a separate process via ProcessPoolExecutor.
It connects to MT5 with user-specific credentials and executes trades.

MT5 connection is kept alive across trades (module-level globals) to avoid
the 1-2 second overhead of mt5.initialize() + mt5.shutdown() per trade.
"""

import atexit
from typing import Any, Dict

# Import shared utilities - fall back to inline if import fails (subprocess isolation)
try:
    from mt5_order_utils import execute_command, map_mt5_retcode
    USE_SHARED_UTILS = True
except ImportError:
    USE_SHARED_UTILS = False

    MT5_RETCODE_MESSAGES = {
        10016: "Invalid stop loss or take profit.",
        10018: "Market is currently closed.",
        10019: "Not enough money in account to execute trade.",
    }

    def map_mt5_retcode(retcode):
        if retcode is None:
            return "Trade request failed."
        return MT5_RETCODE_MESSAGES.get(retcode, f"Broker returned error code {retcode}.")


# ── Persistent connection state (process-global) ──────────────────────────────
_mt5_initialized = False
_mt5_login = None


def _worker_initializer():
    """Called once per worker process by ProcessPoolExecutor initializer."""
    pass


def execute_managed_trade_worker(payload: Dict[str, Any]) -> Dict[str, Any]:
    global _mt5_initialized, _mt5_login

    try:
        import MetaTrader5 as mt5
    except ImportError:
        return {"status": "failed", "error": "MetaTrader5 module unavailable in worker"}

    login    = int(payload.get("mt5_login", 0))
    password = payload.get("mt5_password", "")
    server   = payload.get("mt5_server", "")
    path     = payload.get("mt5_path") or None

    # Re-initialize only if not connected or account changed
    needs_init = (not _mt5_initialized) or (_mt5_login != login)

    if needs_init:
        if _mt5_initialized:
            mt5.shutdown()
        initialized = mt5.initialize(path=path, login=login, password=password, server=server)
        if not initialized:
            _mt5_initialized = False
            return {"status": "failed", "error": f"mt5 init failed: {mt5.last_error()}"}
        _mt5_initialized = True
        _mt5_login = login
        atexit.register(mt5.shutdown)

    try:
        command = {
            "action": payload.get("action", ""),
            "symbol": payload.get("symbol", ""),
            "size": payload.get("size", 0.1),
            "sl": payload.get("sl"),
            "tp": payload.get("tp"),
        }
        if USE_SHARED_UTILS:
            result = execute_command(mt5, command, comment_prefix="managed-bridge")
        else:
            result = _execute_command_inline(mt5, command)
        return result
    except Exception as exc:
        # Reset state so next call re-initializes
        _mt5_initialized = False
        return {"status": "failed", "error": str(exc)}
    # NOTE: No mt5.shutdown() in finally — connection stays alive for next trade


def _execute_command_inline(mt5, command: Dict[str, Any]) -> Dict[str, Any]:
    """Inline execution logic as fallback when shared utils unavailable."""
    action = (command.get("action") or "").upper()
    symbol = command.get("symbol", "")
    size = float(command.get("size") or 0.1)
    sl = command.get("sl")
    tp = command.get("tp")

    if not symbol and action not in ("CLOSE_ALL",):
        return {"status": "failed", "error": "missing symbol"}

    if action == "BUY":
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return {"status": "failed", "error": f"no tick data for {symbol}"}
        request_data = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": size,
            "type": mt5.ORDER_TYPE_BUY,
            "price": tick.ask,
            "sl": sl,
            "tp": tp,
            "comment": "managed-bridge-trade",
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
    elif action == "SELL":
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return {"status": "failed", "error": f"no tick data for {symbol}"}
        request_data = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": size,
            "type": mt5.ORDER_TYPE_SELL,
            "price": tick.bid,
            "sl": sl,
            "tp": tp,
            "comment": "managed-bridge-trade",
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
    elif action.startswith("CLOSE"):
        positions = mt5.positions_get(symbol=symbol if action != "CLOSE_ALL" else None)
        if not positions:
            return {"status": "failed", "error": "no open positions"}

        closed = []
        for pos in positions:
            close_req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                "position": pos.ticket,
                "comment": "managed-bridge-close",
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            close_res = mt5.order_send(close_req)
            if close_res and close_res.retcode == mt5.TRADE_RETCODE_DONE:
                closed.append(close_res.order)

        if closed:
            return {"status": "executed", "order_ids": closed}
        return {"status": "failed", "error": "close failed"}
    else:
        return {"status": "failed", "error": f"unsupported action: {action}"}

    result = mt5.order_send(request_data)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        return {"status": "executed", "order_id": result.order}
    return {
        "status": "failed",
        "error": getattr(result, "comment", "order_send failed"),
        "retcode": getattr(result, "retcode", None),
        "error_message": map_mt5_retcode(getattr(result, "retcode", None)),
    }
