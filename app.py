from fastapi import FastAPI, Request, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from cachetools import TTLCache
import time
import logging
from pathlib import Path
import os

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Separate caches for different types of data
ohlcv_cache = TTLCache(maxsize=100, ttl=300)  # 5 minutes for OHLCV data
exchange_cache = TTLCache(maxsize=20, ttl=3600)  # 1 hour for exchange instances

# Add at the top with other globals
last_update_time = None
FORCE_UPDATE_AFTER = timedelta(hours=24)  # Force update after 24 hours

# Add these constants at the top
STATIC_DIR = Path("static")
STATIC_FILE = STATIC_DIR / "index.html"

# Create static directory if it doesn't exist
STATIC_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.DEBUG)

def calculate_ema(data, periods):
    return pd.Series(data).ewm(span=periods, adjust=False).mean().iloc[-1]

def get_cached_data(key, fetch_func, cache_store=ohlcv_cache):
    """Get data from cache or fetch if not available"""
    if key not in cache_store:
        try:
            cache_store[key] = fetch_func()
            time.sleep(0.5)  # Rate limiting
        except Exception as e:
            logging.error(f"Error fetching data for {key}: {str(e)}")
            return {"error": str(e)}
    return cache_store[key]

def get_btc_price():
    """Get current BTC/USD price to use for conversions"""
    def fetch():
        exchange = ccxt.coinbase()
        btc_ohlcv = exchange.fetch_ohlcv('BTC/USD', '1d', limit=50)
        return [x[4] for x in btc_ohlcv]
    
    return get_cached_data('btc_price', fetch)

def get_exchange_for_asset(base_symbol, preferred_exchange=None):
    """Choose appropriate exchange based on the asset"""
    if preferred_exchange:
        return getattr(ccxt, preferred_exchange)()
    elif base_symbol in ["BTC", "ETH", "SOL"]:
        return ccxt.coinbase()
    elif base_symbol == "BANANA":
        # Try MEXC first, then Gate, then Bitget for BANANA
        exchanges_to_try = ['mexc', 'gate', 'bitget']
        for exchange_id in exchanges_to_try:
            try:
                exchange = getattr(ccxt, exchange_id)()
                exchange.load_markets()
                return exchange
            except Exception:
                continue
        raise Exception("Could not connect to any exchange for BANANA")
    elif base_symbol == "TIG":
        return ccxt.xt()
    elif base_symbol == "NATIX":
        exchanges_to_try = ['kucoin', 'gate', 'mexc']
        for exchange_id in exchanges_to_try:
            try:
                exchange = getattr(ccxt, exchange_id)()
                exchange.load_markets()
                return exchange
            except Exception:
                continue
        raise Exception("Could not connect to any exchange for NATIX")
    elif base_symbol == "FAI":
        exchanges_to_try = ['bitmart', 'bingx']
        for exchange_id in exchanges_to_try:
            try:
                exchange = getattr(ccxt, exchange_id)()
                exchange.load_markets()
                return exchange
            except Exception:
                continue
        raise Exception("Could not connect to any exchange for FAI")
    else:
        return ccxt.coinbase()

def calculate_performance(closes, days):
    """Calculate percentage performance over X days"""
    try:
        if len(closes) >= days:
            current_price = closes[-1]
            past_price = closes[-days]
            if past_price != 0:  # Prevent division by zero
                perf = ((current_price - past_price) / past_price) * 100
                return perf
    except Exception:
        pass
    return None

def get_trend_analysis(base_symbol, quote_symbol="USD", chain=None, preferred_exchange=None):
    cache_key = f"{base_symbol}_{quote_symbol}"
    
    def fetch_analysis():
        try:
            exchange = get_exchange_for_asset(base_symbol, preferred_exchange)
        except Exception as e:
            return {
                "symbol": f"{base_symbol}/{quote_symbol}",
                "error": str(e),
                "chain": chain
            }
        
        try:
            # Handle different quote currencies based on exchange
            if exchange.id == 'coinbase':
                actual_quote = "USD"
            else:
                actual_quote = "USDT"
                
            symbol_pair = f"{base_symbol}/{actual_quote}"
            
            # Special handling for specific tokens and their symbol formats
            if base_symbol == "BANANA":
                if exchange.id == 'mexc':
                    symbol_pair = "BANANA/USDT"
                elif exchange.id == 'gate':
                    symbol_pair = "BANANA_USDT"
                elif exchange.id == 'bitget':
                    symbol_pair = "BANANA/USDT"
            elif base_symbol == "TIG":
                symbol_pair = "TIG/USDT"
            elif base_symbol == "NATIX":
                if exchange.id == 'kucoin':
                    symbol_pair = "NATIX-USDT"
                elif exchange.id == 'gate':
                    symbol_pair = "NATIX_USDT"
            elif base_symbol == "FAI":
                if exchange.id == 'bitmart':
                    symbol_pair = "FAI/USDT"
                elif exchange.id == 'bingx':
                    symbol_pair = "FAI/USDT"
            
            if quote_symbol == "BTC":
                # Get token's USD/USDT price
                token_ohlcv = exchange.fetch_ohlcv(symbol_pair, '1d', limit=50)
                if not token_ohlcv:
                    return {
                        "symbol": symbol_pair,
                        "error": "No price data available",
                        "chain": chain,
                        "exchange": exchange.id
                    }
                
                # Get BTC's USD price from Coinbase
                btc_exchange = ccxt.coinbase()
                btc_ohlcv = btc_exchange.fetch_ohlcv('BTC/USD', '1d', limit=50)
                
                # Calculate BTC ratio
                closes = []
                for i in range(min(len(token_ohlcv), len(btc_ohlcv))):
                    if exchange.id == 'coinbase':
                        token_price = token_ohlcv[i][4]  # Already in USD
                    else:
                        token_price = token_ohlcv[i][4]  # USDT price (approximately equal to USD)
                    btc_price = btc_ohlcv[i][4]
                    closes.append(token_price / btc_price)
            else:
                # Normal OHLCV fetch for USD/USDT pairs
                ohlcv = exchange.fetch_ohlcv(symbol_pair, '1d', limit=50)
                if not ohlcv:
                    return {
                        "symbol": symbol_pair,
                        "error": f"No data available on {exchange.id}",
                        "chain": chain,
                        "exchange": exchange.id
                    }
                closes = [x[4] for x in ohlcv]
            
            ema8 = calculate_ema(closes, 8)
            ema20 = calculate_ema(closes, 20)
            current_price = closes[-1]
            
            # Calculate performance with safety checks
            perf_7d = calculate_performance(closes, 7)
            perf_14d = calculate_performance(closes, 14)
            
            return {
                "symbol": symbol_pair,
                "current_price": current_price,
                "ema8": ema8,
                "ema20": ema20,
                "is_uptrend": ema8 > ema20,
                "trend_text": "Uptrend" if ema8 > ema20 else "Downtrend",
                "quote_currency": quote_symbol,
                "chain": chain,
                "exchange": exchange.id,
                "is_calculated": quote_symbol == "BTC",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "perf_7d": perf_7d if perf_7d is not None else None,
                "perf_14d": perf_14d if perf_14d is not None else None
            }
        except Exception as e:
            return {
                "symbol": f"{base_symbol}/{quote_symbol}",
                "error": f"Error on {exchange.id}: {str(e)}",
                "chain": chain,
                "exchange": exchange.id
            }
    
    return get_cached_data(cache_key, fetch_analysis)

@app.get("/update")
async def update_data(request: Request):
    """Endpoint to update the static file"""
    try:
        logging.info("Starting data update...")
        # Clear the caches
        ohlcv_cache.clear()
        exchange_cache.clear()
        
        # Your existing data fetching code here
        portfolio_assets = [
            {"symbol": "BTC", "chain": None},
            {"symbol": "ETH", "chain": None},
            {"symbol": "SOL", "chain": None},
            {"symbol": "BANANA", "chain": "ethereum"},
            {"symbol": "NATIX", "chain": "solana"},
            {"symbol": "TIG", "chain": "base"},
            {"symbol": "FAI", "chain": "base"}
        ]
        
        # Full watchlist assets
        watchlist_assets = [
            {"symbol": "AAVE", "chain": None, "preferred_exchange": "coinbase"},
            {"symbol": "BNB", "chain": None, "preferred_exchange": "mexc"},
            {"symbol": "TAO", "chain": None, "preferred_exchange": "coinbase"},
            {"symbol": "JUP", "chain": None, "preferred_exchange": "mexc"}
        ]
        
        # Process portfolio assets
        portfolio_analysis = []
        for asset in portfolio_assets:
            try:
                if asset["symbol"] == "BTC":
                    usdt_analysis = get_trend_analysis(asset["symbol"], "USD", asset["chain"])
                    portfolio_analysis.append({
                        "asset": asset["symbol"],
                        "chain": asset["chain"],
                        "usdt": usdt_analysis,
                        "btc": {"symbol": "BTC/BTC", "error": "Same asset"}
                    })
                else:
                    usdt_analysis = get_trend_analysis(asset["symbol"], "USD", asset["chain"])
                    time.sleep(0.5)  # Rate limiting between analyses
                    btc_analysis = get_trend_analysis(asset["symbol"], "BTC", asset["chain"])
                    portfolio_analysis.append({
                        "asset": asset["symbol"],
                        "chain": asset["chain"],
                        "usdt": usdt_analysis,
                        "btc": btc_analysis
                    })
            except Exception as e:
                logging.error(f"Error processing portfolio asset {asset['symbol']}: {str(e)}")
                portfolio_analysis.append({
                    "asset": asset["symbol"],
                    "chain": asset["chain"],
                    "usdt": {"error": f"Failed to fetch data: {str(e)}"},
                    "btc": {"error": f"Failed to fetch data: {str(e)}"}
                })
        
        # Process watchlist assets
        watchlist_analysis = []
        for asset in watchlist_assets:
            try:
                usdt_analysis = get_trend_analysis(
                    asset["symbol"], 
                    "USD", 
                    asset["chain"], 
                    preferred_exchange=asset.get("preferred_exchange")
                )
                time.sleep(0.5)  # Rate limiting between analyses
                btc_analysis = get_trend_analysis(
                    asset["symbol"], 
                    "BTC", 
                    asset["chain"],
                    preferred_exchange=asset.get("preferred_exchange")
                )
                watchlist_analysis.append({
                    "asset": asset["symbol"],
                    "chain": asset["chain"],
                    "usdt": usdt_analysis,
                    "btc": btc_analysis
                })
            except Exception as e:
                logging.error(f"Error processing watchlist asset {asset['symbol']}: {str(e)}")
                watchlist_analysis.append({
                    "asset": asset["symbol"],
                    "chain": asset["chain"],
                    "usdt": {"error": f"Failed to fetch data: {str(e)}"},
                    "btc": {"error": f"Failed to fetch data: {str(e)}"}
                })
        
        # Generate the HTML
        html_content = templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "portfolio_analysis": portfolio_analysis,
                "watchlist_analysis": watchlist_analysis,
                "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        )
        
        # Save the rendered HTML to a static file
        STATIC_FILE.write_text(html_content.body.decode())
        logging.info("Data update completed successfully")
        
        return {"status": "success", "timestamp": datetime.now().isoformat()}
    except Exception as e:
        logging.error(f"Error updating data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Serve the static file"""
    if not STATIC_FILE.exists():
        # If static file doesn't exist, generate it
        await update_data(request)
    
    # Always serve the static file
    return HTMLResponse(STATIC_FILE.read_text()) 