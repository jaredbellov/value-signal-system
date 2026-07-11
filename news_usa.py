# -*- coding: utf-8 -*-
"""
NEWS USA (TSLA / NVDA)
=======================
Noticias e interpretacion de impacto para las acciones USA del watchlist.

Wrapper delgado sobre news_etfs.py: reutiliza su motor completo (Google News
RSS, analisis de impacto por keywords, resumen ejecutivo con Groq + fallback
a plantilla) y solo redefine queries, keywords especificas de cada accion y
el archivo de salida. Cualquier mejora futura a news_etfs.py se hereda sola.

Output: noticias_usa.json

Para agregar una accion nueva al watchlist USA:
1. Agregar su categoria en QUERIES_USA y CATEGORIA_META_USA
2. Agregar sus keywords de impacto en KEYWORDS_STOCK
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import subprocess as _sp

import news_etfs as base

SCRIPT_DIR = Path(__file__).parent
OUTPUT_FILE = SCRIPT_DIR / "noticias_usa.json"

# ============================================================
# Categorias y queries por accion
# ============================================================
QUERIES_USA = {
    "tesla": [
        "Tesla stock TSLA",
        "Tesla deliveries production",
        "Tesla FSD robotaxi Optimus",
    ],
    "nvidia": [
        "Nvidia stock NVDA",
        "Nvidia earnings data center",
        "Nvidia AI chips Blackwell Rubin",
    ],
    "ia_semis": [
        "AI chips semiconductor demand",
        "AI capex datacenter spending",
        "semiconductor export controls China",
    ],
    "macro_tech": [
        "Nasdaq tech stocks today",
        "Fed rates tech stocks",
        "Magnificent Seven stocks",
    ],
}

CATEGORIA_META_USA = {
    "tesla":      {"emoji": "🚗", "label": "Tesla (TSLA)"},
    "nvidia":     {"emoji": "🤖", "label": "NVIDIA (NVDA)"},
    "ia_semis":   {"emoji": "🔌", "label": "IA / Semiconductores"},
    "macro_tech": {"emoji": "🏛️", "label": "Macro / Tech USA"},
}

# ============================================================
# Keywords de impacto especificas de las acciones
# (se SUMAN al diccionario macro de news_etfs: Fed, inflacion,
#  recesion, earnings genericos, etc. ya vienen heredados)
# ============================================================
KEYWORDS_STOCK = {
    # === TESLA positivo ===
    "tesla beats":            ("positivo", 3, "Tesla sobre lo esperado"),
    "deliveries beat":        ("positivo", 3, "Entregas Tesla sobre lo esperado"),
    "record deliveries":      ("positivo", 3, "Entregas record de Tesla"),
    "fsd approval":           ("positivo", 3, "Aprobacion regulatoria de FSD"),
    "robotaxi launch":        ("positivo", 2, "Lanzamiento robotaxi: catalizador Tesla"),
    "robotaxi expansion":     ("positivo", 2, "Expansion robotaxi: catalizador Tesla"),
    "optimus":                ("positivo", 1, "Avances en Optimus: opcionalidad Tesla"),
    "megapack":               ("positivo", 2, "Energia/Megapack: segmento de alto margen"),
    "tesla margin":           ("positivo", 1, "Margenes Tesla en foco"),
    # === TESLA negativo ===
    "tesla recall":           ("negativo", 2, "Recall de Tesla: costo y reputacion"),
    "deliveries miss":        ("negativo", 3, "Entregas Tesla bajo lo esperado"),
    "tesla misses":           ("negativo", 3, "Tesla bajo lo esperado"),
    "price cuts":             ("negativo", 2, "Recortes de precio: presion de margenes"),
    "ev demand slows":        ("negativo", 2, "Demanda EV desacelerando"),
    "ev slowdown":            ("negativo", 2, "Desaceleracion EV"),
    "byd":                    ("negativo", 1, "Competencia china (BYD) presiona a Tesla"),
    "nhtsa investigation":    ("negativo", 2, "Investigacion NHTSA: riesgo regulatorio"),
    "musk sells":             ("negativo", 2, "Venta de acciones de Musk"),
    # === NVIDIA positivo ===
    "nvidia beats":           ("positivo", 3, "Nvidia sobre lo esperado: lider IA"),
    "record data center":     ("positivo", 3, "Datacenter record: motor de Nvidia"),
    "blackwell demand":       ("positivo", 3, "Demanda Blackwell solida"),
    "rubin":                  ("positivo", 2, "Proxima arquitectura Rubin: roadmap fuerte"),
    "ai capex":               ("positivo", 2, "Capex en IA: demanda estructural de GPUs"),
    "sold out":               ("positivo", 2, "Capacidad vendida: demanda supera oferta"),
    "raises guidance":        ("positivo", 3, "Guidance al alza"),
    # === NVIDIA negativo ===
    "nvidia misses":          ("negativo", 3, "Nvidia bajo lo esperado"),
    "export restrictions":    ("negativo", 3, "Restricciones de exportacion: riesgo China"),
    "export controls":        ("negativo", 3, "Controles de exportacion: riesgo China"),
    "china ban":              ("negativo", 3, "Veto chino: riesgo de ingresos Nvidia"),
    "chip glut":              ("negativo", 2, "Sobreoferta de chips"),
    "ai bubble":              ("negativo", 2, "Narrativa de burbuja IA"),
    "amd gains":              ("negativo", 1, "AMD ganando terreno"),
    "custom chips":           ("negativo", 1, "Chips propios de hyperscalers: competencia"),
    "antitrust":              ("negativo", 2, "Riesgo antimonopolio"),
}


def git_sync_and_push():
    """Igual que base.git_sync_and_push pero para noticias_usa.json."""
    # [PLAN-B] git neutralizado: publicar.py se encarga del push
    return
    def g(args):
        try:
            r = _sp.run(["git"] + args, cwd=SCRIPT_DIR, capture_output=True,
                        text=True, timeout=60)
            return r.returncode, r.stdout, r.stderr
        except Exception as e:
            return -1, "", str(e)
    code, out, _ = g(["status", "--porcelain", OUTPUT_FILE.name])
    if not out.strip():
        base.log.info(f"Sin cambios en {OUTPUT_FILE.name}")
        return
    g(["pull", "--rebase", "--autostash"])
    g(["add", OUTPUT_FILE.name])
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    g(["commit", "-m", f"chore: update noticias USA {ts} [skip ci]"])
    code, _, err = g(["push"])
    if code != 0:
        g(["pull", "--rebase", "--autostash"])
        g(["push"])
    base.log.info("git push OK")


def main():
    # Reconfigurar el motor heredado para acciones USA
    base.QUERIES = QUERIES_USA
    base.CATEGORIA_META = CATEGORIA_META_USA
    base.ASSET_LABEL_RESUMEN = "las acciones Tesla (TSLA) y NVIDIA (NVDA)"
    base.IMPACTO_KEYWORDS = {**base.IMPACTO_KEYWORDS, **KEYWORDS_STOCK}

    base.log.info("=" * 60)
    base.log.info("=== Update Noticias Acciones USA (TSLA/NVDA) ===")
    cutoff = datetime.now(timezone.utc) - timedelta(days=base.VENTANA_DIAS)
    base.log.info(f"Cutoff: {cutoff.isoformat()}")

    noticias_por_categoria = {}
    total = 0
    for categoria, keywords in QUERIES_USA.items():
        noticias = base.fetch_categoria(categoria, keywords, cutoff)
        noticias_por_categoria[categoria] = noticias
        total += len(noticias)

    resumen_ejecutivo = base.generar_resumen_ejecutivo(noticias_por_categoria)

    output = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ventana_dias": base.VENTANA_DIAS,
        "categorias_meta": CATEGORIA_META_USA,
        "noticias_por_categoria": noticias_por_categoria,
        "stats": {
            "total_noticias": total,
            "por_categoria": {k: len(v) for k, v in noticias_por_categoria.items()},
        },
        "resumen_ejecutivo": resumen_ejecutivo,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    base.log.info(f"Guardado: {OUTPUT_FILE} ({total} noticias)")
    git_sync_and_push()
    base.log.info(f"=== FIN ({datetime.now().strftime('%H:%M:%S')}) ===")


if __name__ == "__main__":
    main()
