from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from cachetools import TTLCache
import time

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Cache for 5 minutes
cache = TTLCache(maxsize=100, ttl=300)

def calculate_ema(data, periods):
    return pd.Series(data).ewm(span=periods, adjust=False).mean().iloc[-1]

def get_cached_data(key, fetch_func):
    """Get data from cache or fetch if not available"""
    if key not in cache:
        cache[key] = fetch_func()
        time.sleep(0.1)  # Rate limiting
    return cache[key]

def get_btc_price():
    """Get current BTC/USD price to use for conversions"""
    def fetch():
        exchange = ccxt.coinbase()
        btc_ohlcv = exchange.fetch_ohlcv('BTC/USD', '1d', limit=50)
        return [x[4] for x in btc_ohlcv]
    
    return get_cached_data('btc_price', fetch)

def get_exchange_for_asset(base_symbol):
    """Choose appropriate exchange based on the asset"""
    if base_symbol in ["BTC", "ETH", "SOL"]:
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

def get_trend_analysis(base_symbol, quote_symbol="USD", chain=None):
    cache_key = f"{base_symbol}_{quote_symbol}"
    
    def fetch_analysis():
        try:
            exchange = get_exchange_for_asset(base_symbol)
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
            
            # Special handling for specific tokens
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
            
            if quote_symbol == "BTC" and base_symbol in ["NATIX", "TIG", "FAI"]:
                usdt_ohlcv = exchange.fetch_ohlcv(symbol_pair, '1d', limit=50)
                if not usdt_ohlcv:
                    return {
                        "symbol": symbol_pair,
                        "error": "No price data available",
                        "chain": chain,
                        "exchange": exchange.id
                    }
                
                btc_prices = get_btc_price()
                
                closes = []
                for i in range(min(len(usdt_ohlcv), len(btc_prices))):
                    token_price = usdt_ohlcv[i][4]
                    btc_usd_price = btc_prices[i]
                    closes.append(token_price / btc_usd_price)
            else:
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
            
            return {
                "symbol": symbol_pair,
                "current_price": current_price,
                "ema8": ema8,
                "ema20": ema20,
                "is_uptrend": ema8 > ema20,
                "trend_text": "Uptrend" if ema8 > ema20 else "Downtrend",
                "quote_currency": actual_quote,
                "chain": chain,
                "exchange": exchange.id,
                "is_calculated": quote_symbol == "BTC" and base_symbol in ["NATIX", "TIG", "FAI"],
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        except Exception as e:
            return {
                "symbol": f"{base_symbol}/{quote_symbol}",
                "error": f"Error on {exchange.id}: {str(e)}",
                "chain": chain,
                "exchange": exchange.id
            }
    
    return get_cached_data(cache_key, fetch_analysis)

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    assets = [
        {"symbol": "BTC", "chain": None},
        {"symbol": "ETH", "chain": None},
        {"symbol": "SOL", "chain": None},
        {"symbol": "BANANA", "chain": "ethereum"},
        {"symbol": "NATIX", "chain": "solana"},
        {"symbol": "TIG", "chain": "base"},
        {"symbol": "FAI", "chain": "base"}
    ]
    
    analysis = []
    for asset in assets:
        if asset["symbol"] == "BTC":
            usd_analysis = get_trend_analysis(asset["symbol"], "USD", asset["chain"])
            analysis.append({
                "asset": asset["symbol"],
                "chain": asset["chain"],
                "usdt": usd_analysis,  # Keep the key as 'usdt' for template compatibility
                "btc": {"symbol": "BTC/BTC", "error": "Same asset"}
            })
        else:
            usd_analysis = get_trend_analysis(asset["symbol"], "USD", asset["chain"])
            btc_analysis = get_trend_analysis(asset["symbol"], "BTC", asset["chain"])
            analysis.append({
                "asset": asset["symbol"],
                "chain": asset["chain"],
                "usdt": usd_analysis,  # Keep the key as 'usdt' for template compatibility
                "btc": btc_analysis
            })
    
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "analysis": analysis}
    ) 