"""
Fetch noticias macro relacionadas con el oro (GLD) desde Google News RSS.
Cubre: precio del oro, Fed/tasas, inflacion, DXY, geopolitica, bancos
centrales. Sin API key necesaria.

Output: noticias_gld.json
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
LOG_FILE = LOG_DIR / "news_gld.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

OUTPUT_FILE = SCRIPT_DIR / "noticias_gld.json"
VENTANA_DIAS = 3  # ventana de noticias recientes
MAX_POR_CATEGORIA = 5
ASSET_LABEL_RESUMEN = "el oro (GLD)"

# Queries por categoria. Mezcla espanol + ingles para mayor cobertura.
QUERIES = {
    "precio": [
        "precio oro hoy",
        "gold price today",
        "oro spot XAU",
    ],
    "fed_macro": [
        "Federal Reserve tasas",
        "Fed rate decision",
        "inflacion EEUU CPI",
        "real yields treasury",
    ],
    "dolar": [
        "DXY dollar index",
        "indice dolar",
        "dolar debilidad fortaleza",
    ],
    "bancos_centrales": [
        "central bank gold buying",
        "bancos centrales oro reservas",
        "China gold reserves",
    ],
    "geopolitica": [
        "safe haven oro tensiones",
        "geopolitical tensions gold",
        "guerra impacto oro",
    ],
    "proyecciones": [
        "gold forecast 2026",
        "proyeccion precio oro Goldman",
        "JP Morgan gold target",
    ],
}

CATEGORIA_META = {
    "precio":          {"emoji": "💰", "label": "Precio del oro"},
    "fed_macro":       {"emoji": "🏦", "label": "Fed / Macro"},
    "dolar":           {"emoji": "💵", "label": "DXY / Dolar"},
    "bancos_centrales":{"emoji": "🏛️", "label": "Bancos centrales"},
    "geopolitica":     {"emoji": "🌍", "label": "Geopolitica"},
    "proyecciones":    {"emoji": "📈", "label": "Proyecciones"},
}


def build_query_url(keyword):
    """Construye URL de Google News RSS para una keyword."""
    q = urllib.parse.quote_plus(keyword)
    # hl=es-CL: titulares en espanol cuando existan, fallback ingles
    return f"https://news.google.com/rss/search?q={q}&hl=es-CL&gl=CL&ceid=CL:es"


def fetch_rss(url, timeout=20):
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.warning(f"  fetch_rss fallo para {url[:80]}: {e}")
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
                "title": title,
                "link": link,
                "pubdate": pubdate,
                "source": source.strip(),
            })
    except Exception as e:
        log.warning(f"  parse_rss fallo: {e}")
    return items


def parse_pubdate(pubdate_str):
    """Parsea pubDate RSS (RFC 2822) a datetime UTC."""
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
    if dt is None:
        return False
    return dt >= cutoff


def clean_title(title):
    """Limpia el sufijo ' - Medio' que Google News agrega al final."""
    title = re.sub(r"\s+-\s+[^-]+$", "", title)
    return title.strip()


def normalizar(s):
    """Para deduplicar: minusculas, sin tildes, sin puntuacion."""
    import unicodedata
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


# ============================================================
# ANALISIS DE IMPACTO
# ============================================================

# Diccionario de keywords con peso (+) positivo para oro / (-) negativo para oro
# Peso 3 = factor muy fuerte, 2 = fuerte, 1 = moderado
IMPACTO_KEYWORDS = {
    # === POSITIVO PARA EL ORO (presion al alza) ===
    # --- Tasas / Fed dovish ---
    "baja tasas":          ("positivo", 3, "Fed dovish: tasas mas bajas favorecen oro"),
    "bajar tasas":         ("positivo", 3, "Fed dovish: tasas mas bajas favorecen oro"),
    "recorte de tasas":    ("positivo", 3, "Fed dovish: recorte de tasas favorece oro"),
    "recortes de tasas":   ("positivo", 3, "Fed dovish: recortes de tasas favorecen oro"),
    "rate cut":            ("positivo", 3, "Fed dovish: rate cut favorece oro"),
    "rate cuts":           ("positivo", 3, "Fed dovish: rate cuts favorecen oro"),
    "tasas mas bajas":     ("positivo", 2, "Expectativa de tasas bajas favorece oro"),
    "lower rates":         ("positivo", 2, "Lower rates favorece oro"),
    "dovish":              ("positivo", 2, "Tono dovish de la Fed favorece oro"),
    "fed dovish":          ("positivo", 3, "Fed dovish favorece oro"),
    "rate cuts ahead":     ("positivo", 3, "Expectativa de recortes futuros favorece oro"),
    "pause rate hikes":    ("positivo", 2, "Fed en pausa: positivo para oro"),

    # --- Inflacion alta ---
    "inflacion alta":      ("positivo", 3, "Inflacion al alza: oro como cobertura"),
    "inflacion mas alta":  ("positivo", 3, "Inflacion mas alta: oro como cobertura"),
    "inflacion sube":      ("positivo", 3, "Inflacion sube: oro como cobertura"),
    "inflacion creciente": ("positivo", 3, "Inflacion creciente: oro como cobertura"),
    "inflacion al alza":   ("positivo", 3, "Inflacion al alza: oro como cobertura"),
    "inflacion subyacente":("positivo", 2, "Core inflation persistente: positivo para oro"),
    "high inflation":      ("positivo", 3, "Alta inflacion favorece oro"),
    "higher inflation":    ("positivo", 3, "Inflacion mas alta: positivo para oro"),
    "rising inflation":    ("positivo", 3, "Inflacion creciente favorece oro"),
    "inflation rises":     ("positivo", 3, "Inflacion sube: positivo para oro"),
    "inflation jumps":     ("positivo", 3, "Inflacion salta: positivo para oro"),
    "sticky inflation":    ("positivo", 2, "Inflacion persistente: positivo para oro"),
    "cpi sube":            ("positivo", 2, "CPI al alza favorece oro"),
    "cpi rises":           ("positivo", 2, "CPI al alza: positivo para oro"),
    "core cpi":            ("positivo", 1, "Mencion de core CPI: posible relevancia para oro"),
    "presion inflacionaria":("positivo", 2, "Presion inflacionaria favorece oro"),
    "stagflation":         ("positivo", 3, "Estanflacion: muy positivo para oro"),

    # --- Dolar debil ---
    "dolar cae":           ("positivo", 2, "Dolar debil: oro sube por correlacion inversa"),
    "dolar baja":          ("positivo", 2, "Dolar debil: oro sube por correlacion inversa"),
    "dolar debil":         ("positivo", 2, "Dolar debil favorece oro"),
    "dxy cae":             ("positivo", 2, "DXY debil favorece oro"),
    "dxy baja":            ("positivo", 2, "DXY debil favorece oro"),
    "dollar weak":         ("positivo", 2, "Dolar debil favorece oro"),
    "weaker dollar":       ("positivo", 2, "Dolar mas debil favorece oro"),
    "weak dollar":         ("positivo", 2, "Dolar debil favorece oro"),
    "weak us dollar":      ("positivo", 2, "Dolar debil favorece oro"),
    "dollar drops":        ("positivo", 2, "Dolar baja: positivo para oro"),
    "dollar falls":        ("positivo", 2, "Dolar baja: positivo para oro"),
    "dollar slips":        ("positivo", 1, "Dolar baja levemente: leve positivo para oro"),
    "dollar declines":     ("positivo", 2, "Dolar baja: positivo para oro"),
    "dollar tumbles":      ("positivo", 2, "Dolar cae fuerte: positivo para oro"),
    "presion del dolar":   ("positivo", 1, "Dolar bajo presion: leve positivo para oro"),

    # --- Bancos centrales comprando ---
    "central banks buy":   ("positivo", 3, "Bancos centrales compran oro: demanda institucional"),
    "central bank buying": ("positivo", 3, "Bancos centrales comprando oro: demanda institucional"),
    "central bank purchases":("positivo", 3, "Compras de bancos centrales: demanda institucional"),
    "comprar oro":         ("positivo", 2, "Demanda institucional de oro"),
    "comprado oro":        ("positivo", 2, "Demanda institucional de oro"),
    "compra de oro":       ("positivo", 2, "Demanda institucional de oro"),
    "compras de oro":      ("positivo", 2, "Demanda institucional de oro"),
    "banco central compra":("positivo", 3, "Banco central comprando oro: demanda institucional"),
    "gold reserves":       ("positivo", 2, "Acumulacion de reservas de oro"),
    "reservas de oro":     ("positivo", 2, "Acumulacion de reservas de oro"),
    "china gold":          ("positivo", 2, "China acumulando oro: demanda institucional"),
    "india gold":          ("positivo", 1, "Demanda india de oro"),
    "gold buying":         ("positivo", 2, "Compra de oro: demanda creciente"),
    "gold demand":         ("positivo", 1, "Demanda de oro mencionada"),

    # --- Geopolitica / safe haven ---
    "tensiones":           ("positivo", 2, "Tensiones geopoliticas: flujo a safe haven"),
    "tension geopolitica": ("positivo", 2, "Tension geopolitica: flujo a safe haven"),
    "geopolitical":        ("positivo", 2, "Tension geopolitica: flujo a safe haven"),
    "war":                 ("positivo", 2, "Conflicto belico: flujo a oro como refugio"),
    "guerra":              ("positivo", 2, "Conflicto belico: flujo a oro como refugio"),
    "safe haven":          ("positivo", 2, "Demanda de activos refugio"),
    "refugio":             ("positivo", 1, "Demanda de activo refugio"),
    "haven demand":        ("positivo", 2, "Flujo a activos refugio"),
    "crisis":              ("positivo", 2, "Crisis aumenta demanda de oro como refugio"),
    "recesion":            ("positivo", 2, "Temor recesivo favorece oro"),
    "recession":           ("positivo", 2, "Recession fears favorecen oro"),
    "uncertainty":         ("positivo", 1, "Incertidumbre: leve flujo a oro"),
    "incertidumbre":       ("positivo", 1, "Incertidumbre: leve flujo a oro"),

    # --- Yields reales bajos ---
    "yields fall":         ("positivo", 2, "Yields reales caen: positivo para oro"),
    "yields drop":         ("positivo", 2, "Yields reales bajan: positivo para oro"),
    "real yields drop":    ("positivo", 2, "Yields reales bajan: positivo para oro"),
    "treasury yields fall":("positivo", 2, "Treasury yields bajan: positivo para oro"),

    # --- Oro sube directo ---
    "gold rises":          ("positivo", 2, "Oro al alza"),
    "gold rallies":        ("positivo", 2, "Rally del oro"),
    "gold surges":         ("positivo", 3, "Oro salta con fuerza"),
    "gold jumps":          ("positivo", 2, "Oro al alza"),
    "gold climbs":         ("positivo", 2, "Oro al alza"),
    "gold gains":          ("positivo", 2, "Oro al alza"),
    "gold up":             ("positivo", 1, "Oro sube"),
    "oro sube":            ("positivo", 2, "Oro al alza"),
    "oro rebota":          ("positivo", 2, "Rebote del oro"),
    "record high gold":    ("positivo", 3, "Oro en maximos historicos"),
    "oro maximo":          ("positivo", 2, "Oro en maximos"),

    # === NEGATIVO PARA EL ORO (presion a la baja) ===
    # --- Tasas / Fed hawkish ---
    "sube tasas":          ("negativo", 3, "Fed hawkish: tasas mas altas presionan oro"),
    "subir tasas":         ("negativo", 3, "Fed hawkish: tasas mas altas presionan oro"),
    "alza de tasas":       ("negativo", 3, "Fed hawkish: tasas mas altas presionan oro"),
    "rate hike":           ("negativo", 3, "Fed hawkish: rate hike presiona oro"),
    "rate hikes":          ("negativo", 3, "Fed hawkish: rate hikes presionan oro"),
    "higher rates":        ("negativo", 2, "Expectativa de tasas altas presiona oro"),
    "hawkish":             ("negativo", 2, "Tono hawkish de la Fed presiona oro"),
    "fed hawkish":         ("negativo", 3, "Fed hawkish: presion negativa para oro"),

    # --- Inflacion baja ---
    "inflacion cae":       ("negativo", 3, "Inflacion baja: menos demanda de cobertura"),
    "inflacion baja":      ("negativo", 3, "Inflacion baja: menos demanda de cobertura"),
    "inflation falls":     ("negativo", 3, "Inflacion baja: menos demanda de cobertura"),
    "inflation drops":     ("negativo", 3, "Inflacion baja: presion negativa para oro"),
    "inflation cools":     ("negativo", 2, "Inflacion se enfria: presion negativa para oro"),
    "cooling inflation":   ("negativo", 2, "Inflacion enfriando: presion negativa para oro"),
    "cpi cae":             ("negativo", 2, "CPI baja: presion negativa para oro"),
    "cpi falls":           ("negativo", 2, "CPI baja: presion negativa para oro"),
    "disinflation":        ("negativo", 2, "Desinflacion: presion negativa para oro"),

    # --- Dolar fuerte ---
    "dolar fuerte":        ("negativo", 2, "Dolar fortalecido presiona oro"),
    "dolar sube":          ("negativo", 2, "Dolar fortalecido presiona oro"),
    "fortaleza del dolar": ("negativo", 2, "Fortaleza del dolar presiona oro"),
    "dxy sube":            ("negativo", 2, "DXY fuerte presiona oro"),
    "dollar strength":     ("negativo", 2, "Dolar fortalecido presiona oro"),
    "stronger dollar":     ("negativo", 2, "Dolar fortalecido presiona oro"),
    "strong dollar":       ("negativo", 2, "Dolar fuerte: presion negativa para oro"),
    "dollar rallies":      ("negativo", 2, "Dolar rallea: presion negativa para oro"),
    "dollar surges":       ("negativo", 2, "Dolar salta: presion negativa para oro"),
    "dollar climbs":       ("negativo", 2, "Dolar al alza: presion negativa para oro"),

    # --- Bancos centrales vendiendo ---
    "central banks sell":  ("negativo", 3, "Bancos centrales venden oro: oferta institucional"),
    "vender oro":          ("negativo", 2, "Liquidacion institucional de oro"),
    "selling gold":        ("negativo", 2, "Venta de oro: presion negativa"),

    # --- Resolucion / risk-on ---
    "alto el fuego":       ("negativo", 2, "Cese de hostilidades: risk-on, oro cae"),
    "ceasefire":           ("negativo", 2, "Ceasefire: risk-on, oro pierde refugio"),
    "peace deal":          ("negativo", 2, "Acuerdo de paz: risk-on, oro cae"),
    "risk-on":             ("negativo", 2, "Risk-on de mercado: oro pierde demanda"),
    "risk on":             ("negativo", 2, "Risk-on de mercado: oro pierde demanda"),
    "stocks rally":        ("negativo", 1, "Bolsa fuerte: leve presion negativa para oro"),
    "stocks surge":        ("negativo", 1, "Bolsa salta: leve presion negativa para oro"),
    "record high stocks":  ("negativo", 1, "Maximos en bolsa: presion sobre oro"),
    "sp 500 record":       ("negativo", 1, "S&P en maximos: presion sobre oro"),

    # --- Yields reales altos ---
    "yields rise":         ("negativo", 2, "Yields reales suben: presion para oro"),
    "yields jump":         ("negativo", 2, "Yields reales saltan: presion para oro"),
    "real yields jump":    ("negativo", 2, "Yields reales suben: presion para oro"),
    "treasury yields rise":("negativo", 2, "Treasury yields suben: presion para oro"),
    "10-year yield":       ("negativo", 1, "Treasury 10Y al alza: presion para oro"),

    # --- Oro baja directo ---
    "gold falls":          ("negativo", 2, "Oro a la baja"),
    "gold drops":          ("negativo", 2, "Oro cae"),
    "gold tumbles":        ("negativo", 3, "Oro cae fuerte"),
    "gold slips":          ("negativo", 1, "Oro baja levemente"),
    "gold declines":       ("negativo", 2, "Oro a la baja"),
    "gold sinks":          ("negativo", 2, "Oro cae"),
    "oro cae":             ("negativo", 2, "Oro a la baja"),
    "oro baja":            ("negativo", 2, "Oro a la baja"),
    "oro retrocede":       ("negativo", 2, "Oro retrocede"),
}


def analizar_impacto(titulo, categoria):
    """
    Analiza el titulo. Mejoras v2:
    - Word boundaries para keywords de 1 palabra (evita matches "ban" en "banco")
    - Razon del factor de mayor peso dominante en la direccion final
    """
    import re as _re
    titulo_norm = normalizar(titulo)

    score_positivo = 0
    score_negativo = 0
    matches_pos = []
    matches_neg = []

    for kw, (direccion, peso, razon) in IMPACTO_KEYWORDS.items():
        kw_norm = normalizar(kw)
        # Si la keyword tiene >1 palabra: substring match. Si es 1 palabra: word boundary
        if " " in kw_norm:
            matched = kw_norm in titulo_norm
        else:
            matched = bool(_re.search(r"\b" + _re.escape(kw_norm) + r"\b", titulo_norm))
        if matched:
            if direccion == "positivo":
                score_positivo += peso
                matches_pos.append((peso, razon))
            else:
                score_negativo += peso
                matches_neg.append((peso, razon))

    diferencia = score_positivo - score_negativo
    if abs(diferencia) < 1:
        direccion = "neutral"
        intensidad = 0
        razonamiento = "Sin senal direccional clara en el titular"
    elif diferencia > 0:
        direccion = "positivo"
        intensidad = min(3, max(1, abs(diferencia) // 2 + 1))
        razonamiento = max(matches_pos, key=lambda x: x[0])[1] if matches_pos else "Sesgo positivo para oro"
    else:
        direccion = "negativo"
        intensidad = min(3, max(1, abs(diferencia) // 2 + 1))
        razonamiento = max(matches_neg, key=lambda x: x[0])[1] if matches_neg else "Sesgo negativo para oro"

    # Mapear a emoji
    emoji_map = {
        ("positivo", 3): "🟢🟢",
        ("positivo", 2): "🟢",
        ("positivo", 1): "🟢",
        ("negativo", 3): "🔴🔴",
        ("negativo", 2): "🔴",
        ("negativo", 1): "🔴",
        ("neutral",  0): "⚪",
    }
    emoji = emoji_map.get((direccion, intensidad), "⚪")

    # Label de intensidad
    intensidad_label = {0: "neutral", 1: "leve", 2: "moderado", 3: "fuerte"}[intensidad]

    return {
        "direccion": direccion,
        "intensidad": intensidad,
        "intensidad_label": intensidad_label,
        "emoji": emoji,
        "razonamiento": razonamiento,
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
    """Fetch + filter + dedupe para una categoria."""
    log.info(f"\n--- Categoria: {categoria} ---")
    items_brutos = []
    for kw in keywords:
        url = build_query_url(kw)
        xml = fetch_rss(url)
        items = parse_rss(xml)
        items_brutos.extend(items)
        log.info(f"  '{kw}': {len(items)} resultados")

    # Filtrar por ventana de tiempo
    recientes = [it for it in items_brutos if is_reciente(it, cutoff)]
    log.info(f"  Recientes (<={VENTANA_DIAS}d): {len(recientes)}")

    # Deduplicar por titulo normalizado
    vistos = set()
    unicos = []
    for it in recientes:
        clave = normalizar(clean_title(it["title"]))
        if clave and clave not in vistos:
            vistos.add(clave)
            titulo_limpio = clean_title(it["title"])
            impacto = analizar_impacto(titulo_limpio, categoria)
            unicos.append({
                "title": titulo_limpio,
                "link": it["link"],
                "source": it["source"],
                "pubdate": it["pubdate"],
                "pubdate_iso": parse_pubdate(it["pubdate"]).isoformat() if parse_pubdate(it["pubdate"]) else "",
                "impacto": impacto,
            })

    # Ordenar por fecha desc y top N
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
    code, out, _ = ejecutar_git(["status", "--porcelain", "noticias_gld.json"])
    if not out.strip():
        log.info("Sin cambios en noticias_gld.json")
        return
    ejecutar_git(["pull", "--rebase", "--autostash"])
    ejecutar_git(["add", "noticias_gld.json"])
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    ejecutar_git(["commit", "-m", f"chore: update noticias GLD {ts} [skip ci]"])
    code, _, err = ejecutar_git(["push"])
    if code != 0:
        log.warning(f"git push fallo: {err}. Reintentando con pull...")
        ejecutar_git(["pull", "--rebase", "--autostash"])
        ejecutar_git(["push"])
    log.info("git push OK")


def main():
    log.info("=" * 60)
    log.info("=== Update Noticias GLD ===")
    log.info(f"Hora: {datetime.now().isoformat()}")

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
