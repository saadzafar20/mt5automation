"""Shared MT5 order execution utilities.

This module extracts common MT5 order logic used by both:
- relay.py (self-hosted execution)
- managed_mt5_worker.py (VPS managed execution)
"""

import logging
import math
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Common MT5 error code mappings
MT5_RETCODE_MESSAGES = {
    10004: "Requote received.",
    10006: "Request rejected by server.",
    10007: "Request canceled by trader.",
    10010: "Only part of the request was completed.",
    10011: "Request processing error.",
    10012: "Request timed out.",
    10013: "Invalid request parameters.",
    10014: "Invalid volume.",
    10015: "Invalid price.",
    10016: "Invalid stop loss or take profit.",
    10017: "Trading disabled for this account.",
    10018: "Market is currently closed.",
    10019: "Not enough money in account to execute trade.",
    10020: "Price has changed significantly.",
    10021: "No quotes available for this symbol.",
    10022: "Invalid order expiration date.",
    10023: "Order state has changed.",
    10024: "Too many trade requests.",
    10025: "No changes in request parameters.",
    10026: "Autotrading disabled by server.",
    10027: "Autotrading disabled by client terminal.",
    10028: "Request locked for processing.",
    10029: "Order or position frozen.",
    10030: "Invalid order filling type.",
    10031: "No connection with the trade server.",
    10032: "Operation allowed only for live accounts.",
    10033: "Number of pending orders has reached the limit.",
    10034: "Volume of orders and positions for this symbol has reached the limit.",
    10035: "Incorrect or prohibited order type.",
    10036: "Position with the specified POSITION_IDENTIFIER has already been closed.",
    10038: "A close volume exceeds the current position volume.",
    10039: "A close order already exists for this position.",
    10040: "Number of open positions reached the limit set by the broker.",
    10041: "Pending order activation request is rejected, order is canceled.",
    10042: "Request rejected due to the 'Only long positions are allowed' rule.",
    10043: "Request rejected due to the 'Only short positions are allowed' rule.",
    10044: "Request rejected due to the 'Only position closing is allowed' rule.",
    10045: "Request rejected as exceeding the maximum allowed position volume by symbol.",
    10046: "Request rejected due to hedging being disallowed.",
    10047: "Request rejected due to violation of FIFO rule.",
}


def map_mt5_retcode(retcode: Optional[int]) -> str:
    """Map MT5 return code to human-readable message."""
    if retcode is None:
        return "Trade request failed."
    return MT5_RETCODE_MESSAGES.get(retcode, f"Broker returned error code {retcode}.")


# FIX 11: User-friendly error messages with actionable guidance
_USER_FRIENDLY_ERRORS = {
    10027: (
        "AutoTrading disabled",
        "Enable AutoTrading in MT5: Tools → Options → Expert Advisors → Allow automated trading",
    ),
    10031: (
        "No broker connection",
        "Check your internet connection and MT5 terminal status",
    ),
    10019: (
        "Insufficient funds",
        "Your account balance is too low for this trade size",
    ),
    10018: (
        "Market closed",
        "This market is currently closed. Try during market hours",
    ),
    10016: (
        "Invalid SL/TP",
        "Stop loss or take profit is too close to the current price",
    ),
    10026: (
        "AutoTrading disabled by server",
        "Contact your broker — AutoTrading has been disabled server-side",
    ),
    10017: (
        "Trading disabled",
        "Trading is disabled for this account. Contact your broker",
    ),
    10014: (
        "Invalid volume",
        "The lot size is invalid for this symbol. Check min/max lot size with your broker",
    ),
    10015: (
        "Invalid price",
        "The submitted price is invalid. The market may have moved — try again",
    ),
    10024: (
        "Too many requests",
        "MT5 is rate-limiting trade requests. Wait a moment and try again",
    ),
    10033: (
        "Pending order limit reached",
        "Maximum number of pending orders reached. Close some orders first",
    ),
    10034: (
        "Volume limit reached",
        "Maximum volume for this symbol has been reached",
    ),
    10040: (
        "Open position limit reached",
        "Maximum number of open positions reached. Close some positions first",
    ),
}


def user_friendly_error(retcode: Optional[int]) -> tuple:
    """
    FIX 11: Return (short_message, action_hint) for the given MT5 retcode.

    Returns:
        tuple of (short_message: str, action_hint: str)
    """
    if retcode is None:
        return ("Trade failed", "Unknown error — check MT5 connection")
    if retcode in _USER_FRIENDLY_ERRORS:
        return _USER_FRIENDLY_ERRORS[retcode]
    return ("Trade failed", f"Error code {retcode}")


def build_market_order(mt5, action: str, symbol: str, volume: float,
                       sl: Optional[float] = None, tp: Optional[float] = None,
                       comment: str = "bridge-trade",
                       magic: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Build a market order request dict for MT5.
    
    Args:
        mt5: MetaTrader5 module
        action: "BUY" or "SELL"
        symbol: Trading symbol (e.g., "EURUSD")
        volume: Lot size
        sl: Stop loss price (optional)
        tp: Take profit price (optional)
        comment: Order comment
        
    Returns:
        Order request dict or None if invalid
    """
    mt5.symbol_select(symbol, True)
    # Portable terminals start fresh — wait up to 3s for the first tick to arrive
    tick = None
    for _ in range(6):
        tick = mt5.symbol_info_tick(symbol)
        if tick and tick.bid > 0:
            break
        import time as _time
        _time.sleep(0.5)
    if not tick or tick.bid == 0:
        return None
    
    order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if action == "BUY" else tick.bid
    
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(volume),
        "type": order_type,
        "price": price,
        "comment": comment,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    if sl is not None:
        request["sl"] = float(sl)
    if tp is not None:
        request["tp"] = float(tp)
    if magic is not None:
        request["magic"] = int(magic)

    return request


def build_close_request(mt5, position, comment: str = "bridge-close") -> Dict[str, Any]:
    """
    Build a close position request dict for MT5.
    
    Args:
        mt5: MetaTrader5 module
        position: Position object from mt5.positions_get()
        comment: Order comment
        
    Returns:
        Close order request dict
    """
    close_type = mt5.ORDER_TYPE_SELL if position.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
    
    return {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": position.symbol,
        "volume": position.volume,
        "type": close_type,
        "position": position.ticket,
        "comment": comment,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }


def execute_market_order(mt5, action: str, symbol: str, volume: float,
                         sl: Optional[float] = None, tp: Optional[float] = None,
                         comment: str = "bridge-trade",
                         magic: Optional[int] = None) -> Dict[str, Any]:
    """
    Execute a market order via MT5.
    
    Args:
        mt5: MetaTrader5 module
        action: "BUY" or "SELL"
        symbol: Trading symbol
        volume: Lot size
        sl: Stop loss price (optional)
        tp: Take profit price (optional)
        comment: Order comment
        
    Returns:
        Result dict with status, order_id, error, etc.
    """
    if not symbol:
        return {"status": "failed", "error": "missing symbol"}
    
    action = action.upper()
    if action not in ("BUY", "SELL"):
        return {"status": "failed", "error": f"invalid action: {action}"}
    
    request = build_market_order(mt5, action, symbol, volume, sl, tp, comment, magic=magic)
    if not request:
        return {"status": "failed", "error": f"no tick data for {symbol}"}

    # Pick the filling mode the broker actually supports for this symbol.
    # symbol_info().filling_mode is a bitmask: bit0=FOK, bit1=IOC, bit2=RETURN.
    # Try in order: IOC → FOK → RETURN; fall through to whichever works.
    fill_modes = [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]
    try:
        info = mt5.symbol_info(symbol)
        if info and hasattr(info, "filling_mode") and info.filling_mode:
            fm = info.filling_mode
            priority = []
            if fm & 0x2: priority.append(mt5.ORDER_FILLING_IOC)
            if fm & 0x1: priority.append(mt5.ORDER_FILLING_FOK)
            if fm & 0x4: priority.append(mt5.ORDER_FILLING_RETURN)
            if priority:
                fill_modes = priority
    except Exception:
        pass

    result = None
    for fill in fill_modes:
        request["type_filling"] = fill
        result = mt5.order_send(request)
        if result and result.retcode != 10030:  # 10030 = TRADE_RETCODE_INVALID_FILL
            break

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        return {
            "status": "executed",
            "order_id": result.order,
        }
    else:
        return {
            "status": "failed",
            "error": result.comment if result else "order_send returned None",
            "retcode": result.retcode if result else -1,
            "error_message": map_mt5_retcode(result.retcode) if result else "no result",
        }


def close_positions(mt5, symbol: Optional[str] = None, 
                    comment: str = "bridge-close") -> Dict[str, Any]:
    """
    Close all positions, optionally filtered by symbol.
    
    Args:
        mt5: MetaTrader5 module
        symbol: Symbol to close (None = close all)
        comment: Order comment
        
    Returns:
        Result dict with status, order_ids, error
    """
    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    
    if not positions:
        return {"status": "failed", "error": "no open positions"}
    
    closed_orders = []
    failed_count = 0
    
    for pos in positions:
        close_req = build_close_request(mt5, pos, comment)
        # Try filling modes in order (same logic as execute_market_order)
        fill_modes = [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]
        try:
            info = mt5.symbol_info(pos.symbol)
            if info and hasattr(info, "filling_mode") and info.filling_mode:
                fm = info.filling_mode
                priority = []
                if fm & 0x2: priority.append(mt5.ORDER_FILLING_IOC)
                if fm & 0x1: priority.append(mt5.ORDER_FILLING_FOK)
                if fm & 0x4: priority.append(mt5.ORDER_FILLING_RETURN)
                if priority:
                    fill_modes = priority
        except Exception:
            pass
        result = None
        for fill in fill_modes:
            close_req["type_filling"] = fill
            result = mt5.order_send(close_req)
            if result and result.retcode != 10030:
                break
        
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            closed_orders.append(result.order)
        else:
            failed_count += 1
    
    if closed_orders:
        result = {
            "status": "executed",
            "order_ids": closed_orders,
            "closed_count": len(closed_orders),
        }
        if failed_count:
            result["failed_count"] = failed_count
        return result
    else:
        return {"status": "failed", "error": "all close requests failed"}


def pip_size_for_symbol(symbol: str) -> float:
    """Return the pip size (1 pip in price terms) for a given symbol.

    Used to convert pips-based SL/TP offsets into absolute price levels.
    Covers forex majors/minors, metals, indices, and crypto.
    """
    s = symbol.upper().replace("/", "")
    # JPY pairs: 1 pip = 0.01
    if "JPY" in s:
        return 0.01
    # Gold (XAUUSD): 1 pip = $0.1
    if s in ("XAUUSD", "GOLD"):
        return 0.1
    # Silver (XAGUSD): 1 pip = $0.01
    if s in ("XAGUSD", "SILVER"):
        return 0.01
    # Oil: 1 pip = $0.01
    if s in ("USOUSD", "UKOUSD", "BRENT", "WTI", "OIL"):
        return 0.01
    # Major indices: 1 pip = 1 index point
    if any(s.startswith(x) for x in ("US30", "NAS100", "SPX500", "GER40", "UK100", "DAX")):
        return 1.0
    # Crypto: 1 pip = $1
    if "BTC" in s or "ETH" in s:
        return 1.0
    # Default forex: 1 pip = 0.0001
    return 0.0001


def pips_to_price(symbol: str, pips: float, action: str,
                  price: float, direction: str) -> float:
    """Convert a pip offset to an absolute price level.

    Args:
        symbol:    Trading symbol
        pips:      Number of pips
        action:    "BUY" or "SELL"
        price:     Current entry price (ask for BUY, bid for SELL)
        direction: "sl" or "tp"

    Returns:
        Absolute price level
    """
    size = pip_size_for_symbol(symbol) * pips
    is_buy = action.upper() == "BUY"
    if direction == "sl":
        return round(price - size if is_buy else price + size, 5)
    else:  # tp
        return round(price + size if is_buy else price - size, 5)


def execute_command(mt5, command: Dict[str, Any],
                    comment_prefix: str = "bridge",
                    max_lot_size: Optional[float] = None) -> Dict[str, Any]:
    """
    Execute a trade command dict via MT5.

    Args:
        mt5: MetaTrader5 module
        command: Command dict with action, symbol, size, sl, tp,
                 and optionally sl_pips / tp_pips (converted using current tick)
        comment_prefix: Prefix for order comments

    Returns:
        Result dict
    """
    action = (command.get("action") or "").upper()
    symbol = command.get("symbol", "")
    size = float(command.get("size") or 0.1)
    sl = command.get("sl")
    tp = command.get("tp")
    magic = command.get("magic")
    # max_lot_size: explicit param takes priority, then command dict field
    if max_lot_size is None:
        max_lot_size = command.get("max_lot_size")
        if max_lot_size is not None:
            max_lot_size = float(max_lot_size)

    # Ensure symbol is in market watch before any tick/info queries
    if symbol and action in ("BUY", "SELL"):
        mt5.symbol_select(symbol, True)

    # FIX 12: Cap percentage lot size at 10% max per trade
    if size < 0 and action in ("BUY", "SELL"):
        raw_pct = abs(size)
        if raw_pct > 10.0:
            logger.warning(
                "Percentage lot size capped from %.1f%% to 10%%", raw_pct
            )
            size = -10.0

    # Negative size = percentage of equity (convention from cloud_bridge)
    if size < 0 and action in ("BUY", "SELL"):
        pct = abs(size) / 100.0
        account = mt5.account_info()
        symbol_info = mt5.symbol_info(symbol)
        if account and symbol_info:
            contract_size = symbol_info.trade_contract_size or 100000.0
            tick_data = mt5.symbol_info_tick(symbol)
            price = tick_data.ask if (tick_data and action == "BUY") else (tick_data.bid if tick_data else 0)
            if price > 0 and contract_size > 0:
                size = (account.equity * pct) / (contract_size * price)
            else:
                logger.warning("Cannot compute lot size for %s (price=%s contract_size=%s) — falling back to 0.01", symbol, price, contract_size)
                size = 0.01
            vol_min = symbol_info.volume_min or 0.01
            vol_max = symbol_info.volume_max or 100.0
            vol_step = symbol_info.volume_step or 0.01
            size = max(vol_min, min(size, vol_max))
            if vol_step > 0:
                size = math.floor(size / vol_step) * vol_step
                if size < vol_min:
                    # FIX 12: Return error if percentage mode yields sub-minimum lots
                    logger.warning(
                        "Percentage lot size too small for %s after rounding (%.5f < vol_min %.5f)",
                        symbol, size, vol_min,
                    )
                    return {
                        "status": "failed",
                        "error": f"Calculated lot size {size:.5f} is below minimum {vol_min} for {symbol}",
                        "retcode": -1,
                    }
        else:
            logger.warning("Cannot compute lot size for %s (no account or symbol_info) — falling back to 0.01", symbol)
            size = 0.01

    # Apply max_lot_size cap
    if max_lot_size is not None and size > 0 and size > max_lot_size:
        size = max_lot_size

    # Convert pips-based SL/TP to absolute price levels using current tick
    sl_pips = command.get("sl_pips")
    tp_pips = command.get("tp_pips")
    if action in ("BUY", "SELL") and (sl_pips or tp_pips):
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            entry_price = tick.ask if action == "BUY" else tick.bid
            if sl is None and sl_pips:
                sl = pips_to_price(symbol, float(sl_pips), action, entry_price, "sl")
            if tp is None and tp_pips:
                tp = pips_to_price(symbol, float(tp_pips), action, entry_price, "tp")

    if action in ("BUY", "SELL"):
        return execute_market_order(
            mt5, action, symbol, size, sl, tp,
            comment=f"{comment_prefix}-trade",
            magic=magic,
        )
    elif action.startswith("CLOSE"):
        close_symbol = None if action == "CLOSE_ALL" else symbol
        return close_positions(mt5, close_symbol, comment=f"{comment_prefix}-close")
    else:
        return {"status": "failed", "error": f"unknown action: {action}"}
