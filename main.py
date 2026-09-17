# -*- coding: utf-8 -*-
"""
main.py — Orquestador del hijo (desk-analista), etapa 1: leer y archivar.

Cada corrida:
1. Descarga salidas/estado.json de la MADRE (URL publica, contrato v1).
   Si el estado tiene mas de madre.max_atraso_dias, AVISA y sigue
   (es el ultimo valido disponible).
2. Lee el espejo de cartera (cartera.py). Datos de cartera = SOLO RAM.
3. Archiva la foto en la pestana 'historial' de la hoja si trae fecha nueva.
4. Actualiza watchlist.json con las senales PUBLICAS de la madre:
   setups, vigilancia con >=2 fondos, insiders del bloque con neto > 0.
   Entradas automaticas; salidas NUNCA automaticas (etapa posterior).
5. Escribe historial/TICKER_<anio>.json: fila liviana por dia de senal
   (los datos vienen del estado de la madre; el diario completo con precios
   y la ficha pesada llegan con analista.py en etapa 2).
6. Publica salidas/estado.json propio (contrato v1 del hijo, sin cartera).

Reglas de la familia:
- Flujo unidireccional madre -> hijo. El hijo nunca escribe en la madre.
- Nada derivado de la cartera viaja a archivos ni logs publicos (RAM only).
- Cada capa con try/except propio: la corrida degrada, no muere.
- Sin ntfy en etapa 1: los avisos van al log (repo publico, sin datos
  sensibles por diseno).
"""

import json
import os
import time
from datetime import date

import requests

from cartera import leer_cartera, archivar_foto

WATCHLIST = "watchlist.json"


def cargar_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------- madre
def descargar_estado_madre(cfg):
    """Descarga el estado publico de la madre. Devuelve (estado, aviso).
    estado=None significa fallo definitivo (aviso explica la causa)."""
    mcfg = cfg.get("madre", {})
    url = (mcfg.get("estado_url") or "").strip()
    max_atraso = int(mcfg.get("max_atraso_dias", 2))
    if not url or "USUARIO" in url:
        return None, ("madre.estado_url sin configurar: reemplazá USUARIO por "
                      "tu usuario de GitHub en config.json")
    resultado = None
    for intento in range(1, 4):
        try:
            r = requests.get(url, timeout=30,
                             headers={"User-Agent": "desk-analista/1.0"})
            if r.status_code == 200:
                resultado = r.json()
                break
            resultado = f"HTTP {r.status_code}"
        except Exception as e:
            resultado = type(e).__name__
        if intento < 3:
            time.sleep(5 * intento)
    if not isinstance(resultado, dict):
        pista = (" (¿existe salidas/estado.json en el repo madre? "
                 "¿la URL y el usuario son correctos?)"
                 if "404" in str(resultado) else "")
        return None, f"fallo descarga ({resultado}){pista}"
    if resultado.get("version") != 1:
        return None, f"contrato inesperado: version {resultado.get('version')}"
    try:
        fecha = date.fromisoformat(str(resultado.get("fecha"))[:10])
    except ValueError:
        return None, "estado de la madre sin fecha valida"
    atraso = (date.today() - fecha).days
    if atraso > max_atraso:
        print(f"  AVISO: estado de la madre con {atraso} dias de atraso "
              f"(fecha {fecha.isoformat()}); sigo con el ultimo valido")
    return resultado, "ok"


# --------------------------------------------------------------- senales publicas
def senales_publicas_hoy(estado):
    """Junta las senales del dia desde el estado de la madre (todo publico).
    Reglas de entrada del watchlist: setup, vigilancia con >=2 fondos,
    insider del bloque con neto comprador. Devuelve {ticker: datos}."""
    out = {}

    def _add(ticker, origen, sector=None, precio=None, rsi=None,
             n_fondos=None, insiders_neto=None):
        t = (ticker or "").strip().upper()
        if not t:
            return
        d = out.setdefault(t, {"sector": None, "precio": None, "rsi": None,
                               "n_fondos": 0, "insiders_neto_usd": None,
                               "origen": []})
        if origen not in d["origen"]:
            d["origen"].append(origen)
        if sector and not d["sector"]:
            d["sector"] = sector
        if precio is not None and d["precio"] is None:
            d["precio"] = precio
        if rsi is not None and d["rsi"] is None:
            d["rsi"] = rsi
        if n_fondos:
            d["n_fondos"] = max(d["n_fondos"], n_fondos)
        if insiders_neto is not None and d["insiders_neto_usd"] is None:
            d["insiders_neto_usd"] = insiders_neto

    for f in estado.get("setups", []):
        _add(f.get("ticker"), "setup", f.get("sector"), f.get("precio"),
             f.get("rsi"), f.get("n_fondos"), f.get("insiders_neto_usd"))
    for f in estado.get("vigilancia", []):
        if (f.get("n_fondos") or 0) >= 2:
            _add(f.get("ticker"), "vigilancia_fondos", f.get("sector"),
                 f.get("precio"), f.get("rsi"), f.get("n_fondos"))
    for h in estado.get("insiders_universo", []):
        if (h.get("neto_usd") or 0) > 0:
            _add(h.get("ticker"), "insider", None, None, None, None,
                 h.get("neto_usd"))
    return out


# --------------------------------------------------------------- watchlist
def cargar_watchlist():
    """Lee watchlist.json (o crea la estructura inicial si falta/corrupto)."""
    if os.path.exists(WATCHLIST):
        try:
            with open(WATCHLIST, "r", encoding="utf-8") as f:
                w = json.load(f)
            if isinstance(w.get("empresas"), dict):
                w.setdefault("version", 1)
                return w
            print("  AVISO: watchlist.json sin formato esperado; lo recreo")
        except (json.JSONDecodeError, OSError):
            print("  AVISO: watchlist.json ilegible; lo recreo")
    return {"version": 1, "actualizado": None, "empresas": {}}


def guardar_watchlist(w):
    with open(WATCHLIST, "w", encoding="utf-8") as f:
        json.dump(w, f, ensure_ascii=False, indent=2)


def actualizar_watchlist(w, senales, fecha_op):
    """Altas automaticas por senal de hoy. Nunca da de baja: las empresas
    entran y permanecen (permanencia minima 12 meses, salidas en etapa
    posterior SIEMPRE con aviso previo). Devuelve lista de altas de hoy."""
    empresas = w.setdefault("empresas", {})
    nuevas = []
    for t in senales:
        e = empresas.get(t)
        if e is None:
            empresas[t] = {"desde": fecha_op.isoformat(),
                           "ultima_senal": fecha_op.isoformat(),
                           "origen": sorted(set(senales[t]["origen"]))}
            nuevas.append(t)
        else:
            e["ultima_senal"] = fecha_op.isoformat()
    w["actualizado"] = fecha_op.isoformat()
    return sorted(nuevas)


# --------------------------------------------------------------- historial repo
def actualizar_historial_repo(w, senales, fecha_op):
    """Fila liviana por empresa con senal hoy, en historial/TICKER_<anio>.json.
    Mismo dia ya presente = se reescribe (idempotente); dias anteriores
    nunca se tocan. Sin senales hoy = sin escrituras."""
    anio = fecha_op.year
    os.makedirs("historial", exist_ok=True)
    escritos = 0
    for t, datos in senales.items():
        if t not in w["empresas"]:
            continue
        ruta = os.path.join("historial", f"{t}_{anio}.json")
        doc = {"ticker": t, "anio": anio, "dias": []}
        if os.path.exists(ruta):
            try:
                with open(ruta, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                doc.setdefault("dias", [])
            except (json.JSONDecodeError, OSError):
                print(f"  AVISO: {ruta} ilegible; lo recreo (se pierde el "
                      f"diario previo de {t})")
        fila = {"fecha": fecha_op.isoformat(),
                "precio": datos.get("precio"),
                "rsi": datos.get("rsi"),
                "n_fondos": datos.get("n_fondos", 0),
                "insiders_neto_usd": datos.get("insiders_neto_usd"),
                "origen_hoy": datos.get("origen", []),
                "fundamentos": None}
        doc["dias"] = [d for d in doc["dias"] if d.get("fecha") != fila["fecha"]]
        doc["dias"].append(fila)
        doc["ticker"] = t
        doc["anio"] = anio
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        escritos += 1
    return escritos


# --------------------------------------------------------------- estado propio
def publicar_estado_hijo(w, senales, fecha_op, madre_fecha):
    """Contrato v1 del hijo: SOLO derivados de datos publicos de la madre.
    Jamas incluye nada derivado de la cartera (regla RAM only)."""
    empresas_out = []
    for t, e in sorted(w.get("empresas", {}).items()):
        d = senales.get(t, {})
        try:
            desde = date.fromisoformat(e["desde"])
            dias = (fecha_op - desde).days
        except ValueError:
            dias = None
        empresas_out.append({
            "ticker": t,
            "sector": d.get("sector"),
            "desde": e["desde"],
            "dias_seguimiento": dias,
            "ultima_senal": e.get("ultima_senal"),
            "origen": e.get("origen", []),
            "precio": d.get("precio"),
            "rsi": d.get("rsi"),
            "n_fondos": d.get("n_fondos", 0),
            "senales_hoy": d.get("origen", []),
        })
    estado = {
        "version": 1,
        "fecha": fecha_op.isoformat(),
        "madre_fecha": madre_fecha,
        "corrida_ok": True,
        "resumen": {"empresas": len(empresas_out)},
        "watchlist": empresas_out,
    }
    os.makedirs("salidas", exist_ok=True)
    with open(os.path.join("salidas", "estado.json"), "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------- orquestacion
def main():
    cfg = cargar_config()
    fecha_hoy = date.today()

    # 1. Estado de la madre (dia operativo = fecha del estado)
    estado, aviso_madre = descargar_estado_madre(cfg)
    if estado is None:
        raise RuntimeError(f"sin estado de la madre: {aviso_madre}")
    fecha_op = date.fromisoformat(str(estado["fecha"])[:10])
    print(f"estado madre ok (fecha {fecha_op.isoformat()}, hoy {fecha_hoy.isoformat()})")

    # 2. Cartera (RAM) + 3. archivo de la foto en la hoja
    foto = leer_cartera(cfg)
    print(foto.get("resumen_log", "cartera: sin resumen"))
    if not foto.get("ok"):
        print("  AVISO: sin cartera hoy; la corrida sigue con nucleo vacio")
    print("historial hoja: " + archivar_foto(cfg, foto))

    # 4. Nucleo en RAM: acciones seguibles de la hoja (solo conteos al log)
    if foto.get("ok"):
        nucleo = {l["ticker"] for l in foto["lineas"] if l.get("tipo") == "accion"}
    else:
        nucleo = set()

    # 5. Watchlist con las senales publicas de hoy
    senales = senales_publicas_hoy(estado)
    w = cargar_watchlist()
    nuevas = actualizar_watchlist(w, senales, fecha_op)
    guardar_watchlist(w)

    en_senales = nucleo & set(senales)
    print(f"cartera: {len(nucleo)} acciones seguibles | "
          f"en senales de hoy: {len(en_senales)}")
    print(f"watchlist: {len(w['empresas'])} empresas | altas hoy: {len(nuevas)}")
    if nuevas:
        print("  altas: " + ", ".join(nuevas))  # publico por diseno: entran por senales publicas

    # 6. Historial anual en el repo (filas de dias con senal)
    escritos = actualizar_historial_repo(w, senales, fecha_op)
    print(f"historial repo: {escritos} archivo(s) actualizado(s) "
          f"(anio {fecha_op.year})")

    # 7. Estado propio del hijo
    publicar_estado_hijo(w, senales, fecha_op, estado["fecha"])
    print("estado.json del hijo publicado")


# --------------------------------------------------------------- arranque
if __name__ == "__main__":
    main()
