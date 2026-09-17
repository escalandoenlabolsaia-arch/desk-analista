# -*- coding: utf-8 -*-
"""
cartera.py — Lector del espejo de cartera (Google Sheets) para el hijo.

Reglas de la familia que este archivo respeta:
- La cartera vive SOLO en RAM durante la corrida. Nada se guarda en el repo.
- Los logs nunca muestran tickers, pesos ni rendimientos (repo publico):
  solo conteos y numeros agregados.
- Lectura por NOMBRE de columna (headers), no por posicion.
- 3 reintentos. Si falla definitivo: ok=False (ese dia no se proponen salidas).

archivar_foto(): escribe en la pestana 'historial' de la MISMA hoja, en modo
append-only (agrega filas, nunca edita ni borra). Unica escritura permitida
en toda la familia; requiere service account con rol Editor.
"""

import json
import os
import re
import sys
import time
from datetime import date

import gspread

SKIP_TICKERS = {"TOTAL", "SUMA", ""}


# ------------------------- utilidades de parsing -------------------------

def _norm(texto):
    """Minusculas, sin espacios de borde, sin tildes."""
    t = (texto or "").strip().lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        t = t.replace(a, b)
    return t


def _a_float(s):
    """'6.59' o '6,59' -> 6.59. None si no se puede."""
    try:
        return float(str(s).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parsear_porcentaje(valor):
    """
    Acepta '6.59%', '6,59%', '-29%', numero 6.59, o fraccion 0.0659
    (celda con formato porcentaje). Devuelve float en unidades humanas
    (6.59 = 6.59%) o None si vacio/ilegible.
    """
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        v = float(valor)
        if 0 < v <= 1.5:
            return round(v * 100, 4)
        return v
    s = str(valor).strip()
    if s == "":
        return None
    n = _a_float(s.replace("%", ""))
    if n is None:
        return None
    return n  # texto con o sin % : ya esta en unidades de porcentaje


def _normalizar_fecha(txt):
    """Acepta AAAA-MM-DD, AAAA/M/D, D/M/AAAA (o M/D/AAAA si hay mes > 12)."""
    t = (txt or "").strip()
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", t)
    if m:
        d, mes, anio = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if d <= 12 and mes > 12:      # formato M/D/AAAA (locale EEUU)
            d, mes = mes, d
        return f"{anio}-{mes:02d}-{d:02d}"
    return t if t else None


def _buscar_header(headers_norm, prefijos):
    for i, h in enumerate(headers_norm):
        for p in prefijos:
            if h.startswith(p):
                return i
    return None


# ------------------------------ lectura ------------------------------

def leer_cartera(config):
    """Lee la pestaana del espejo. Devuelve dict con ok / lineas / avisos /
    suma / resumen_log. Las 'lineas' y los 'avisos' con detalle viven SOLO
    en RAM: quien llama decide que es seguro mostrar y donde."""
    cfg = (config or {}).get("cartera", {})
    pestana = cfg.get("pestana", "cartera")
    tol = float(cfg.get("suma_tol", 3))
    tipos_validos = set(cfg.get("tipos_validos", []))

    sheet_id = os.environ.get("SHEET_ID_HIJO", "").strip()
    gsa_json = os.environ.get("GSA_JSON_HIJO", "").strip()
    faltan = [n for n, v in (("SHEET_ID_HIJO", sheet_id),
                             ("GSA_JSON_HIJO", gsa_json)) if not v]
    if faltan:
        return {"ok": False, "resumen_log":
                f"lectura NO ok: faltan secrets ({', '.join(faltan)})"}

    try:
        cred = json.loads(gsa_json)
    except json.JSONDecodeError:
        return {"ok": False, "resumen_log":
                "lectura NO ok: GSA_JSON_HIJO no es un JSON valido (¿se copio cortado?)"}

    # 3 reintentos: 5s, 10s, 15s entre intentos
    valores, ultimo_error = None, None
    for intento in range(1, 4):
        try:
            gc = gspread.service_account_from_dict(cred)
            sh = gc.open_by_key(sheet_id)
            ws = sh.worksheet(pestana)
            valores = ws.get_all_values()
            break
        except Exception as e:
            ultimo_error = type(e).__name__  # solo la clase, nunca el detalle
            if intento < 3:
                time.sleep(5 * intento)
    if valores is None:
        pista = {"HttpError": "(¿falta compartir la hoja con la service account?)",
                 "APIError": "(¿service account sin permiso Lector?)",
                 "WorksheetNotFound": f"(¿existe la pestana '{pestana}'?)",
                 "SpreadsheetNotFound": "(¿SHEET_ID_HIJO es el ID correcto?)"}.get(
                     ultimo_error, "")
        return {"ok": False, "resumen_log":
                f"lectura NO ok: hoja inaccesible tras 3 reintentos "
                f"({ultimo_error}) {pista}".strip()}

    if len(valores) < 2:
        return {"ok": False, "resumen_log": "lectura NO ok: hoja sin filas de datos"}

    # headers por nombre
    fila1 = valores[0]
    headers_norm = [_norm(h) for h in fila1]
    i_ticker = _buscar_header(headers_norm, ("ticker",))
    i_peso = _buscar_header(headers_norm, ("peso",))
    i_tipo = _buscar_header(headers_norm, ("tipo",))
    i_rend = _buscar_header(headers_norm, ("rend",))
    faltan_h = [n for n, i in (("ticker", i_ticker), ("peso_%", i_peso),
                               ("tipo", i_tipo), ("rend_%", i_rend)) if i is None]
    if faltan_h:
        return {"ok": False, "resumen_log":
                f"lectura NO ok: headers no encontrados: {', '.join(faltan_h)}"}

    # fecha de la foto: celda E1 (indice 4 de la fila 1)
    fecha = _normalizar_fecha(fila1[4]) if len(fila1) > 4 else None

    lineas, avisos, vistos = [], [], {}
    for num_fila, fila in enumerate(valores[1:], start=2):
        celda = lambda i: fila[i] if i < len(fila) else ""
        ticker = _norm(celda(i_ticker)).upper()
        if ticker in SKIP_TICKERS:
            continue
        peso = _parsear_porcentaje(celda(i_peso))
        if ticker == "" and peso is None:
            continue  # fila vacia o de relleno
        tipo = _norm(celda(i_tipo))
        rend = _parsear_porcentaje(celda(i_rend))

        if ticker == "":
            avisos.append({"tipo": "fila_sin_ticker", "fila": num_fila})
            continue
        if peso is None:
            avisos.append({"tipo": "peso_ilegible", "fila": num_fila, "ticker": ticker})
        elif peso < 0 or peso > 100:
            avisos.append({"tipo": "peso_fuera_rango", "fila": num_fila,
                           "ticker": ticker, "valor": peso})
        if tipo not in tipos_validos:
            avisos.append({"tipo": "tipo_desconocido", "fila": num_fila,
                           "ticker": ticker, "tipo_valor": tipo})
        if rend is not None and rend < -100:
            avisos.append({"tipo": "rend_muy_negativo", "fila": num_fila,
                           "ticker": ticker, "valor": rend})
        if rend is not None and rend > 300:
            avisos.append({"tipo": "rend_extremo", "fila": num_fila,
                           "ticker": ticker, "valor": rend})
        if ticker in vistos:
            avisos.append({"tipo": "ticker_duplicado", "fila": num_fila,
                           "ticker": ticker, "otra_fila": vistos[ticker]})
        else:
            vistos[ticker] = num_fila

        lineas.append({"ticker": ticker, "peso": peso,
                       "tipo": tipo or None, "rend": rend, "fila": num_fila})

    if not lineas:
        return {"ok": False, "resumen_log": "lectura NO ok: 0 lineas utiles"}

    pesos = [l["peso"] for l in lineas if l["peso"] is not None]
    suma = round(sum(pesos), 2)
    suma_ok = abs(suma - 100) <= tol
    if not suma_ok:
        avisos.append({"tipo": "suma_fuera_rango", "suma": suma})

    n = len(lineas)
    tipos_ok = sum(1 for l in lineas if l["tipo"] in tipos_validos)
    rend_vacios = sum(1 for l in lineas if l["rend"] is None)
    resumen = (f"lectura ok: {n} lineas | suma {suma}% "
               f"({'ok' if suma_ok else f'FUERA de ±{tol}'}) | "
               f"tipos {tipos_ok}/{n} | rend vacios {rend_vacios} | "
               f"avisos {len(avisos)} | fecha {fecha or 'FALTA'}")

    return {"ok": True, "fecha": fecha, "lineas": lineas, "avisos": avisos,
            "suma": suma, "suma_ok": suma_ok, "resumen_log": resumen}


# ------------------------------ archivo en hoja ------------------------------

def archivar_foto(config, foto):
    """Append-only: si la fecha de la foto es NUEVA (mayor que la ultima
    archivada), agrega sus filas a la pestana 'historial'. Si la fecha ya
    existe o es mas vieja, no toca nada. Nunca edita ni borra filas.
    Devuelve un resumen para el log (sin tickers ni pesos)."""
    cfg = (config or {}).get("cartera", {})
    pestana_h = cfg.get("pestana_historial", "historial")
    if not (foto or {}).get("ok") or not foto.get("fecha"):
        return "archivo de foto omitido: foto no ok o sin fecha"
    fecha = foto["fecha"]
    try:
        fecha_d = date.fromisoformat(fecha)
    except ValueError:
        return f"archivo de foto omitido: fecha ilegible ({fecha})"

    sheet_id = os.environ.get("SHEET_ID_HIJO", "").strip()
    gsa_json = os.environ.get("GSA_JSON_HIJO", "").strip()
    if not sheet_id or not gsa_json:
        return "archivo de foto omitido: faltan secrets"
    try:
        cred = json.loads(gsa_json)
    except json.JSONDecodeError:
        return "archivo de foto omitido: JSON de llave invalido"

    ultimo_error = None
    for intento in range(1, 4):
        try:
            gc = gspread.service_account_from_dict(cred)
            sh = gc.open_by_key(sheet_id)
            ws = sh.worksheet(pestana_h)
            existentes = ws.get_all_values()
            ultima = None
            for fila in existentes[1:]:
                try:
                    f = date.fromisoformat(str(fila[0])[:10])
                    if ultima is None or f > ultima:
                        ultima = f
                except ValueError:
                    continue
            if ultima is not None and fecha_d <= ultima:
                return (f"historial hoja al dia (ultima foto "
                        f"{ultima.isoformat()}), nada que archivar")
            filas = [[fecha, l["ticker"],
                      "" if l["peso"] is None else l["peso"],
                      l["tipo"] or "",
                      "" if l["rend"] is None else l["rend"]]
                     for l in foto["lineas"]]
            if filas:
                ws.append_rows(filas, value_input_option="RAW")
            previas = max(len(existentes) - 1, 0)
            return (f"foto {fecha} archivada: {len(filas)} filas nuevas "
                    f"(historial total: {previas + len(filas)} filas)")
        except Exception as e:
            ultimo_error = type(e).__name__
            if intento < 3:
                time.sleep(5 * intento)
    pista = {"HttpError": "(¿la service account tiene rol Editor en la hoja?)",
             "APIError": "(¿permiso de escritura?)",
             "WorksheetNotFound": f"(¿existe la pestana '{pestana_h}'?)",
             "SpreadsheetNotFound": "(¿SHEET_ID_HIJO es el ID correcto?)"}.get(
                 ultimo_error, "")
    return (f"archivo de foto FALLO tras 3 reintentos ({ultimo_error}) "
            f"{pista}").strip()


# ------------------------- prueba manual -------------------------

if __name__ == "__main__":
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
    r = leer_cartera(config)
    print(r.get("resumen_log", "sin resumen"))
    if not r.get("ok"):
        sys.exit(1)
    # En log publico: solo categoria y numero de fila. JAMAS ticker ni valores.
    # El numero de fila coincide con la fila de TU hoja (fila 2 = primera linea
    # de datos), asi vos podes verificar en privado si apareciera algun aviso.
    for a in r.get("avisos", []):
        print(f"  aviso: {a['tipo']} (fila {a.get('fila', '-')})")
    print("historial hoja: " + archivar_foto(config, r))
    sys.exit(0)
