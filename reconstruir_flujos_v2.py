# -*- coding: utf-8 -*-
"""
FASE B1: cache de flujos v2, NORMALIZADO A MILES DE CLP.

Problema detectado: COLBUN, ENELAM, SOQUICOM reportan a la CMF en USD, y
PEHUENCHE / ENELGXCH CAMBIARON a USD en 2025 -> el cache v1 mezclaba
monedas (el grafico La Polar de PEHUENCHE muestra 2025 colapsado ~1000x).

Este script reconstruye flujos_historicos.json:
  - Re-descarga los cierres 12/2021..12/2025 de la watchlist.
  - Lee la MONEDA que declara la CMF en cada periodo (puede cambiar por ano).
  - Convierte USD -> CLP con el dolar observado al cierre de cada ano
    (yfinance USDCLP=X; tabla de respaldo si falla).
  - Guarda todo en MILES DE CLP homogeneos + metadatos (moneda_origen, tc).
  - NUEVO: guarda tambien el patrimonio de cada cierre (normalizado), para
    validar el numero de acciones y habilitar ROE historico futuro.
  - Salvaguarda: detecta saltos >50x entre anos consecutivos tras normalizar
    (si quedara alguno, se reporta para revision).

Respaldo del cache v1 en flujos_historicos.json.v1.bak. Guardado incremental.
Validaciones al final: OXIQUIM (CLP, sin conversion), COLBUN (USD siempre),
PEHUENCHE (cambio a USD en 2025 - continuidad esperada).

Uso:  python reconstruir_flujos_v2.py
"""
import io, json, re, sys, time
from pathlib import Path

MCP = r"C:\mcp-bolsa"
if MCP not in sys.path:
    sys.path.insert(0, MCP)
from cmf import obtener_estados_financieros, extraer_cuentas_clave  # noqa

REPO = Path(r"C:\value-signal-local\repo")
CACHE_PATH = REPO / "flujos_historicos.json"
WATCHLIST_JSON = REPO / "acciones_chilenas.json"
ANOS = [2021, 2022, 2023, 2024, 2025]
PAUSA_SEG = 1.0

# Tabla de respaldo: dolar observado aprox. al cierre de cada ano
TC_FALLBACK = {2021: 850.8, 2022: 861.2, 2023: 879.4, 2024: 996.0, 2025: 945.0}

def obtener_tc_por_ano():
    """Dolar observado al ultimo cierre habil de diciembre, via yfinance."""
    tc = {}
    try:
        import yfinance as yf
        h = yf.Ticker("USDCLP=X").history(period="6y")
        if h is not None and not h.empty:
            for ano in ANOS:
                sub = h[(h.index.year == ano) & (h.index.month == 12)]
                if not sub.empty:
                    tc[ano] = float(sub["Close"].iloc[-1])
    except Exception as e:
        print(f"AVISO: yfinance USDCLP fallo ({e}); uso tabla de respaldo.")
    for ano in ANOS:
        if ano not in tc or not tc[ano] or tc[ano] <= 0:
            tc[ano] = TC_FALLBACK[ano]
            print(f"  TC {ano}: {tc[ano]} (tabla de respaldo)")
        else:
            print(f"  TC {ano}: {tc[ano]:.2f} (yfinance, cierre dic)")
    return tc

def _num(v):
    if v is None: return None
    if isinstance(v, (int, float)): return float(v)
    if isinstance(v, dict):
        try:
            ks = sorted(v.keys())
            for k in reversed(ks):
                n = _num(v[k])
                if n is not None: return n
        except Exception: pass
        for x in v.values():
            n = _num(x)
            if n is not None: return n
        return None
    if isinstance(v, (list, tuple)):
        for x in v:
            n = _num(x)
            if n is not None: return n
        return None
    try:
        return float(str(v).replace(".", "").replace(",", "."))
    except Exception:
        return None

def _buscar(d, *patrones):
    if not isinstance(d, dict): return None
    for pat in patrones:
        rx = re.compile(pat, re.I)
        for k in d.keys():
            if rx.search(str(k)):
                n = _num(d[k])
                if n is not None: return n
    return None

def es_usd(moneda_str):
    return bool(re.search(r"d[o\u00f3]lar|usd|us\$", str(moneda_str or ""), re.I))

def main():
    wl = json.load(io.open(WATCHLIST_JSON, encoding="utf-8"))
    tickers = [a["ticker"] for a in wl.get("acciones", [])]
    print(f"Watchlist: {len(tickers)} tickers")
    print("Tipos de cambio (cierre de diciembre):")
    TC = obtener_tc_por_ano()
    print()

    # respaldo del v1 una sola vez
    if CACHE_PATH.exists():
        bak = CACHE_PATH.with_suffix(".json.v1.bak")
        if not bak.exists():
            bak.write_bytes(CACHE_PATH.read_bytes())
            print(f"Respaldo del cache v1 en {bak.name}")

    cache = {}
    if CACHE_PATH.exists():
        prev = json.load(io.open(CACHE_PATH, encoding="utf-8"))
        # conservar solo registros que ya sean v2 (tienen moneda_origen)
        for t, regs in prev.items():
            v2 = [r for r in regs if "moneda_origen" in r]
            if v2:
                cache[t] = v2

    for t in tickers:
        registros = {r["ano"]: r for r in cache.get(t, [])}
        faltan = [a for a in ANOS if a not in registros]
        if not faltan:
            print(f"{t}: v2 completo, omito.")
            continue
        print(f"{t}: descargando cierres {faltan}...")
        for aa in faltan:
            try:
                ef = obtener_estados_financieros(t, mm=12, aa=aa)
            except Exception as e:
                print(f"  {aa}: error descarga ({e})"); time.sleep(PAUSA_SEG); continue
            if not ef:
                print(f"  {aa}: sin datos en CMF"); time.sleep(PAUSA_SEG); continue
            cuentas = extraer_cuentas_clave(ef)
            flujo = cuentas.get("flujo") or {}
            eerr = cuentas.get("eerr") or {}
            bal = cuentas.get("balance") or {}

            util = _buscar(eerr, r"atribuible.*controlador", r"^ganancia_perdida$", r"ganancia")
            op = _buscar(flujo, r"operaci")
            inv = _buscar(flujo, r"inversi")
            fin = _buscar(flujo, r"financ")
            patr = _buscar(bal, r"patrimonio")

            moneda = getattr(ef, "moneda", "") or ""
            usd = es_usd(moneda)
            tc = TC[aa] if usd else 1.0
            conv = lambda x: (x * tc) if (x is not None) else None

            reg = {
                "ano": aa,
                "utilidad": conv(util), "flujo_op": conv(op),
                "flujo_inv": conv(inv), "flujo_fin": conv(fin),
                "fcf": (conv(op) + conv(inv)) if (op is not None and inv is not None) else None,
                "patrimonio": conv(patr),
                "moneda_origen": "USD" if usd else "CLP",
                "tc": round(tc, 2) if usd else None,
                "periodo": getattr(ef, "periodo", f"12/{aa}"),
                "unidad": "Miles CLP (normalizado)",
            }
            registros[aa] = reg
            ok = "OK" if (util is not None and op is not None) else "PARCIAL"
            print(f"  {aa}: {ok} [{reg['moneda_origen']}{' tc '+str(reg['tc']) if usd else ''}]  util={reg['utilidad'] and round(reg['utilidad']):,}  fcf={reg['fcf'] and round(reg['fcf']):,}")
            time.sleep(PAUSA_SEG)

        cache[t] = sorted(registros.values(), key=lambda r: r["ano"])
        io.open(CACHE_PATH, "w", encoding="utf-8").write(
            json.dumps(cache, ensure_ascii=False, indent=1))

    # ---- resumen + salvaguarda de saltos ----
    print()
    print("=" * 64)
    print("RESUMEN v2 (todo en miles de CLP):")
    for t in tickers:
        regs = cache.get(t, [])
        monedas = sorted(set(r["moneda_origen"] for r in regs))
        utils = [r["utilidad"] for r in regs if r.get("utilidad")]
        salto = ""
        for i in range(1, len(utils)):
            a, b = abs(utils[i-1]), abs(utils[i])
            if a > 0 and b > 0 and (a/b > 50 or b/a > 50):
                salto = "  \u26a0\ufe0f SALTO >50x - revisar"
        print(f"  {t:<12} anos:{len(regs)}  moneda(s) origen: {'/'.join(monedas) or '?'}{salto}")

    # ---- validaciones de oro ----
    print()
    def reg_de(t, aa):
        return next((r for r in cache.get(t, []) if r["ano"] == aa), None)
    oxi = reg_de("OXIQUIM", 2025)
    if oxi and oxi.get("flujo_op"):
        ok = 40e6 < oxi["flujo_op"] < 50e6
        print(f"VALIDACION OXIQUIM 2025 (CLP directo): op={oxi['flujo_op']:,.0f} -> {'OK' if ok else 'REVISAR'}")
    peh24, peh25 = reg_de("PEHUENCHE", 2024), reg_de("PEHUENCHE", 2025)
    if peh24 and peh25 and peh24.get("utilidad") and peh25.get("utilidad"):
        ratio = peh25["utilidad"] / peh24["utilidad"]
        print(f"VALIDACION PEHUENCHE continuidad 2024->2025: ratio {ratio:.2f} -> {'OK (sin colapso 1000x)' if 0.3 < ratio < 3 else 'REVISAR'}")
    col = reg_de("COLBUN", 2025)
    if col and col.get("utilidad"):
        print(f"VALIDACION COLBUN 2025 (USD->CLP): utilidad={col['utilidad']:,.0f} miles CLP (~{col['utilidad']/1e6:,.0f} mil MM)")
    print("\nCache v2 guardado en:", CACHE_PATH)
    print("Siguiente paso: regenerar el JSON (python acciones_chilenas.py) para")
    print("que el grafico La Polar use los datos normalizados.")

if __name__ == "__main__":
    main()
