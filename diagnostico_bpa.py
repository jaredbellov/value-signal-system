# -*- coding: utf-8 -*-
"""
Diagnostico para el feature BPA 5 anos:
1. Localiza la funcion fetch_data_for_nemo en el repo (autodescubrimiento).
2. Captura el resumen BCS de OXIQUIM e imprime TODAS sus claves y valores.
3. Marca las claves candidatas para el numero de acciones o valor libro.
4. Si hay valor libro: deriva N acciones = Patrimonio CMF / Valor Libro y
   lo valida (OXIQUIM tiene ~123 millones de acciones).
Solo lectura: no modifica nada.
"""
import glob, importlib.util, io, json, re, sys
from pathlib import Path

REPO = Path(r"C:\value-signal-local\repo")
MCP = Path(r"C:\mcp-bolsa")

# 1. Encontrar el modulo que define fetch_data_for_nemo (repo Y mcp-bolsa)
modulo_path = None
rutas = list(glob.glob(str(REPO / "*.py"))) + list(glob.glob(str(MCP / "*.py")))
for py in rutas:
    try:
        src = io.open(py, encoding="utf-8-sig").read()
    except Exception:
        continue
    if re.search(r"^def fetch_data_for_nemo", src, re.M) or re.search(r"^\s+def fetch_data_for_nemo", src, re.M):
        modulo_path = py
        break

if not modulo_path:
    sys.exit("ERROR: no encontre fetch_data_for_nemo en ningun .py del repo.")

print(f"fetch_data_for_nemo encontrada en: {Path(modulo_path).name}")

spec = importlib.util.spec_from_file_location("mod_bcs", modulo_path)
mod = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(MCP))
spec.loader.exec_module(mod)

# puede ser funcion de modulo o metodo: probar ambas
fetch = getattr(mod, "fetch_data_for_nemo", None)
if fetch is None:
    # buscar en clases del modulo
    for nombre in dir(mod):
        obj = getattr(mod, nombre)
        if isinstance(obj, type) and hasattr(obj, "fetch_data_for_nemo"):
            try:
                inst = obj()
                fetch = inst.fetch_data_for_nemo
                print(f"  (metodo de la clase {nombre})")
                break
            except Exception as e:
                print(f"  AVISO: no pude instanciar {nombre}: {e}")

if fetch is None:
    sys.exit("ERROR: fetch_data_for_nemo existe pero no pude invocarla.")

print("Capturando resumen BCS de OXIQUIM (tarda unos segundos)...")
data = fetch("OXIQUIM")

print()
print("=" * 60)
if isinstance(data, dict):
    print(f"Claves del data: {list(data.keys())}")
    resumen = data.get("resumen") or {}
else:
    resumen = data or {}

print()
print("RESUMEN BCS COMPLETO (todas las claves):")
candidatas = []
for k, v in sorted(resumen.items()):
    marca = ""
    if re.search(r"ACCION|NUM|LIBRO|PATRIM|CANTIDAD|SUSCRIT|PAGAD", str(k), re.I):
        marca = "   <<< CANDIDATA"
        candidatas.append((k, v))
    vs = str(v)
    print(f"  {k}: {vs[:60]}{marca}")

print()
if candidatas:
    print("CANDIDATAS PARA EL BPA:", candidatas)
else:
    print("Sin claves obvias de acciones/valor libro en el resumen.")

# 4. Validacion con valor libro si existe
vl = None
for k, v in resumen.items():
    if "LIBRO" in str(k).upper():
        try:
            vl = float(v)
        except Exception:
            pass
if vl:
    try:
        d = json.load(io.open(REPO / "acciones_chilenas.json", encoding="utf-8"))
        oxi = [a for a in d["acciones"] if a["ticker"] == "OXIQUIM"][0]
        patr_miles = oxi["cmf"]["patrimonio_total"]  # miles CLP
        acciones = (patr_miles * 1000) / vl
        print(f"\nVALIDACION: Patrimonio CMF {patr_miles:,.0f} miles / Valor Libro {vl:,.2f}")
        print(f"  => N acciones derivado: {acciones:,.0f}")
        print(f"  Esperado OXIQUIM: ~123.000.000  ->  {'COINCIDE' if 100e6 < acciones < 150e6 else 'NO CALZA, revisar'}")
    except Exception as e:
        print(f"\n(no pude validar con valor libro: {e})")
