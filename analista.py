# -*- coding: utf-8 -*-
"""
analista.py — Ficha fundamental pesada + scoring del hijo (etapa 2b).

Diseno del leeme4:
- La ficha pesada se baja UNA vez por empresa (financials via yfinance .info)
  y se guarda en fichas/TICKER.json. Los dias siguientes leen del cache:
  costo de red cero. Refresco automatico si la ficha tiene mas de 90 dias
  (frecuencia de resultados trimestrales).
- Scoring con los umbrales de la seccion 'fundamentos' (herencia del config
  de la madre; defaults aca, editables desde config.json del hijo).
  Veredicto: solido / mixto / fragil (o sin_datos si yfinance no entrego
  metricas suficientes) + puntos debiles señalados.
- Tesis de 1 linea via Groq (misma key/patron que la madre) con plantilla
  local de respaldo.

Privacidad: todo lo que maneja este archivo es data PUBLICA de empresas
publicas. La regla RAM-only rige para la cartera, no para esto.
Filosofia: cada capa con try/except propio; una ficha con datos incompletos
es un veredicto 'sin_datos', nunca un crash.
"""

import json
import os
import sys
import time
from datetime import date, datetime

import requests
import yfinance as yf

FICHAS_DIR = "fichas"
FICHA_MAX_DIAS = 90          # refresco trimestral (sincroniza con earnings)

# Heredados del leeme4 (seccion fundamentos del config de la madre).
# Se pueden sobrescribir desde config.json del hijo con la misma estructura.
DEFAULTS_UMBRALES = {
    "pe_max": 40,            # PE por encima = caro (PE negativo = pierde plata)
    "pb_max": 10,            # PB por encima = caro (PB negativo = patrim. negativo)
    "roe_min": 8,            # ROE (%)
    "deuda_ebitda_max": 3.5, # apalancamiento
    "margen_op_min": 5,      # margen operativo (%)
}


# ------------------------------ descarga pesada ------------------------------

def _num(info, clave):
    """Valor float de info, None si falta o es NaN. yfinance devuelve NaN
    en vez de None en muchos campos."""
    try:
        v = info.get(clave)
        f = float(v)
        return None if f != f else f   # NaN != NaN
    except (TypeError, ValueError, AttributeError):
        return None


def obtener_fundamentos(ticker):
    """Baja la ficha pesada de yfinance. Devuelve dict con metricas o None.
    Nunca lanza: ante error devuelve el dict vacio y la corrida sigue."""
    out = {"pe": None, "pb": None, "roe": None, "deuda_ebitda": None,
           "margen_op": None, "margen_neto": None, "fcf_usd": None,
           "proximo_earnings": None, "sector": None}
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
    except Exception as e:
        print(f"  {ticker}: yfinance sin ficha ({type(e).__name__})")
        return out

    out["sector"] = info.get("sector") or None

    pe = _num(info, "trailingPE")
    if pe is not None:
        out["pe"] = round(pe, 2)
    pb = _num(info, "priceToBook")
    if pb is not None:
        out["pb"] = round(pb, 2)

    roe = _num(info, "returnOnEquity")           # fraccion (0.185 = 18.5%)
    if roe is not None:
        out["roe"] = round(roe * 100, 2)
    mo = _num(info, "operatingMargins")          # fraccion
    if mo is not None:
        out["margen_op"] = round(mo * 100, 2)
    mn = _num(info, "netProfitMargins")          # fraccion
    if mn is not None:
        out["margen_neto"] = round(mn * 100, 2)

    fcf = _num(info, "freeCashflow")
    if fcf is not None:
        out["fcf_usd"] = round(fcf)

    deuda = _num(info, "totalDebt")
    ebitda = _num(info, "ebitda")
    if deuda is not None and ebitda is not None and ebitda > 0:
        out["deuda_ebitda"] = round(deuda / ebitda, 2)

    ts = _num(info, "earningsTimestamp")         # fecha del proximo earnings
    if ts is not None:
        try:
            out["proximo_earnings"] = datetime.fromtimestamp(ts).date().isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    return out


# --------------------------------- scoring ---------------------------------

def scorar(fund, umbrales=None):
    """Aplica los umbrales a las metricas. Devuelve (veredicto, debiles,
    desconocidos). 0 debilidades = solido; 1-2 = mixto; 3+ = fragil.
    Si casi no hay metricas, veredicto 'sin_datos' (informa, no inventa)."""
    u = dict(DEFAULTS_UMBRALES)
    u.update(umbrales or {})
    debiles, desconocidos = [], []

    pe = fund.get("pe")
    if pe is None:
        desconocidos.append("PE")
    elif pe < 0 or pe > u["pe_max"]:
        debiles.append(f"PE {pe:.1f} (max {u['pe_max']})")

    pb = fund.get("pb")
    if pb is None:
        desconocidos.append("PB")
    elif pb < 0 or pb > u["pb_max"]:
        debiles.append(f"PB {pb:.1f} (max {u['pb_max']})")

    roe = fund.get("roe")
    if roe is None:
        desconocidos.append("ROE")
    elif roe < u["roe_min"]:
        debiles.append(f"ROE {roe:.1f}% (min {u['roe_min']}%)")

    de = fund.get("deuda_ebitda")
    if de is None:
        desconocidos.append("deuda/EBITDA")
    elif de > u["deuda_ebitda_max"]:
        debiles.append(f"deuda/EBITDA {de:.1f} (max {u['deuda_ebitda_max']})")

    mo = fund.get("margen_op")
    if mo is None:
        desconocidos.append("margen op")
    elif mo < u["margen_op_min"]:
        debiles.append(f"margen op {mo:.1f}% (min {u['margen_op_min']}%)")

    if len(debiles) == 0:
        veredicto = "solido"
    elif len(debiles) <= 2:
        veredicto = "mixto"
    else:
        veredicto = "fragil"
    if len(desconocidos) >= 4:
        veredicto = "sin_datos"   # informo que no hay base para opinar
    return veredicto, debiles, desconocidos


# ------------------------------ tesis (Groq) ------------------------------

def tesis_groq(ticker, fund, key):
    """Una linea escéptica de largo plazo. None si no hay key o falla."""
    if not key:
        return None
    datos = (
        f"{ticker} (sector: {fund.get('sector') or 'n/d'}) - "
        f"PE {fund.get('pe')}, PB {fund.get('pb')}, "
        f"ROE {fund.get('roe')}%, margen operativo {fund.get('margen_op')}%, "
        f"margen neto {fund.get('margen_neto')}%, "
        f"deuda/EBITDA {fund.get('deuda_ebitda')}, "
        f"FCF anual USD {(fund.get('fcf_usd') or 0) / 1e9:.1f}B."
    )
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": (cfg_modelo() or "openai/gpt-oss-120b"),
                "messages": [
                    {"role": "system",
                     "content": "Sos un analista fundamentalista esceptico de "
                                "largo plazo. Escribi en espanol UNA sola linea: "
                                "la tesis de inversion (por que el negocio vale) "
                                "y su riesgo principal. Sin saludos ni relleno."},
                    {"role": "user", "content": datos},
                ],
                "reasoning_effort": "low",
                "max_tokens": 300,
                "temperature": 0.3,
            },
            timeout=30,
        )
        r.raise_for_status()
        contenido = (r.json()["choices"][0]["message"]["content"] or "").strip()
        lineas = [l.strip() for l in contenido.splitlines() if l.strip()]
        return lineas[0] if lineas else None
    except Exception as e:
        print(f"  Groq fallo ({type(e).__name__}) - uso plantilla local")
        return None


def cfg_modelo():
    """Modelo Groq desde config.json del hijo (misma filosofia que la madre).
    Sin seccion 'ia', usa el default declarado en tesis_groq."""
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return ((json.load(f).get("ia") or {}).get("modelo"))
    except Exception:
        return None


def tesis_local(ticker, fund, veredicto, debiles):
    """Respaldo sin IA: una linea con reglas."""
    base = {"solido": "Fundamentales solidos",
            "mixto": "Fundamentales mixtos",
            "fragil": "Fundamentales fragiles"}.get(veredicto,
                                                    "Sin datos fundamentales")
    detalle = ""
    if fund.get("roe") is not None and fund.get("margen_op") is not None:
        detalle = f" (ROE {fund['roe']:.0f}%, margen op {fund['margen_op']:.0f}%)"
    if debiles:
        riesgo = debiles[0]
    else:
        riesgo = "verificar tesis en los proximos resultados"
    return f"{base}{detalle}. Riesgo principal: {riesgo}."


# ------------------------------ ficha (cache) ------------------------------

def obtener_ficha(ticker, umbrales=None, groq_key=None, forzar=False):
    """Devuelve (ficha, es_nueva). Ficha nueva = se bajo y scorio hoy.
    Si existe una ficha con menos de FICHA_MAX_DIAS, se devuelve del cache
    (es_nueva=False, costo cero)."""
    os.makedirs(FICHAS_DIR, exist_ok=True)
    ruta = os.path.join(FICHAS_DIR, f"{ticker.upper()}.json")
    hoy = date.today()
    if not forzar and os.path.exists(ruta):
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                ficha = json.load(f)
            fecha = date.fromisoformat(ficha.get("fecha_ficha", "2000-01-01"))
            if (hoy - fecha).days <= FICHA_MAX_DIAS:
                return ficha, False
        except (json.JSONDecodeError, OSError, ValueError):
            print(f"  AVISO: ficha de {ticker} ilegible; la reconstruyo")

    fund = obtener_fundamentos(ticker)
    veredicto, debiles, desconocidos = scorar(fund, umbrales)
    tesis = (tesis_groq(ticker, fund, groq_key)
             or tesis_local(ticker, fund, veredicto, debiles))
    ficha = {
        "version": 1,
        "ticker": ticker.upper(),
        "sector": fund.get("sector"),
        "fecha_ficha": hoy.isoformat(),
        "fundamentos": {k: fund.get(k) for k in
                        ("pe", "pb", "roe", "deuda_ebitda", "margen_op",
                         "margen_neto", "fcf_usd")},
        "proximo_earnings": fund.get("proximo_earnings"),
        "veredicto": veredicto,
        "debiles": debiles,
        "metricas_sin_datos": desconocidos,
        "tesis_1_linea": tesis,
    }
    try:
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(ficha, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"  AVISO: no pude guardar ficha de {ticker}: {e}")
    return ficha, True


# ------------------------------ prueba manual ------------------------------

if __name__ == "__main__":
    t = sys.argv[1].upper() if len(sys.argv) > 1 else "UNH"
    key = os.environ.get("GROQ_API_KEY")
    ficha, nueva = obtener_ficha(t, groq_key=key, forzar=True)
    print(json.dumps(ficha, ensure_ascii=False, indent=2))
    print(f"({'NUEVA' if nueva else 'cache'})")
