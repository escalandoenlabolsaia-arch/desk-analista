# -*- coding: utf-8 -*-
"""
main.py — Orquestador del hijo (desk-analista), etapa 2b: leer, archivar,
seguir y OPINAR.

Cada corrida:
1. Descarga salidas/estado.json de la MADRE (URL publica, contrato v1).
   Si el estado tiene mas de madre.max_atraso_dias, AVISA y sigue.
2. Lee el espejo de cartera (cartera.py) y archiva la foto en 'historial'
   si trae fecha nueva (append-only).
3. Nucleo en RAM: acciones de la cartera. JAMAS se persisten ni publican.
4. Watchlist publico: altas por senal de la madre (setup / vigilancia >=2
   fondos / insider con neto comprador). Salidas NUNCA automaticas.
5. Una sola descarga batch de precios (watchlist + nucleo) -> indicadores.
6. Ficha pesada por empresa del watchlist (analista.py): se baja UNA vez y
   queda en cache 90 dias; refresco automatico despues. Veredicto
   solido/mixto/fragil + tesis de 1 linea (Groq o plantilla local).
7. historial/TICKER_<anio>.json: fila diaria COMPLETA (precios + fundamentos
   + tesis). Fila del mismo dia = se reescribe; anteriores intocables.
8. Avisos por ntfy PRIVADO (topic propio del hijo): cruce nucleo x senales,
   acciones de cartera sin datos, y la ficha completa de cada empresa nueva.
   El analisis de cartera vive solo en RAM: nunca al repo.
9. Publica salidas/estado.json propio (contrato v1, solo datos publicos).

Reglas de la familia:
- Flujo unidireccional madre -> hijo. El hijo nunca escribe en la madre.
- Nada derivado de la cartera viaja a archivos ni logs publicos (RAM only).
- Cada capa con try/except propio: la corrida degrada, no muere.
- Sin NTFY_TOPIC_HIJO: la corrida sigue, pero avisa que no hay notificaciones.
"""

import json
import os
import time
from datetime import date

import requests

from cartera import leer_cartera, archivar_foto
from precios import descargar_indicadores
from analista import obtener_ficha

WATCHLIST = "watchlist.json"
FICHAS_DIR = "fichas"


def cargar_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------- madre
def descargar_estado_madre(cfg):
    """Descarga el estado publico de la madre. Devuelve (estado, aviso)."""
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
    """Senales del dia desde el estado de la madre (todo publico)."""
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
    """Altas automaticas por senal de hoy. Nunca da de baja."""
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


# --------------------------------------------------------------- ntfy (topic privado)
def enviar_ntfy(topic, texto, titulo="Desk Analista"):
    MAX = 3800
    partes, resto = [], texto
    while len(resto) > MAX:
        corte = resto.rfind("\n", 0, MAX)
        if corte == -1:
            corte = MAX
        partes.append(resto[:corte])
        resto = resto[corte:].lstrip("\n")
    if resto:
        partes.append(resto)
    for i, parte in enumerate(partes, start=1):
        ok = False
        for intento in range(3):
            try:
                r = requests.post(
                    f"https://ntfy.sh/{topic}",
                    data=parte.encode("utf-8"),
                    headers={"Title": titulo, "Priority": "high",
                             "Tags": "chart", "Markdown": "yes"},
                    timeout=30,
                )
                r.raise_for_status()
                ok = True
                break
            except Exception as e:
                print(f"  ntfy intento {intento + 1} fallo: {e}")
                time.sleep(5)
        if not ok:
            raise RuntimeError(f"No se pudo enviar la parte {i} a ntfy")
        time.sleep(2)


def _v(x, suf=""):
    """Formatea una metrica para mensaje: valor+suffix o 'n/d'."""
    return "n/d" if x is None else f"{x}{suf}"


def avisar_fichas_nuevas(fichas_nuevas):
    """Ficha completa de cada empresa nueva/al dia de refresco -> ntfy privado."""
    if not fichas_nuevas:
        return
    topic = os.environ.get("NTFY_TOPIC_HIJO", "").strip()
    if not topic:
        print("  AVISO: sin NTFY_TOPIC_HIJO: fichas nuevas NO enviadas")
        return
    bloques = []
    for t, f in fichas_nuevas.items():
        fund = f.get("fundamentos") or {}
        fcf = fund.get("fcf_usd")
        fcf_txt = "n/d" if fcf is None else f"USD {fcf / 1e9:.1f}B"
        vered = f.get("veredicto") or "sin_datos"
        debiles = f.get("debiles") or []
        l = [f"**FICHA — {t}** ({f.get('sector') or 'sector n/d'})",
             f"Veredicto: **{vered}**"
             + (f" — débiles: {'; '.join(debiles)}" if debiles else ""),
             f"PE {_v(fund.get('pe'))} · PB {_v(fund.get('pb'))} · "
             f"ROE {_v(fund.get('roe'), '%')} · margen op {_v(fund.get('margen_op'), '%')} · "
             f"deuda/EBITDA {_v(fund.get('deuda_ebitda'))} · FCF {fcf_txt}",
             f"Próximo earnings: {f.get('proximo_earnings') or 'n/d'}",
             f"🤖 Tesis: {f.get('tesis_1_linea') or 'n/d'}"]
        bloques.append("\n".join(l))
    try:
        enviar_ntfy(topic, "\n\n---\n\n".join(bloques),
                    titulo="Desk Analista - Fichas")
        print(f"ntfy: {len(bloques)} ficha(s) enviada(s)")
    except Exception as e:
        print(f"  AVISO: ntfy fallo ({type(e).__name__}); la corrida sigue")


def avisos_cartera(cruces, senales, inds, sin_datos):
    """Cruce nucleo x senales + avisos de cartera. Todo por ntfy PRIVADO."""
    topic = os.environ.get("NTFY_TOPIC_HIJO", "").strip()
    if not topic:
        if cruces or sin_datos:
            print("  AVISO: sin NTFY_TOPIC_HIJO: avisos de cartera NO enviados")
        return
    bloques = []
    if cruces:
        l = ["**SEÑAL DE LA MADRE SOBRE TU CARTERA**"]
        for t in cruces:
            s = senales[t]
            i = inds.get(t) or {}
            precio = i.get("precio") or s.get("precio")
            rsi = i.get("rsi") if i.get("rsi") is not None else s.get("rsi")
            l.append(f"- **{t}** ({', '.join(s['origen'])}) - precio {precio} - "
                     f"RSI {rsi}")
        bloques.append("\n".join(l))
    if sin_datos:
        bloques.append("**Cartera: acciones sin datos de mercado** (¿ticker "
                       "correcto?): " + ", ".join(sin_datos))
    if not bloques:
        return
    try:
        enviar_ntfy(topic, "\n\n---\n\n".join(bloques))
        print("ntfy: avisos de cartera enviados")
    except Exception as e:
        print(f"  AVISO: ntfy fallo ({type(e).__name__}); la corrida sigue")


# --------------------------------------------------------------- fichas
def _ficha_vacia(f):
    """Ficha sin ninguna metrica: no sirve ni para cachear."""
    fund = f.get("fundamentos") or {}
    return (f.get("veredicto") == "sin_datos"
            and all(v is None for v in fund.values()))


def obtener_fichas_watchlist(w, cfg, groq_key):
    """Ficha pesada por empresa del watchlist (cache de 90 dias dentro de
    analista.py). Devuelve (fichas, nuevas): nuevas = las creadas hoy."""
    umbrales = cfg.get("fundamentos") or {}
    fichas, nuevas = {}, {}
    for t in sorted(w.get("empresas", {})):
        try:
            ficha, es_nueva = obtener_ficha(t, umbrales=umbrales,
                                            groq_key=groq_key)
        except Exception as e:
            print(f"  AVISO: ficha de {t} fallo ({type(e).__name__}); sigo sin ella")
            continue
        if es_nueva and _ficha_vacia(ficha):
            try:
                ruta = os.path.join(FICHAS_DIR, f"{t.upper()}.json")
                if os.path.exists(ruta):
                    os.remove(ruta)   # no cachear 90 dias de nada
            except OSError:
                pass
            print(f"  AVISO: ficha de {t} sin datos; se reintenta mañana")
            continue
        fichas[t] = ficha
        if es_nueva:
            nuevas[t] = ficha
            print(f"ficha: {t} NUEVA (veredicto {ficha.get('veredicto')})")
        else:
            print(f"ficha: {t} (cache, veredicto {ficha.get('veredicto')})")
    return fichas, nuevas


# --------------------------------------------------------------- historial repo
def _fila_diaria(t, senal, ind, fecha_op, ficha=None):
    s = senal or {}
    i = ind if (ind and not ind.get("error")) else {}
    f = ficha or {}
    fund = f.get("fundamentos") or {}
    fundamentos = None
    if fund:
        fundamentos = {"pe": fund.get("pe"), "pb": fund.get("pb"),
                       "roe": fund.get("roe"),
                       "deuda_ebitda": fund.get("deuda_ebitda"),
                       "margen_op": fund.get("margen_op"),
                       "score": f.get("veredicto")}
    return {
        "fecha": fecha_op.isoformat(),
        "precio": i.get("precio", s.get("precio")),
        "dist_ema200": i.get("dist_ema200"),
        "dist_ema50": i.get("dist_ema50"),
        "rsi": i.get("rsi", s.get("rsi")),
        "vol_ratio": i.get("vol_ratio"),
        "senales": s.get("origen", []),
        "n_fondos": s.get("n_fondos", 0),
        "insiders_neto_usd": s.get("insiders_neto_usd"),
        "fundamentos": fundamentos,
        "proximo_earnings": f.get("proximo_earnings"),
        "nuevos_filings": [],      # etapa 2c (filings.py)
        "tesis_1_linea": f.get("tesis_1_linea"),
    }


def actualizar_historial_repo(w, senales, inds, fecha_op, fichas):
    """Fila diaria COMPLETA para cada empresa del watchlist.
    Mismo dia ya presente = se reescribe (idempotente); anteriores intocables."""
    anio = fecha_op.year
    os.makedirs("historial", exist_ok=True)
    escritos = 0
    for t in sorted(w.get("empresas", {})):
        ruta = os.path.join("historial", f"{t}_{anio}.json")
        doc = {"ticker": t, "anio": anio, "dias": []}
        if os.path.exists(ruta):
            try:
                with open(ruta, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                doc.setdefault("dias", [])
            except (json.JSONDecodeError, OSError):
                print(f"  AVISO: {ruta} ilegible; lo recreo")
        fila = _fila_diaria(t, senales.get(t), inds.get(t), fecha_op,
                            ficha=fichas.get(t))
        doc["dias"] = [d for d in doc["dias"] if d.get("fecha") != fila["fecha"]]
        doc["dias"].append(fila)
        doc["ticker"] = t
        doc["anio"] = anio
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        escritos += 1
    return escritos


# --------------------------------------------------------------- estado propio
def publicar_estado_hijo(w, senales, inds, fichas, fecha_op, madre_fecha):
    """Contrato v1 del hijo: SOLO derivados de datos publicos (senales de la
    madre + precios + fundamentales del watchlist). JAMAS datos de cartera."""
    empresas_out = []
    for t, e in sorted(w.get("empresas", {}).items()):
        d = senales.get(t, {})
        i = inds.get(t) or {}
        f = fichas.get(t) or {}
        try:
            desde = date.fromisoformat(e["desde"])
            dias = (fecha_op - desde).days
        except ValueError:
            dias = None
        empresas_out.append({
            "ticker": t,
            "sector": (f.get("sector") or d.get("sector")),
            "desde": e["desde"],
            "dias_seguimiento": dias,
            "ultima_senal": e.get("ultima_senal"),
            "origen": e.get("origen", []),
            "precio": i.get("precio") or d.get("precio"),
            "rsi": i.get("rsi") if i.get("rsi") is not None else d.get("rsi"),
            "n_fondos": d.get("n_fondos", 0),
            "senales_hoy": d.get("origen", []),
            "veredicto": f.get("veredicto"),
            "proximo_earnings": f.get("proximo_earnings"),
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

    # 1. Estado de la madre
    estado, aviso_madre = descargar_estado_madre(cfg)
    if estado is None:
        raise RuntimeError(f"sin estado de la madre: {aviso_madre}")
    fecha_op = date.fromisoformat(str(estado["fecha"])[:10])
    print(f"estado madre ok (fecha {fecha_op.isoformat()}, hoy {fecha_hoy.isoformat()})")

    # 2. Cartera (RAM) + archivo de la foto en la hoja
    foto = leer_cartera(cfg)
    print(foto.get("resumen_log", "cartera: sin resumen"))
    if not foto.get("ok"):
        print("  AVISO: sin cartera hoy; la corrida sigue con nucleo vacio")
    print("historial hoja: " + archivar_foto(cfg, foto))

    # 3. Nucleo en RAM (solo acciones seguibles)
    if foto.get("ok"):
        nucleo = {l["ticker"] for l in foto["lineas"] if l.get("tipo") == "accion"}
    else:
        nucleo = set()

    # 4. Watchlist con las senales publicas de hoy
    senales = senales_publicas_hoy(estado)
    w = cargar_watchlist()
    nuevas = actualizar_watchlist(w, senales, fecha_op)
    guardar_watchlist(w)

    cruces = sorted(nucleo & set(senales))
    print(f"cartera: {len(nucleo)} acciones seguibles | en senales de hoy: {len(cruces)}")
    print(f"watchlist: {len(w['empresas'])} empresas | altas hoy: {len(nuevas)}")
    if nuevas:
        print("  altas: " + ", ".join(nuevas))

    # 5. Precios batch: watchlist + nucleo, una sola descarga
    a_seguir = sorted(set(w.get("empresas", {})) | nucleo)
    inds = descargar_indicadores(a_seguir)
    con_datos = sum(1 for v in inds.values() if not v.get("error"))
    print(f"precios batch: {con_datos}/{len(a_seguir)} tickers con datos")

    # 6. Fichas pesadas del watchlist (cache 90 dias; pesado solo la 1a vez)
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    fichas, fichas_nuevas = obtener_fichas_watchlist(w, cfg, groq_key)
    print(f"fichas: {len(fichas)} ok ({len(fichas_nuevas)} nueva(s) hoy)")

    # 7. Historial anual del watchlist (fila diaria completa)
    escritos = actualizar_historial_repo(w, senales, inds, fecha_op, fichas)
    print(f"historial repo: {escritos} archivo(s) actualizado(s) "
          f"(anio {fecha_op.year})")

    # 8. Avisos privados: cartera (cruces/sin datos) + fichas nuevas
    sin_datos = sorted(t for t in nucleo if (inds.get(t) or {}).get("error"))
    if sin_datos:
        print(f"  AVISO: {len(sin_datos)} accion(es) de cartera sin datos de "
              f"mercado (detalle por ntfy)")
    avisos_cartera(cruces, senales, inds, sin_datos)
    avisar_fichas_nuevas(fichas_nuevas)

    # 9. Estado propio del hijo
    publicar_estado_hijo(w, senales, inds, fichas, fecha_op, estado["fecha"])
    print("estado.json del hijo publicado")


# --------------------------------------------------------------- arranque
if __name__ == "__main__":
    main()
