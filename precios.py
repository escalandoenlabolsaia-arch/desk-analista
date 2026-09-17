# -*- coding: utf-8 -*-
"""
precios.py — Indicadores tecnicos livianos en lote para el hijo.

Una sola descarga batch de yfinance para todo el universo del hijo
(watchlist publico + nucleo en RAM). Calcula por ticker:
  precio (ultimo cierre), dist_ema200, dist_ema50, rsi (14, Wilder),
  vol_ratio (volumen del ultimo dia vs promedio 20).
Un ticker sin datos -> clave 'error'. Nunca lanza: degrada.
"""

import pandas as pd
import yfinance as yf

PERIODO = "2y"  # suficiente para EMA200 asentada + ventana de volumen


def _rsi_wilder(cierres, periodo=14):
    delta = cierres.diff()
    ganancia = delta.clip(lower=0)
    perdida = -delta.clip(upper=0)
    ag = ganancia.ewm(alpha=1 / periodo, adjust=False).mean()
    ap = perdida.ewm(alpha=1 / periodo, adjust=False).mean()
    rs = ag / ap
    return 100 - 100 / (1 + rs)


def _f(x):
    try:
        if x is None or pd.isna(x):
            return None
        return round(float(x), 4)
    except (TypeError, ValueError):
        return None


def _de_un_ticker(df):
    if df is None or "Close" not in getattr(df, "columns", []):
        return {"error": "sin datos"}
    df = df.dropna(subset=["Close"])
    if len(df) < 30:
        return {"error": "sin datos"}
    close = df["Close"]
    precio = float(close.iloc[-1])
    ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    rsi = float(_rsi_wilder(close).iloc[-1])
    vol_ratio = None
    if "Volume" in df.columns and float(df["Volume"].tail(20).mean()) > 0:
        vol_ratio = float(df["Volume"].iloc[-1]) / float(df["Volume"].tail(20).mean())
    return {"precio": _f(precio),
            "dist_ema200": _f(precio / ema200 - 1) if ema200 else None,
            "dist_ema50": _f(precio / ema50 - 1) if ema50 else None,
            "rsi": _f(rsi),
            "vol_ratio": _f(vol_ratio)}


def descargar_indicadores(tickers):
    """Devuelve {ticker: indicadores} para los que respondan."""
    lista = sorted({t.strip().upper() for t in tickers if t and t.strip()})
    out = {}
    if not lista:
        return out
    try:
        data = yf.download(lista, period=PERIODO, interval="1d",
                           group_by="ticker", auto_adjust=True,
                           progress=False, threads=True)
    except Exception as e:
        print(f"  AVISO: descarga batch fallo ({type(e).__name__}); sin precios hoy")
        return {t: {"error": "descarga"} for t in lista}
    if data is None or data.empty:
        return {t: {"error": "sin datos"} for t in lista}
    multi = isinstance(data.columns, pd.MultiIndex)
    for t in lista:
        try:
            df = data[t] if multi else data
            out[t] = _de_un_ticker(df)
        except Exception:
            out[t] = {"error": "proceso"}
    return out
