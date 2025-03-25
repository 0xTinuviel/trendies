from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

app = FastAPI()
templates = Jinja2Templates(directory="templates")

def calculate_ema(data, periods):
    return pd.Series(data).ewm(span=periods, adjust=False).mean().iloc[-1]

def get_btc_price():
    """Get current BTC/USDT price to use for conversions"""
    exchange = ccxt.binance()
    btc_ohlcv = exchange.fetch_ohlcv('BTC/USDT', '1d', limit=50)
    return [x[4] for x in btc_ohlcv]

def get_exchange_for_asset(base_symbol):
    """Choose appropriate exchange based on the asset"""
    if base_symbol == "TIG":
        return ccxt.xt()
    elif base_symbol == "NATIX":
        # Try KuCoin first, with fallbacks to Gate.io and MEXC
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
        # Try BitMart first, fallback to BingX
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
        return ccxt.binance()

def get_trend_analysis(base_symbol, quote_symbol="USDT", chain=None):
    try:
        exchange = get_exchange_for_asset(base_symbol)
    except Exception as e:
        return {
            "symbol": f"{base_symbol}/{quote_symbol}",
            "error": str(e),
            "chain": chain
        }
    
    try:
        symbol_pair = f"{base_symbol}/{quote_symbol}"
        
        # Special handling for specific tokens
        if base_symbol == "TIG":
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
        
        # For BTC relative value, we need to calculate it
        if quote_symbol == "BTC" and base_symbol in ["NATIX", "TIG", "FAI"]:
            # Get USDT prices for the token
            usdt_ohlcv = exchange.fetch_ohlcv(symbol_pair, '1d', limit=50)
            if not usdt_ohlcv:
                return {
                    "symbol": symbol_pair,
                    "error": "No USDT data available",
                    "chain": chain,
                    "exchange": exchange.id
                }
            
            # Get BTC prices for the same period
            btc_prices = get_btc_price()
            
            # Calculate relative values
            closes = []
            for i in range(min(len(usdt_ohlcv), len(btc_prices))):
                token_usdt_price = usdt_ohlcv[i][4]
                btc_usdt_price = btc_prices[i]
                closes.append(token_usdt_price / btc_usdt_price)
        else:
            # Normal OHLCV fetch for other cases
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
            "quote_currency": quote_symbol,
            "chain": chain,
            "exchange": exchange.id,
            "is_calculated": quote_symbol == "BTC" and base_symbol in ["NATIX", "TIG", "FAI"]
        }
    except Exception as e:
        return {
            "symbol": f"{base_symbol}/{quote_symbol}",
            "error": f"Error on {exchange.id}: {str(e)}",
            "chain": chain,
            "exchange": exchange.id
        }

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    # Define assets with their chains
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
            usdt_analysis = get_trend_analysis(asset["symbol"], "USDT", asset["chain"])
            analysis.append({
                "asset": asset["symbol"],
                "chain": asset["chain"],
                "usdt": usdt_analysis,
                "btc": {"symbol": "BTC/BTC", "error": "Same asset"}
            })
        else:
            usdt_analysis = get_trend_analysis(asset["symbol"], "USDT", asset["chain"])
            btc_analysis = get_trend_analysis(asset["symbol"], "BTC", asset["chain"])
            analysis.append({
                "asset": asset["symbol"],
                "chain": asset["chain"],
                "usdt": usdt_analysis,
                "btc": btc_analysis
            })
    
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "analysis": analysis}
    ) 