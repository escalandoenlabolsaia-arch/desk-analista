Las fichas llegaron **exactamente como fueron diseñadas** — métricas completas (ni un `n/d`), veredictos coherentes y las tesis de Groq con el tono pedido: tesis + riesgo concreto, sin relleno. Una lectura rápida con ojo de analista:

**AMZN**: ROE 30.56% y deuda/EBITDA 1.49 justifican el `solido`. Dos observaciones que el propio sistema va a resolver: el FCF de 3.2B **luce bajo** para Amazon (probablemente el TTM tragándose el capex brutal de data centers — es el tipo de dato con ruido que se refresca solo a los 90 días), y PE 19.8 es de los más baratos que Amazon mostró en años.

**UNH**: márgenes finos (7.13%, apenas sobre el mínimo 5%) pero sin ninguna métrica violada → `solido` limpio. Y fijate la elegancia: el leeme4 soñaba con la tesis *"aseguradora con ROE alto comprada en pánico del sector"*... y Groq la escribió sola, con el riesgo correcto (regulatorio). El ejemplo del papel se volvió real.

**Dato vivo**: earnings de UNH el **13/10** — a menos de un mes. El campo ya quedó en el cuaderno; un aviso automático "earnings en 7 días" es mejora natural futura (lo anotamos).

**Nota técnica** (quema una duda futura): tu celular dice 22:57 del 16/9 y el log dice "hoy 2026-09-17" — no es error: el runner de GitHub va en UTC, y a esa hora de Argentina ya era 17/9 para él. El cuaderno usa la fecha **operativa** (la del estado de la madre), que es la correcta.

Y como la memoria fresca vale oro, acá va el **README del hijo** — su propio "leeme", con toda la historia de estos dos días. En el repo `desk-analista` → abrí `README.md` → lápiz ✏️ → reemplazá TODO:

````markdown
# Desk Analista 📈 — el HIJO

Agente analista de largo plazo. Toma las señales de la MADRE (repo
`desk-inversion`), las sigue con precios diarios, les saca ficha fundamental
con scoring, redacta una tesis de 1 línea, y archiva todo en cuadernos
anuales por empresa. El usuario es inversor de largo plazo: este diseño se
piensa en años. Corre gratis en GitHub Actions. Silencio = todo bien.

**La familia:** madre (encuentra) → **hijo (entiende)** → nieto futuro
(`desk-cartera`, contextualiza con la cartera). Flujo UNIDIRECCIONAL: el hijo
nunca escribe en la madre. Un agente a la vez.

---

## Flujo completo (cada corrida)

```
1. MADRE      Descarga salidas/estado.json de la madre por URL pública
              (contrato v1). Sin estado válido → ABORTA limpio.
2. CARTERA    Lee la hoja espejo (pestaña 'cartera') y ARCHIVA la foto en la
              pestaña 'historial' si trae fecha nueva (append-only).
3. NUCLEO     Acciones de la cartera, derivadas en RAM. JAMAS se guardan.
4. WATCHLIST  Altas automáticas por señal pública (setup / vigilancia >=2
              fondos / insider comprador). Bajas NUNCA automáticas.
5. PRECIOS    Una descarga batch (watchlist + nucleo): precio, EMA200/50,
              RSI, volumen.
6. FICHAS     Ficha pesada por empresa del watchlist: UNA vez, cache 90 días.
              Scoring solido/mixto/fragil + tesis de 1 línea (Groq).
7. CUADERNOS  historial/TICKER_<anio>.json: fila diaria completa. La fila de
              hoy se reescribe (idempotente); días anteriores intocables.
8. AVISOS     ntfy PRIVADO (topic propio): cruce cartera x señales, acciones
              sin datos, ficha completa de cada empresa nueva.
9. ESTADO     Publica salidas/estado.json propio (contrato v1, sin cartera).
```

## Archivos

| Archivo | Qué hace |
|---|---|
| `config.json` | Panel: URL del estado de la madre, pestañas de la hoja, umbrales de fundamentos, modelo IA. |
| `cartera.py` | Lee la hoja (headers por nombre, 3 reintentos) + archiva la foto en 'historial'. Tiene botón de test propio (workflow `test-cartera`). |
| `precios.py` | Indicadores técnicos en lote (yfinance batch). Sin datos → degrada, no muere. |
| `analista.py` | Ficha pesada (yfinance .info) + scoring + tesis Groq/plantilla local. Cache 90 días en `fichas/`. |
| `main.py` | Orquestador (el flujo de arriba). |
| `watchlist.json` | Empresas en seguimiento. `origen: seed` = sembrada a mano por el usuario. |
| `historial/` | Cuadernos anuales `TICKER_<anio>.json` (público: solo señales públicas). |
| `fichas/` | Fichas fundamentales cacheadas (público: datos de empresas públicas). |
| `salidas/estado.json` | Contrato v1 del hijo → lo consumirá el nieto. |
| `.github/workflows/desk-analista.yml` | Cron `30 0 * * 0-6` (21:30 AR) + botón manual + commit de resultados. |
| `.github/workflows/test-cartera.yml` | Test rápido de solo-lectura de la hoja (botón manual). |

## Reglas de la familia (NO negociables)

- **RAM-only**: nada derivado de la cartera (tickers, pesos, rendimientos) se
  guarda ni muestra en el repo/logs públicos. Vive en RAM y viaja SOLO por el
  topic ntfy privado. Lo que se commitea es público por diseño.
- **La cartera existe solo en la hoja cáscara** (cuenta Google sin identidad).
  Pestañas: `cartera` (foto actual, la pisa el usuario), `politica` (reglas
  del usuario), `historial` (cuaderno append-only, lo escribe el robot).
- **La fecha E1 de la pestaña cartera es el disparador del archivo**: foto
  nueva = fecha nueva. Sin cambio de fecha, no se archiva nada.
- **Archivos COMPLETOS, nunca a medias** (lección de la madre: main cortado =
  corrida verde de 1 segundo que no hace nada).
- **Lo probado en la madre se copia al hijo** (lección de yfinance, abajo).
- Cada capa con try/except propio: la corrida degrada, no muere.

## Secretos (Settings → Secrets and variables → Actions)

| Secret | Qué es |
|---|---|
| `SHEET_ID_HIJO` | ID de la hoja cáscara (entre `/d/` y `/edit` de la URL). |
| `GSA_JSON_HIJO` | Service account completa (JSON), rol **Editor** en la hoja (escribe solo la pestaña historial). |
| `NTFY_TOPIC_HIJO` | Topic privado del hijo (DISTINTO al de la madre; el nombre ES la llave). |
| `GROQ_API_KEY` | Misma key de Groq que la madre (misma cuenta, pegada en cada repo). |

## Log sano (cómo debe verse una corrida)

```
estado madre ok (fecha ..., hoy ...)
lectura ok: 32 lineas | suma 99.99% (ok) | tipos 32/32 | ... | fecha ...
historial hoja: foto ... archivada: N filas nuevas   <- o "al dia, nada que archivar"
cartera: 20 acciones seguibles | en senales de hoy: 0
watchlist: N empresas | altas hoy: M
precios batch: 22/22 tickers con datos
ficha: TICKER NUEVA (veredicto ...)   <- solo si hay alta o refresco 90d
fichas: N ok (M nueva(s) hoy)
historial repo: N archivo(s) actualizado(s) (anio ...)
estado.json del hijo publicado
```

AVISOS normales (no críticos): `estado de la madre con N dias de atraso`
(fin de semana/feriado), `N accion(es) de cartera sin datos` (ticker raro o
Yahoo corto; se reintenta mañana), `ntfy fallo` (la corrida sigue).

## Problemas ya resueltos (no volver a pisarlos)

- **HTTP 404 descargando a la madre**: el repo madre era PRIVADO →
  raw.githubusercontent.com responde 404 a anónimos (aunque tu navegador lo
  vea, el runner no está logueado). Fix: repo madre → Public (es seguro: no
  tiene secrets ni cartera). Corolario: si la URL falla, probarla en
  ventana de incógnito.
- **URL del estado mal copiada**: copiar SIEMPRE del botón Raw del archivo
  en GitHub, nunca tipearla.
- **`JSONDecodeError: Expecting value...` masivo en yfinance**: versión vieja
  clavada (`==0.2.51`). Fix: `yfinance>=0.2.54` (alineada con la madre).
  REGLA: versiones de descarga = las de la madre.
- **`No module named 'pandas'`**: faltaba en requirements.txt del hijo.
  Agregar librería nueva = editar requirements.txt.
- **El "cerebro viejo"**: código nuevo sin commitear → el workflow corre la
  versión anterior. Si una línea nueva del log no aparece, verificar que el
  archivo esté realmente commiteado.
- **Corrida manual de noche**: el runner va en UTC; a las 22:57 AR ya es
  "mañana" para él. El cuaderno usa la fecha OPERATIVA (estado de la madre).

## Scoring (veredictos)

- **solido**: 0 umbrales violados · **mixto**: 1-2 · **fragil**: 3+ ·
  **sin_datos**: menos de 2 métricas disponibles (informa, no inventa).
- Umbrales (editables en config.json → `fundamentos`): PE≤40, PB≤10,
  ROE≥8%, deuda/EBITDA≤3.5, margen op≥5%. Herencia de la sección huérfana
  del config de la madre.
- Ficha cacheada 90 días (`fichas/TICKER.json`); si sale vacía no se cachea
  (se reintenta mañana).

## Pendientes / roadmap

- **2c — `filings.py`**: completar `nuevos_filings` (10-K/10-Q/8-K vía EDGAR,
  copia del patrón de sec_edgar.py de la madre) + aviso de filing nuevo.
- **Aviso de earnings próximo** ("UNH earnings en 7 días") — el campo ya
  vive en las fichas y cuadernos.
- **Salidas del watchlist** (hoy: nunca automáticas): 12 meses + sin señales
  60 días, SIEMPRE con aviso previo y decisión del usuario.
- **El nieto** (`desk-cartera`): lee el estado de este hijo + la hoja (su
  propia service account Lector) → drift, rebalanceos, concentración, 3
  perfiles (conservador/moderado/agresivo). Nada hasta que el hijo lleve
  semanas estable.
- **Semana de rodaje**: revisar duración de corridas y ruido de avisos.

## Cómo usar este README con Claude (chat nuevo)

1. Chat nuevo: "Te pego el README del repo desk-analista (el hijo)" + este archivo.
2. Pegar el log de Actions de la corrida problemática.
3. Claude tiene: arquitectura, reglas RAM-only, errores históricos, roadmap.

## Recordatorio importante

Fichas, veredictos y tesis son datos que informan, no recomendaciones. La
tesis de la IA es una opinión de modelo de lenguaje, no investigación de
mercado. Decisión, tamaño y riesgo = del operador. Sin dinero real conectado.
````

Commit: `README: memoria del hijo (leeme1)`.

Con ese commit, **el hijo queda completo y documentado** — mismo estándar que la madre. Lo que sigue ya sin código: dejar que corra solo unos días (rodaje), y cuando quieras retomamos con `filings.py` (2c) o directamente a diseñar el nieto. Pegame confirmación del commit y cerramos la sesión con el resumen de estado. 🚀
