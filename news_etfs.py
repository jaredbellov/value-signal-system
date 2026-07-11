"""
Fetch noticias macro del mercado accionario USA (S&P 500 / Nasdaq) desde Google News RSS.
Cubre: Fed/tasas, mercado, tech, economia/recesion, earnings, sentimiento.
Sin API key necesaria.

Output: noticias_etfs.json
"""
import json
import logging
import re
import subprocess as _sp
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "news_etfs.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

OUTPUT_FILE = SCRIPT_DIR / "noticias_etfs.json"
VENTANA_DIAS = 3
MAX_POR_CATEGORIA = 5
ASSET_LABEL_RESUMEN = "el mercado accionario de EEUU (S&P 500 y Nasdaq)"

QUERIES = {
    "fed_tasas": [
        "Federal Reserve rate decision",
        "Fed Powell tasas interes",
        "FOMC meeting rates",
    ],
    "mercado": [
        "S&P 500 today",
        "stock market today wall street",
        "bolsa Estados Unidos hoy",
    ],
    "tech_nasdaq": [
        "Nasdaq tech stocks today",
        "AI stocks rally",
        "tech earnings results",
    ],
    "economia": [
        "US recession risk",
        "US jobs report unemployment",
        "inflacion Estados Unidos CPI",
    ],
    "earnings": [
        "earnings season results",
        "corporate earnings beat miss",
        "guidance outlook companies",
    ],
    "sentimiento": [
        "stock market fear greed",
        "market correction forecast",
        "stock market outlook 2026",
    ],
}

CATEGORIA_META = {
    "fed_tasas":   {"emoji": "🏛️", "label": "Fed / Tasas"},
    "mercado":     {"emoji": "📈", "label": "Mercado S&P 500"},
    "tech_nasdaq": {"emoji": "💻", "label": "Tech / Nasdaq"},
    "economia":    {"emoji": "🏭", "label": "Economia / Recesion"},
    "earnings":    {"emoji": "💼", "label": "Earnings"},
    "sentimiento": {"emoji": "🌡️", "label": "Sentimiento de mercado"},
}

# ============================================================
# Diccionario de keywords con peso e impacto en BTC
# ============================================================
IMPACTO_KEYWORDS = {
    # === POSITIVO PARA ACCIONES USA ===
    # --- Fed dovish ---
    "rate cut":            ("positivo", 3, "Fed dovish: rate cut impulsa acciones"),
    "rate cuts":           ("positivo", 3, "Fed dovish: rate cuts impulsan acciones"),
    "lower rates":         ("positivo", 2, "Tasas mas bajas: positivo para equities"),
    "dovish":              ("positivo", 2, "Tono dovish de la Fed: positivo"),
    "recorte de tasas":    ("positivo", 3, "Fed dovish: recorte impulsa acciones"),
    "baja tasas":          ("positivo", 3, "Fed dovish: positivo para acciones"),
    "fed pivot":           ("positivo", 3, "Fed pivot: muy positivo para equities"),
    "fed pause":           ("positivo", 2, "Fed en pausa: alivio para equities"),
    # --- Inflacion BAJANDO (positivo equities, OPUESTO al oro) ---
    "inflation falls":     ("positivo", 3, "Inflacion cede: alivio para la Fed y equities"),
    "inflation eases":     ("positivo", 3, "Inflacion cede: positivo para equities"),
    "inflation cools":     ("positivo", 3, "Inflacion se enfria: positivo"),
    "cooling inflation":   ("positivo", 3, "Inflacion enfriandose: positivo"),
    "inflacion baja":      ("positivo", 3, "Inflacion baja: positivo para acciones"),
    "inflacion cede":      ("positivo", 3, "Inflacion cede: positivo para acciones"),
    "soft landing":        ("positivo", 3, "Soft landing: escenario ideal para equities"),
    "aterrizaje suave":    ("positivo", 3, "Aterrizaje suave: escenario ideal"),
    # --- Crecimiento / earnings ---
    "earnings beat":       ("positivo", 3, "Earnings sobre lo esperado"),
    "beats expectations":  ("positivo", 3, "Resultados sobre lo esperado"),
    "strong earnings":     ("positivo", 3, "Earnings fuertes: soporte fundamental"),
    "raises guidance":     ("positivo", 3, "Guidance al alza: confianza corporativa"),
    "strong guidance":     ("positivo", 2, "Guidance fuerte"),
    "gdp growth":          ("positivo", 2, "Crecimiento del PIB: soporte macro"),
    "economy grows":       ("positivo", 2, "Economia creciendo"),
    "job growth":          ("positivo", 2, "Creacion de empleo solida"),
    "consumer spending":   ("positivo", 1, "Consumo resiliente"),
    "buyback":             ("positivo", 2, "Recompras: soporte para el precio"),
    "stimulus":            ("positivo", 2, "Estimulo: viento a favor"),
    # --- Tech / AI ---
    "ai boom":             ("positivo", 2, "Boom de IA: impulso para tech"),
    "ai rally":            ("positivo", 2, "Rally de IA: impulso Nasdaq"),
    "chip demand":         ("positivo", 2, "Demanda de chips: soporte tech"),
    "nvidia beats":        ("positivo", 3, "Nvidia sobre lo esperado: lider tech"),
    # --- Mercado sube directo ---
    "record high":         ("positivo", 3, "Maximos historicos"),
    "all-time high":       ("positivo", 3, "ATH: momentum fuerte (cuidado: tope)"),
    "all time high":       ("positivo", 3, "ATH: momentum fuerte (cuidado: tope)"),
    "stocks rally":        ("positivo", 2, "Rally accionario"),
    "stocks rise":         ("positivo", 2, "Acciones al alza"),
    "stocks surge":        ("positivo", 3, "Acciones saltan con fuerza"),
    "stocks climb":        ("positivo", 2, "Acciones al alza"),
    "wall street sube":    ("positivo", 2, "Wall Street al alza"),
    "bolsa sube":          ("positivo", 2, "Bolsa al alza"),
    "rebote":              ("positivo", 1, "Rebote del mercado"),
    "extreme fear":        ("positivo", 2, "Extreme fear: contrarian, suelo posible"),
    "miedo extremo":       ("positivo", 2, "Miedo extremo: contrarian, suelo posible"),

    # === NEGATIVO PARA ACCIONES USA ===
    # --- Fed hawkish / inflacion ALTA (OPUESTO al oro) ---
    "rate hike":           ("negativo", 3, "Fed hawkish: rate hike presiona equities"),
    "rate hikes":          ("negativo", 3, "Fed hawkish: presion para equities"),
    "higher rates":        ("negativo", 2, "Tasas mas altas: presion para equities"),
    "hawkish":             ("negativo", 2, "Tono hawkish: presion"),
    "sube tasas":          ("negativo", 3, "Fed hawkish: presion para acciones"),
    "higher for longer":   ("negativo", 3, "Tasas altas por mas tiempo: presion"),
    "hot inflation":       ("negativo", 3, "Inflacion caliente: Fed hawkish, presion"),
    "inflation rises":     ("negativo", 3, "Inflacion sube: presion para equities"),
    "inflation surges":    ("negativo", 3, "Inflacion se dispara: muy negativo"),
    "sticky inflation":    ("negativo", 3, "Inflacion pegajosa: presion prolongada"),
    "inflacion sube":      ("negativo", 3, "Inflacion sube: presion para acciones"),
    "inflacion alta":      ("negativo", 2, "Inflacion alta: presion Fed"),
    "yields surge":        ("negativo", 2, "Yields disparados: presion sobre equities"),
    "yields rise":         ("negativo", 1, "Yields al alza: viento en contra"),
    # --- Recesion / empleo debil ---
    "recession":           ("negativo", 3, "Riesgo de recesion: muy negativo"),
    "recesion":            ("negativo", 3, "Riesgo de recesion: muy negativo"),
    "layoffs":             ("negativo", 2, "Despidos: senal de desaceleracion"),
    "despidos":            ("negativo", 2, "Despidos: senal de desaceleracion"),
    "unemployment rises":  ("negativo", 2, "Desempleo sube: desaceleracion"),
    "jobs miss":           ("negativo", 2, "Empleo bajo lo esperado"),
    "weak data":           ("negativo", 2, "Datos macro debiles"),
    "hard landing":        ("negativo", 3, "Hard landing: muy negativo"),
    "stagflation":         ("negativo", 3, "Estanflacion: peor escenario para equities"),
    "default":             ("negativo", 2, "Riesgo de default"),
    "bank failure":        ("negativo", 3, "Quiebra bancaria: riesgo sistemico"),
    "credit crunch":       ("negativo", 3, "Credit crunch: contraccion crediticia"),
    # --- Earnings debiles ---
    "earnings miss":       ("negativo", 3, "Earnings bajo lo esperado"),
    "misses expectations": ("negativo", 3, "Resultados bajo lo esperado"),
    "weak earnings":       ("negativo", 2, "Earnings debiles"),
    "cuts guidance":       ("negativo", 3, "Guidance recortado: alerta corporativa"),
    "lowers guidance":     ("negativo", 3, "Guidance a la baja: alerta"),
    "profit warning":      ("negativo", 3, "Profit warning: alerta fuerte"),
    # --- Geopolitica / shocks (negativo equities, contrario al oro) ---
    "tariffs":             ("negativo", 2, "Aranceles: presion sobre margenes"),
    "aranceles":           ("negativo", 2, "Aranceles: presion sobre margenes"),
    "trade war":           ("negativo", 3, "Guerra comercial: muy negativo"),
    "guerra comercial":    ("negativo", 3, "Guerra comercial: muy negativo"),
    "war escalates":       ("negativo", 2, "Escalada belica: risk-off"),
    "geopolitical tensions":("negativo", 2, "Tensiones geopoliticas: risk-off"),
    # --- Mercado baja directo ---
    "stocks fall":         ("negativo", 2, "Acciones a la baja"),
    "stocks drop":         ("negativo", 2, "Acciones caen"),
    "stocks tumble":       ("negativo", 3, "Acciones caen fuerte"),
    "stocks plunge":       ("negativo", 3, "Acciones se desploman"),
    "selloff":             ("negativo", 2, "Selloff: presion vendedora"),
    "sell-off":            ("negativo", 2, "Sell-off: presion vendedora"),
    "correction":          ("negativo", 2, "Correccion en curso o anticipada"),
    "correccion":          ("negativo", 2, "Correccion en curso o anticipada"),
    "market crash":        ("negativo", 3, "Crash: muy negativo"),
    "se desploma":         ("negativo", 3, "Desplome: muy negativo"),
    "wall street cae":     ("negativo", 2, "Wall Street a la baja"),
    "bolsa cae":           ("negativo", 2, "Bolsa a la baja"),
    "bear market":         ("negativo", 2, "Bear market"),
    "bubble":              ("negativo", 2, "Alerta de burbuja"),
    "burbuja":             ("negativo", 2, "Alerta de burbuja"),
    "overvalued":          ("negativo", 2, "Sobrevaloracion advertida"),
    "extreme greed":       ("negativo", 1, "Extreme greed: contrarian, posible tope"),
    "fomo":                ("negativo", 1, "FOMO: senal de tope cercano"),
}


def build_query_url(keyword):
    q = urllib.parse.quote_plus(keyword)
    return f"https://news.google.com/rss/search?q={q}&hl=es-CL&gl=CL&ceid=CL:es"


def fetch_rss(url, timeout=20):
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.warning(f"  fetch_rss fallo: {e}")
        return None


def parse_rss(xml_text):
    items = []
    if not xml_text:
        return items
    try:
        root = ET.fromstring(xml_text)
        channel = root.find("channel")
        if channel is None:
            return items
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pubdate = (item.findtext("pubDate") or "").strip()
            source_el = item.find("source")
            source = (source_el.text if source_el is not None else "") or ""
            items.append({
                "title": title, "link": link, "pubdate": pubdate, "source": source.strip(),
            })
    except Exception as e:
        log.warning(f"  parse_rss fallo: {e}")
    return items


def parse_pubdate(pubdate_str):
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pubdate_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def is_reciente(item, cutoff):
    dt = parse_pubdate(item.get("pubdate", ""))
    return dt is not None and dt >= cutoff


def clean_title(title):
    return re.sub(r"\s+-\s+[^-]+$", "", title).strip()


def normalizar(s):
    import unicodedata
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def analizar_impacto(titulo, categoria):
    """
    Analiza impacto del titulo. Mejoras:
    - Word boundary matching (no substring) para keywords cortas
    - Razonamiento del factor de mayor peso dominante en la direccion final
    """
    import re as _re
    titulo_norm = normalizar(titulo)
    score_pos = 0
    score_neg = 0
    matches_pos = []   # (peso, razon)
    matches_neg = []
    for kw, (direccion, peso, razon) in IMPACTO_KEYWORDS.items():
        kw_norm = normalizar(kw)
        # Si la keyword tiene >1 palabra, usar substring match (es especifica)
        # Si tiene 1 palabra, usar word boundary (\b...\b) para evitar falsos positivos
        if " " in kw_norm:
            matched = kw_norm in titulo_norm
        else:
            matched = bool(_re.search(r"\b" + _re.escape(kw_norm) + r"\b", titulo_norm))
        if matched:
            if direccion == "positivo":
                score_pos += peso
                matches_pos.append((peso, razon))
            else:
                score_neg += peso
                matches_neg.append((peso, razon))

    diff = score_pos - score_neg
    if abs(diff) < 1:
        direccion = "neutral"
        intensidad = 0
        razonamiento = "Sin senal direccional clara en el titular"
    elif diff > 0:
        direccion = "positivo"
        intensidad = min(3, max(1, abs(diff) // 2 + 1))
        # Tomar el razon del match positivo de mayor peso
        razonamiento = max(matches_pos, key=lambda x: x[0])[1] if matches_pos else "Sesgo positivo para acciones USA"
    else:
        direccion = "negativo"
        intensidad = min(3, max(1, abs(diff) // 2 + 1))
        razonamiento = max(matches_neg, key=lambda x: x[0])[1] if matches_neg else "Sesgo negativo para acciones USA"

    emoji_map = {
        ("positivo", 3): "🟢🟢", ("positivo", 2): "🟢", ("positivo", 1): "🟢",
        ("negativo", 3): "🔴🔴", ("negativo", 2): "🔴", ("negativo", 1): "🔴",
        ("neutral", 0): "⚪",
    }
    emoji = emoji_map.get((direccion, intensidad), "⚪")
    intensidad_label = {0: "neutral", 1: "leve", 2: "moderado", 3: "fuerte"}[intensidad]
    return {
        "direccion": direccion, "intensidad": intensidad,
        "intensidad_label": intensidad_label, "emoji": emoji, "razonamiento": razonamiento,
    }


def generar_resumen_ejecutivo(noticias_por_categoria):
    """
    Calcula sentimiento neto del feed y genera narrativa (Groq con fallback a plantilla).
    Retorna dict con sentimiento, contadores, narrativa y veredicto.
    """
    # --- 1. Sentimiento neto desde los impactos ya calculados ---
    score_neto = 0
    n_pos = 0
    n_neg = 0
    n_neu = 0
    titulares_con_senal = []
    for cat, items in noticias_por_categoria.items():
        for it in items:
            imp = it.get("impacto", {}) or {}
            d = imp.get("direccion", "neutral")
            i = imp.get("intensidad", 0)
            if d == "positivo":
                score_neto += i
                n_pos += 1
                titulares_con_senal.append(("+", i, it.get("title", ""), imp.get("razonamiento", "")))
            elif d == "negativo":
                score_neto -= i
                n_neg += 1
                titulares_con_senal.append(("-", i, it.get("title", ""), imp.get("razonamiento", "")))
            else:
                n_neu += 1

    # --- 2. Veredicto direccional ---
    if score_neto >= 4:
        sentimiento = "alcista"
        veredicto = "ALCISTA a corto plazo"
    elif score_neto <= -4:
        sentimiento = "bajista"
        veredicto = "BAJISTA a corto plazo"
    elif score_neto >= 2:
        sentimiento = "levemente alcista"
        veredicto = "Levemente ALCISTA"
    elif score_neto <= -2:
        sentimiento = "levemente bajista"
        veredicto = "Levemente BAJISTA"
    else:
        sentimiento = "neutral"
        veredicto = "NEUTRAL - sin sesgo dominante"

    # --- 3. Narrativa: Groq con fallback a plantilla ---
    narrativa = None
    narrativa_fuente = "plantilla"
    try:
        from groq_interpreter import groq_available, query_groq
        available, api_key = groq_available()
        if available and titulares_con_senal:
            # Ordenar por intensidad y tomar los 10 mas fuertes
            top_senales = sorted(titulares_con_senal, key=lambda x: x[1], reverse=True)[:10]
            lista_txt = "\n".join(
                f"[{s}{i}] {t[:100]} ({r})" for s, i, t, r in top_senales
            )
            prompt = (
                f"Eres analista financiero. Estas son las noticias de hoy sobre {ASSET_LABEL_RESUMEN}, "
                f"con su impacto estimado ([+N] positivo, [-N] negativo, N=intensidad 1-3):\n\n"
                f"{lista_txt}\n\n"
                f"El sentimiento neto del feed es {sentimiento} (score {score_neto:+d}, "
                f"{n_pos} positivas vs {n_neg} negativas).\n\n"
                f"Escribe un resumen de maximo 3 frases y 60 palabras en espanol que sintetice el panorama "
                f"y los principales catalizadores. Sin markdown, sin titulos, solo el texto plano."
            )
            respuesta = query_groq(prompt, api_key)
            if respuesta and len(respuesta.strip()) > 20:
                narrativa = respuesta.strip()[:600]
                narrativa_fuente = "groq"
    except Exception as e:
        log.warning(f"Groq no disponible para resumen: {e}")

    if not narrativa:
        # Plantilla determinista
        if titulares_con_senal:
            top = sorted(titulares_con_senal, key=lambda x: x[1], reverse=True)[:2]
            razones_top = "; ".join(r for _, _, _, r in top if r)
            narrativa = (
                f"El feed muestra {n_pos} senales positivas y {n_neg} negativas "
                f"(neto {score_neto:+d}). Factores dominantes: {razones_top}."
            )
        else:
            narrativa = "Sin senales direccionales claras en las noticias de las ultimas 72 horas."

    log.info(f"Resumen ejecutivo: {sentimiento} (neto {score_neto:+d}) via {narrativa_fuente}")
    return {
        "sentimiento": sentimiento,
        "score_neto": score_neto,
        "n_positivas": n_pos,
        "n_negativas": n_neg,
        "n_neutrales": n_neu,
        "narrativa": narrativa,
        "narrativa_fuente": narrativa_fuente,
        "veredicto": veredicto,
    }


def fetch_categoria(categoria, keywords, cutoff):
    log.info(f"\n--- Categoria: {categoria} ---")
    items_brutos = []
    for kw in keywords:
        url = build_query_url(kw)
        xml = fetch_rss(url)
        items = parse_rss(xml)
        items_brutos.extend(items)
        log.info(f"  '{kw}': {len(items)} resultados")
    recientes = [it for it in items_brutos if is_reciente(it, cutoff)]
    log.info(f"  Recientes (<={VENTANA_DIAS}d): {len(recientes)}")
    vistos = set()
    unicos = []
    for it in recientes:
        clave = normalizar(clean_title(it["title"]))
        if clave and clave not in vistos:
            vistos.add(clave)
            titulo_limpio = clean_title(it["title"])
            impacto = analizar_impacto(titulo_limpio, categoria)
            unicos.append({
                "title": titulo_limpio, "link": it["link"], "source": it["source"],
                "pubdate": it["pubdate"],
                "pubdate_iso": parse_pubdate(it["pubdate"]).isoformat() if parse_pubdate(it["pubdate"]) else "",
                "impacto": impacto,
            })
    unicos.sort(key=lambda x: x.get("pubdate_iso", ""), reverse=True)
    top = unicos[:MAX_POR_CATEGORIA]
    log.info(f"  Unicos: {len(unicos)}, top {len(top)} seleccionados")
    return top


def ejecutar_git(args):
    try:
        r = _sp.run(["git"] + args, cwd=SCRIPT_DIR, capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)


def git_sync_and_push():
    # [PLAN-B] git neutralizado: publicar.py se encarga del push
    return
    code, out, _ = ejecutar_git(["status", "--porcelain", "noticias_etfs.json"])
    if not out.strip():
        log.info("Sin cambios en noticias_etfs.json")
        return
    ejecutar_git(["pull", "--rebase", "--autostash"])
    ejecutar_git(["add", "noticias_etfs.json"])
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    ejecutar_git(["commit", "-m", f"chore: update noticias ETFs {ts} [skip ci]"])
    code, _, err = ejecutar_git(["push"])
    if code != 0:
        ejecutar_git(["pull", "--rebase", "--autostash"])
        ejecutar_git(["push"])
    log.info("git push OK")


def main():
    log.info("=" * 60)
    log.info("=== Update Noticias ETFs USA ===")
    cutoff = datetime.now(timezone.utc) - timedelta(days=VENTANA_DIAS)
    log.info(f"Cutoff: {cutoff.isoformat()}")
    noticias_por_categoria = {}
    total = 0
    for categoria, keywords in QUERIES.items():
        noticias = fetch_categoria(categoria, keywords, cutoff)
        noticias_por_categoria[categoria] = noticias
        total += len(noticias)
    resumen_ejecutivo = generar_resumen_ejecutivo(noticias_por_categoria)
    output = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ventana_dias": VENTANA_DIAS,
        "categorias_meta": CATEGORIA_META,
        "noticias_por_categoria": noticias_por_categoria,
        "stats": {
            "total_noticias": total,
            "por_categoria": {k: len(v) for k, v in noticias_por_categoria.items()},
        },
        "resumen_ejecutivo": resumen_ejecutivo,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log.info(f"\nGuardado: {OUTPUT_FILE} ({total} noticias)")
    git_sync_and_push()
    log.info(f"=== FIN ({datetime.now().strftime('%H:%M:%S')}) ===")


if __name__ == "__main__":
    main()
