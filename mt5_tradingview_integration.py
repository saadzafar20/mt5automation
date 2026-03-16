print("=== mt5_tradingview_integration.py script started ===")
"""
TradingView to MT5 Trading Integration Script
Receives signals from TradingView Pine Script via HTTP and executes trades in MT5
Designed for Hostinger VPS deployment
"""

import os
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, asdict
from functools import wraps
import hashlib
import hmac
import math

# Web framework for HTTP listener
from flask import Flask, request, jsonify
from flask_cors import CORS

# MT5 trading library
import MetaTrader5 as mt5

# Utilities
import requests
from dotenv import load_dotenv

# ============================================================================
# CONFIGURATION
# ============================================================================

load_dotenv()

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('mt5_trading.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Configuration for MT5 and API"""
    # MT5 Settings
    MT5_PATH: str = os.getenv('MT5_PATH')
    MT5_LOGIN: int = int(os.getenv('MT5_LOGIN', '0'))
    MT5_PASSWORD: str = os.getenv('MT5_PASSWORD', '')
    MT5_SERVER: str = os.getenv('MT5_SERVER', '')
    MT5_TIMEOUT: int = int(os.getenv('MT5_TIMEOUT', '30000'))
    MAX_SPREAD_PIPS: float = float(os.getenv('MAX_SPREAD_PIPS', '20'))
    SLIPPAGE: int = int(os.getenv('SLIPPAGE', '50'))
    DEFAULT_LOT_SIZE: float = float(os.getenv('DEFAULT_LOT_SIZE', '0.01'))
    DEFAULT_SL_PIPS: float = float(os.getenv('DEFAULT_SL_PIPS', '50'))
    DEFAULT_TP_PIPS: float = float(os.getenv('DEFAULT_TP_PIPS', '100'))
    MIN_LOT_SIZE: float = float(os.getenv('MIN_LOT_SIZE', '0.01'))
    MAX_LOT_SIZE: float = float(os.getenv('MAX_LOT_SIZE', '0.02'))
    RISK_PER_TRADE_PCT: float = float(os.getenv('RISK_PER_TRADE_PCT', '0.01'))
    MAX_DAILY_LOSS_PCT: float = float(os.getenv('MAX_DAILY_LOSS_PCT', '0.05'))
    
    # API Settings
    API_HOST: str = os.getenv('API_HOST', '0.0.0.0')
    API_PORT: int = int(os.getenv('API_PORT', '5000'))
    API_KEY: str = os.getenv('API_KEY', '')  # For webhook validation
    
    # Notifications
    TELEGRAM_TOKEN: str = os.getenv('TELEGRAM_TOKEN', '')
    TELEGRAM_CHAT_ID: str = os.getenv('TELEGRAM_CHAT_ID', '')
    ENABLE_NOTIFICATIONS: bool = os.getenv('ENABLE_NOTIFICATIONS', 'True') == 'True'
    # SL/TP fallback as pct of account balance (e.g. 0.05 = 5%) when SL/TP missing/zero
    SL_TP_BALANCE_PCT: float = float(os.getenv('SL_TP_BALANCE_PCT', '0.05'))
    # Symbol aliases for brokers using short codes (JSON map e.g. {"BTCUSD": "BTC"})
    SYMBOL_ALIASES: str = os.getenv('SYMBOL_ALIASES', '{}')


@dataclass
class Signal:
    """Trading signal structure"""
    symbol: str
    action: str  # BUY, SELL, CLOSE_BUY, CLOSE_SELL, CLOSE_ALL
    lot_size: Optional[float] = None
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    timeframe: str = 'H1'
    comment: str = ''
    timestamp: str = ''
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


# ============================================================================
# MT5 MANAGER CLASS
# ============================================================================

class MT5Manager:
    """Manages MT5 connection and trading operations"""
    
    def __init__(self, config: Config):
        self.config = config
        self.connected = False
        self.account_info = None
        # Optional symbol suffix (e.g. .m, .pro)
        self.symbol_suffix = os.getenv('SYMBOL_SUFFIX', '')
        # Cache resolved symbols to avoid repeated lookups
        self.symbol_cache = {}
        self.symbol_aliases = self._load_symbol_aliases(config.SYMBOL_ALIASES)

    @staticmethod
    def _load_symbol_aliases(raw: str) -> Dict[str, str]:
        """Parse alias JSON safely, uppercasing keys for matching."""
        try:
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                return {}
            return {str(k).upper(): str(v) for k, v in data.items()}
        except Exception as exc:
            logger.warning(f"Failed to parse SYMBOL_ALIASES env: {exc}")
            return {}
    
    def connect(self) -> bool:
        """Connects to the already running MT5 terminal"""
        try:
            # No path / login: attach to open terminal
            if not mt5.initialize():
                logger.error(f"MT5 initialization failed: {mt5.last_error()}")
                self.connected = False
                return False
            
            self.account_info = mt5.account_info()
            if self.account_info is None:
                logger.error("Failed to get account info")
                self.connected = False
                return False
            
            self.connected = True
            logger.info(f"Connected to MT5: {self.account_info.name} ({self.account_info.login})")
            return True
        except Exception as e:
            logger.error(f"MT5 connection error: {str(e)}")
            self.connected = False
            return False
    
    def disconnect(self):
        """Close MT5 connection"""
        if self.connected:
            mt5.shutdown()
            self.connected = False
            logger.info("Disconnected from MT5")

    def get_filling_mode(self, symbol: str) -> int:

        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            return mt5.ORDER_FILLING_FOK  # Safe default
            
        # Check what the broker supports
        filling = symbol_info.filling_mode
        
        # Priority: FOK (1) -> IOC (2) -> RETURN
        # We use integers 1 and 2 because older MT5 libs miss the constants
        if filling & 1:  # 1 is SYMBOL_FILLING_FOK
            return mt5.ORDER_FILLING_FOK
        if filling & 2:  # 2 is SYMBOL_FILLING_IOC
            return mt5.ORDER_FILLING_IOC
            
        return mt5.ORDER_FILLING_RETURN

    def _apply_alias(self, symbol: str) -> Optional[str]:
        """Return alias if defined for the incoming symbol key."""
        if not symbol:
            return None
        key = symbol.upper()
        alias = self.symbol_aliases.get(key)
        if alias:
            alias_up = alias.upper()
            logger.info(f"Alias remap: {key} -> {alias_up}")
            return alias_up
        return None

    def _with_suffix(self, symbol: str) -> str:
        """Resolve broker-specific symbol (alias/suffix/prefix) with caching."""
        if not symbol:
            return symbol

        requested = symbol.upper()
        aliased = self._apply_alias(requested) or requested

        # Cache is keyed by the incoming requested symbol to preserve caller intent
        cached = self.symbol_cache.get(requested)
        if cached:
            return cached

        base = aliased

        # 1) Direct match
        direct = mt5.symbol_info(base)
        if direct:
            mt5.symbol_select(base, True)
            self.symbol_cache[requested] = base
            return base

        # 2) Explicit configured suffix
        if self.symbol_suffix:
            candidate = f"{base}{self.symbol_suffix}"
            info = mt5.symbol_info(candidate)
            if info:
                mt5.symbol_select(candidate, True)
                self.symbol_cache[requested] = candidate
                logger.info(f"Configured suffix mapped {base} -> {candidate}")
                return candidate

        # 3) Wildcard search across broker symbols (includes hidden ones)
        candidates = mt5.symbols_get(f"*{base}*") or []
        best_match = None
        for s in candidates:
            name = s.name
            if base in name:
                if best_match is None or len(name) < len(best_match):
                    best_match = name

        if best_match:
            mt5.symbol_select(best_match, True)
            self.symbol_cache[requested] = best_match
            logger.info(f"Auto-mapped symbol {base} -> {best_match}")
            return best_match

        # 4) Fallback to original
        self.symbol_cache[requested] = requested
        return requested

    @staticmethod
    def _get_pip_size(symbol_info) -> float:
        """Derive pip size from MT5 symbol info."""
        if symbol_info is None:
            return 0.0
        point = symbol_info.point or 0.0
        digits = symbol_info.digits
        if digits in (3, 5):
            return point * 10
        return point

    @staticmethod
    def _safe_float(val, default: float) -> float:
        """Coerce values to float, falling back to default on None/invalid."""
        if val is None or val == "":
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float_optional(val) -> Optional[float]:
        """Coerce values to float or return None when missing/invalid."""
        if val is None or val == "":
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    def _clamp_volume(self, volume: float, symbol_info) -> float:
        """Clamp volume to broker limits and configured min/max."""
        min_vol = max(self.config.MIN_LOT_SIZE, symbol_info.volume_min or 0.0)
        max_vol = min(self.config.MAX_LOT_SIZE, symbol_info.volume_max or self.config.MAX_LOT_SIZE)
        if max_vol < min_vol:
            max_vol = min_vol

        vol = min(max(volume, min_vol), max_vol)
        step = symbol_info.volume_step or 0.0
        if step > 0:
            vol = math.floor(vol / step) * step
            if vol < min_vol:
                vol = min_vol
        return vol
    
    def send_order(self, signal: Signal) -> Optional[int]:
        """Send order to MT5 and return order ticket on success"""
        try:
            if not self.connected:
                # One quick reconnect attempt
                if not self.connect():
                    logger.error("MT5 not connected and reconnect failed")
                    return None

            trade_symbol = self._with_suffix(signal.symbol)

            symbol_info = mt5.symbol_info(trade_symbol)
            if not symbol_info:
                logger.error(f"Symbol {trade_symbol} not found in Market Watch. Check suffix/visibility.")
                return None

            if not symbol_info.visible:
                if not mt5.symbol_select(trade_symbol, True):
                    logger.error(f"Failed to select symbol {trade_symbol}")
                    return None

            order_type = mt5.ORDER_TYPE_BUY if signal.action == 'BUY' else mt5.ORDER_TYPE_SELL

            tick = mt5.symbol_info_tick(trade_symbol)
            if tick is None:
                logger.error(f"No tick data for symbol {trade_symbol}")
                return None

            price = tick.ask if signal.action == 'BUY' else tick.bid

            pip_size = self._get_pip_size(symbol_info)
            default_sl = self.config.DEFAULT_SL_PIPS * pip_size
            default_tp = self.config.DEFAULT_TP_PIPS * pip_size

            sl_value = signal.stop_loss
            tp_value = signal.take_profit

            # Preliminary lot for SL/TP sizing when provided lot is absent
            provisional_lot = self._safe_float_optional(signal.lot_size)
            if provisional_lot is None:
                provisional_lot = self.config.DEFAULT_LOT_SIZE

            account = mt5.account_info()
            contract_size = symbol_info.trade_contract_size or 0.0
            risk_amount = 0.0
            if account:
                risk_amount = account.equity * self.config.SL_TP_BALANCE_PCT

            # If SL/TP missing/zero, first try monetary 5% balance-based distance; else use pip defaults
            use_default_sl = sl_value is None or sl_value == 0.0
            use_default_tp = tp_value is None or tp_value == 0.0

            if (use_default_sl or use_default_tp) and risk_amount > 0 and contract_size > 0 and provisional_lot > 0:
                sl_distance = risk_amount / (provisional_lot * contract_size)
                if use_default_sl:
                    sl_value = price - sl_distance if signal.action == 'BUY' else price + sl_distance
                if use_default_tp:
                    tp_value = price + sl_distance if signal.action == 'BUY' else price - sl_distance
            else:
                if use_default_sl and pip_size > 0:
                    sl_value = price - default_sl if signal.action == 'BUY' else price + default_sl
                if use_default_tp and pip_size > 0:
                    tp_value = price + default_tp if signal.action == 'BUY' else price - default_tp

            if sl_value is None:
                sl_value = 0.0
            if tp_value is None:
                tp_value = 0.0

            lot_size = self._safe_float_optional(signal.lot_size)
            if lot_size is None:
                sl_distance = abs(price - sl_value) if sl_value else 0.0
                contract_size = symbol_info.trade_contract_size or 0.0
                account = mt5.account_info()

                if account and sl_distance > 0 and contract_size > 0:
                    risk_amount = account.equity * self.config.RISK_PER_TRADE_PCT
                    lot_size = risk_amount / (sl_distance * contract_size)
                else:
                    lot_size = self.config.DEFAULT_LOT_SIZE

            lot_size = self._clamp_volume(float(lot_size), symbol_info)

            # sl_value/tp_value already resolved above

            request_dict = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": trade_symbol,
                "volume": lot_size,
                "type": order_type,
                "price": price,
                "sl": float(sl_value),
                "tp": float(tp_value),
                "deviation": self.config.SLIPPAGE,
                "magic": 1234567,
                "comment": f"{signal.comment}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self.get_filling_mode(trade_symbol),
            }

            logger.info(f"Sending Order: {trade_symbol} {signal.action} {lot_size} @ {price}")
            result = mt5.order_send(request_dict)

            if result is None:
                logger.error(f"mt5.order_send returned None: {mt5.last_error()}")
                return None

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                logger.error(f"Order failed: {result.comment} ({result.retcode})")
                return None

            logger.info(f"Order SUCCESS: {trade_symbol} {signal.action}, ticket={result.order}")
            return result.order
        except Exception as e:
            logger.error(f"Error sending order: {str(e)}")
            return None

    def close_position(self, symbol: str, order_type: str = 'BUY') -> bool:
        """Close an open position for the given symbol and direction"""
        try:
            trade_symbol = self._with_suffix(symbol)

            positions = mt5.positions_get(symbol=trade_symbol)
            if not positions:
                return False

            for pos in positions:
                if (order_type == 'BUY' and pos.type == 0) or (order_type == 'SELL' and pos.type == 1):
                    tick = mt5.symbol_info_tick(trade_symbol)
                    if tick is None:
                        logger.error(f"No tick data for symbol {trade_symbol} when closing position")
                        continue

                    price = tick.bid if pos.type == 0 else tick.ask

                    close_request = {
                        "action": mt5.TRADE_ACTION_DEAL,
                        "symbol": trade_symbol,
                        "volume": pos.volume,
                        "type": mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY,
                        "position": pos.ticket,
                        "price": price,
                        "magic": 1234567,
                        "comment": "Close position",
                        "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": self.get_filling_mode(trade_symbol),
                    }

                    result = mt5.order_send(close_request)
                    if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                        logger.info(f"Position closed: {trade_symbol} {pos.volume} lots")
                        return True

            return False
        except Exception as e:
            logger.error(f"Error closing position: {str(e)}")
            return False

    def get_account_stats(self) -> Dict:
        """Get current account statistics"""
        try:
            account = mt5.account_info()
            if account is None:
                logger.error("account_info returned None")
                return {}
            return {
                'balance': account.balance,
                'equity': account.equity,
                'profit': account.profit,
                'margin': account.margin,
                'margin_free': account.margin_free,
                'margin_level': account.margin_level,
            }
        except Exception as e:
            logger.error(f"Error getting account stats: {str(e)}")
            return {}


# ============================================================================
# FLASK APP SETUP
# ============================================================================

app = Flask(__name__)
CORS(app)


# Initialize MT5 Manager
config = Config()
mt5_manager = MT5Manager(config)

# Global state
trades_history = []
last_signal_time = None
daily_start_date = None
daily_start_equity = None
trading_halted = False

# Flask error handler for all exceptions
@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    print("Flask caught exception:", e)
    traceback.print_exc()
    return jsonify({'error': str(e), 'type': str(type(e))}), 500


def verify_webhook(f):
    """Decorator to verify webhook signature"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not config.API_KEY:
            return f(*args, **kwargs)
        
        # Get signature from header
        signature = request.headers.get('X-Signature', '')
        
        # Get request body
        data = request.get_data(as_text=True)
        
        # Calculate expected signature
        expected_signature = hmac.new(
            config.API_KEY.encode(),
            data.encode(),
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(signature, expected_signature):
            logger.warning("Invalid webhook signature")
            return jsonify({'error': 'Invalid signature'}), 401
        
        return f(*args, **kwargs)
    
    return decorated_function


def _reset_daily_limits_if_needed() -> bool:
    """Reset daily loss tracking on new UTC day."""
    global daily_start_date, daily_start_equity, trading_halted

    today = datetime.now(timezone.utc).date()
    if daily_start_date != today:
        account = mt5.account_info()
        if account is None:
            return False
        daily_start_date = today
        daily_start_equity = account.equity
        trading_halted = False
    return True


def _check_daily_loss_limit() -> Tuple[bool, str]:
    """Return (allowed, message) based on max daily loss percentage."""
    global trading_halted

    if not _reset_daily_limits_if_needed():
        return False, "Failed to fetch account info for daily limits"

    account = mt5.account_info()
    if account is None or daily_start_equity is None or daily_start_equity <= 0:
        return False, "Failed to fetch account info for daily limits"

    drawdown = (daily_start_equity - account.equity) / daily_start_equity
    if drawdown >= config.MAX_DAILY_LOSS_PCT:
        trading_halted = True
        return False, f"Daily loss limit reached ({drawdown:.2%})"

    return True, ""


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'online',
        'mt5_connected': mt5_manager.connected,
        'timestamp': datetime.now(timezone.utc).isoformat()
    })


@app.route('/signal', methods=['POST'])
def receive_signal():
    """Receive trading signal from TradingView"""
    global last_signal_time
    
    # Helpers to handle optional numeric fields
    def safe_float(val, default=0.0):
        if val is None or val == "":
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def optional_float(val) -> Optional[float]:
        if val is None or val == "":
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    try:
        data = request.get_json(silent=True)
        if data is None:
            raw_body = request.get_data(as_text=True).strip()
            if raw_body:
                try:
                    data = json.loads(raw_body)
                except json.JSONDecodeError:
                    return jsonify({'error': 'Invalid JSON payload'}), 400
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Parse signal (handle different TradingView formats)
        raw_action = data.get('action') or data.get('type')
        if not raw_action:
            return jsonify({'error': 'Missing action'}), 400

        # Extract values using the safe_float helper
        # Support lot_size_pct (percentage of equity) or legacy lot_size (absolute lots)
        lot_size_pct_raw = data.get('lot_size_pct')
        lot_val = None
        _pct_mode = False
        if lot_size_pct_raw is not None:
            _pct_mode = True
            # Will resolve to lots after account/symbol lookup in send_order
            lot_val = lot_size_pct_raw  # keep raw; resolved below
        else:
            lot_val = data.get('lot_size') or data.get('size')

        tp_val = data.get('take_profit') or data.get('tp')
        sl_val = data.get('stop_loss') or data.get('sl')

        signal = Signal(
            symbol=data.get('symbol', '').upper(),
            action=raw_action.upper(),
            lot_size=optional_float(lot_val),
            take_profit=optional_float(tp_val),
            stop_loss=optional_float(sl_val),
            timeframe=data.get('timeframe', 'H1'),
            comment=data.get('comment', 'TV Signal')
        )

        # If percentage mode, convert lot_size from % of equity to absolute lots
        if _pct_mode and signal.lot_size is not None and mt5_manager.connected:
            pct = max(0.1, min(signal.lot_size, 100.0)) / 100.0
            account = mt5.account_info()
            trade_symbol = mt5_manager._with_suffix(signal.symbol)
            symbol_info = mt5.symbol_info(trade_symbol)
            if account and symbol_info:
                contract_size = symbol_info.trade_contract_size or 100000.0
                tick_data = mt5.symbol_info_tick(trade_symbol)
                price = tick_data.ask if (tick_data and signal.action == "BUY") else (tick_data.bid if tick_data else 0)
                if price > 0 and contract_size > 0:
                    signal.lot_size = (account.equity * pct) / (contract_size * price)
                else:
                    signal.lot_size = config.DEFAULT_LOT_SIZE
                logger.info(f"Percentage lot ({pct*100:.1f}%) resolved to {signal.lot_size:.4f} lots")
            else:
                signal.lot_size = config.DEFAULT_LOT_SIZE

        logger.info(f"Signal Received: {signal}")

        # Process signal: open trades
        if signal.action in ['BUY', 'SELL']:
            if not mt5_manager.connected:
                if not mt5_manager.connect():
                    return jsonify({'status': 'failed', 'error': 'MT5 not connected'}), 503

            allowed, message = _check_daily_loss_limit()
            if not allowed:
                logger.warning(message)
                if config.ENABLE_NOTIFICATIONS:
                    send_notification(f"🛑 Trading halted: {message}")
                return jsonify({'status': 'failed', 'error': message}), 403

            # Note: mt5_manager.send_order will use the updated _with_suffix internally
            order_id = mt5_manager.send_order(signal)

            if order_id:
                trades_history.append(asdict(signal))
                last_signal_time = datetime.now(timezone.utc)

                if config.ENABLE_NOTIFICATIONS:
                    send_notification(f"✅ Order executed: {signal.symbol} {signal.action}")

                return jsonify({'status': 'success', 'order_id': order_id}), 200
            else:
                # mt5_manager logs the specific reason (suffix/visibility/balance)
                return jsonify({'status': 'failed', 'error': 'MT5 rejected order. Check logs for details.'}), 400

        # Process signal: close specific side
        if signal.action in ['CLOSE_BUY', 'CLOSE_SELL']:
            order_type = signal.action.split('_')[1]
            success = mt5_manager.close_position(signal.symbol, order_type)

            if success and config.ENABLE_NOTIFICATIONS:
                send_notification(f"🔴 Position closed: {signal.symbol}")

            return jsonify({
                'status': 'success' if success else 'failed',
                'action': 'position_closed'
            }), 200 if success else 400

        return jsonify({'status': 'ignored', 'message': 'Action not handled'}), 200

    except Exception as e:
        logger.error(f"Error processing signal: {str(e)}")
        return jsonify({'error': str(e)}), 500
        
@app.route('/account', methods=['GET'])
def get_account():
    """Get account information and statistics"""
    try:
        stats = mt5_manager.get_account_stats()
        return jsonify({
            'status': 'success',
            'account': stats
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/positions', methods=['GET'])
def get_positions():
    """Get all open positions"""
    try:
        if not mt5_manager.connected:
            return jsonify({'error': 'MT5 not connected'}), 503
        
        positions = mt5.positions_get()
        
        positions_list = []
        if positions:
            for pos in positions:
                tick = mt5.symbol_info_tick(pos.symbol)
                positions_list.append({
                    'ticket': pos.ticket,
                    'symbol': pos.symbol,
                    'type': 'BUY' if pos.type == 0 else 'SELL',
                    'volume': pos.volume,
                    'open_price': pos.price_open,
                    'current_price': tick.bid if tick else None,
                    'profit': pos.profit,
                    'comment': pos.comment
                })
        
        return jsonify({
            'status': 'success',
            'positions': positions_list,
            'count': len(positions_list)
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/history', methods=['GET'])
def get_history():
    """Get trading history from signals"""
    limit = request.args.get('limit', 50, type=int)
    return jsonify({
        'status': 'success',
        'history': trades_history[-limit:],
        'count': len(trades_history)
    }), 200


@app.route('/stats', methods=['GET'])
def get_stats():
    """Get trading statistics"""
    try:
        if not mt5_manager.connected:
            return jsonify({'error': 'MT5 not connected'}), 503
        
        account = mt5.account_info()
        if account is None:
            return jsonify({'error': 'Failed to fetch account info'}), 503
        positions = mt5.positions_get()
        
        return jsonify({
            'status': 'success',
            'balance': account.balance,
            'equity': account.equity,
            'profit': account.profit,
            'open_positions': len(positions) if positions else 0,
            'total_signals': len(trades_history),
            'last_signal': last_signal_time.isoformat() if last_signal_time else None
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal error: {str(error)}")
    return jsonify({'error': 'Internal server error'}), 500


# ============================================================================
# NOTIFICATION FUNCTIONS
# ============================================================================

def send_notification(message: str):
    """Send Telegram notification"""
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        return
    
    try:
        url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
        data = {
            'chat_id': config.TELEGRAM_CHAT_ID,
            'text': f"{message}\n\n__{datetime.now(timezone.utc).isoformat()}__",
            'parse_mode': 'Markdown'
        }
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        logger.error(f"Failed to send notification: {str(e)}")


# ============================================================================
# MAIN EXECUTION
# ============================================================================

def startup():
    """Initialize the application"""
    logger.info("Starting MT5 TradingView Integration...")
    
    # Try to connect to MT5
    if not mt5_manager.connect():
        logger.warning("Failed to connect to MT5. Server will still start - retrying MT5 connection...")
        # Start a background thread to retry MT5 connection
        def retry_mt5():
            while not mt5_manager.connected:
                time.sleep(10)
                logger.info("Retrying MT5 connection...")
                mt5_manager.connect()
        
        retry_thread = threading.Thread(target=retry_mt5, daemon=True)
        retry_thread.start()
    else:
        logger.info(f"MT5 connection successful")
    
    return True


def shutdown():
    """Cleanup on shutdown"""
    logger.info("Shutting down...")
    mt5_manager.disconnect()


if __name__ == '__main__':
    try:
        if startup():
            # Run Flask app
            app.run(
                host=config.API_HOST,
                port=config.API_PORT,
                debug=False,
                threaded=True
            )
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        shutdown()
