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
    
    # API Settings
    API_HOST: str = os.getenv('API_HOST', '0.0.0.0')
    API_PORT: int = int(os.getenv('API_PORT', '5000'))
    API_KEY: str = os.getenv('API_KEY', '')  # For webhook validation
    
    # Notifications
    TELEGRAM_TOKEN: str = os.getenv('TELEGRAM_TOKEN', '')
    TELEGRAM_CHAT_ID: str = os.getenv('TELEGRAM_CHAT_ID', '')
    ENABLE_NOTIFICATIONS: bool = os.getenv('ENABLE_NOTIFICATIONS', 'True') == 'True'


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

    def _with_suffix(self, symbol: str) -> str:
        """Append configured suffix to symbol if needed"""
        if self.symbol_suffix and not symbol.endswith(self.symbol_suffix):
            return symbol + self.symbol_suffix
        return symbol

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

            lot_size = float(signal.lot_size or self.config.DEFAULT_LOT_SIZE)

            request_dict = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": trade_symbol,
                "volume": lot_size,
                "type": order_type,
                "price": price,
                "sl": float(signal.stop_loss) if signal.stop_loss else 0.0,
                "tp": float(signal.take_profit) if signal.take_profit else 0.0,
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
# @verify_webhook  # Disabled for direct TradingView webhooks
def receive_signal():
    """Receive trading signal from TradingView"""
    global last_signal_time
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Parse signal (handle different TradingView formats)
        raw_action = data.get('action') or data.get('type')
        if not raw_action:
            return jsonify({'error': 'Missing action'}), 400

        signal = Signal(
            symbol=data.get('symbol', '').upper(),
            action=raw_action.upper(),
            lot_size=float(data.get('lot_size', 0.01)),
            take_profit=float(data.get('take_profit', 0.0)),
            stop_loss=float(data.get('stop_loss', 0.0)),
            timeframe=data.get('timeframe', 'H1'),
            comment=data.get('comment', 'TV Signal')
        )

        logger.info(f"Signal Received: {signal}")

        # Process signal: open trades
        if signal.action in ['BUY', 'SELL']:
            order_id = mt5_manager.send_order(signal)

            if order_id:
                trades_history.append(asdict(signal))
                last_signal_time = datetime.now(timezone.utc)

                if config.ENABLE_NOTIFICATIONS:
                    send_notification(f"✅ Order executed: {signal.symbol} {signal.action}")

                return jsonify({'status': 'success', 'order_id': order_id}), 200
            else:
                logger.error("Order execution failed: MT5 rejected order")
                return jsonify({'status': 'failed', 'error': 'MT5 rejected order'}), 400

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

        # Process signal: close all positions (placeholder – implement if needed)
        if signal.action == 'CLOSE_ALL':
            return jsonify({
                'status': 'success',
                'action': 'all_positions_closing'
            }), 200

        # Any other action is currently ignored
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
                positions_list.append({
                    'ticket': pos.ticket,
                    'symbol': pos.symbol,
                    'type': 'BUY' if pos.type == 0 else 'SELL',
                    'volume': pos.volume,
                    'open_price': pos.price_open,
                    'current_price': mt5.symbol_info_tick(pos.symbol).bid,
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
