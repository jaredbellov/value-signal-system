"""
NEWS FETCHER - Value Signal
============================
Obtiene noticias de las empresas del watchlist desde Google News RSS,
filtrando por sitio (Diario Financiero, La Tercera, El Mercurio).

Salida: noticias_watchlist.json (consumido por el dashboard)

Frecuencia recomendada: cada 2-3 horas via tarea programada.
"""
import html as _html_lib
import json
import logging
import re
import subprocess
import urllib.parse
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "news.log"


def ejecutar_git(args, cwd=None):
    """Ejecuta un comando git y devuelve (exit_code, stdout, stderr)."""
    cwd = cwd or SCRIPT_DIR
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except Exception as e:
        return -2, "", str(e)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ============================================================
# CONFIGURACION
# ============================================================

# Diccionario: ticker -> lista de keywords a buscar
# Cada keyword se buscara con comillas (frase exacta) en Google News
WATCHLIST_KEYWORDS = {
    "HABITAT":    ["AFP Habitat"],
    "ZOFRI":      ["Zofri"],
    "PEHUENCHE":  ["Central Pehuenche", "Pehuenche electrica"],
    "TRICAHUE":   ["fondo Tricahue", "Tricahue Capital"],
    "COLBUN":     ["Colbun"],
    "ENELGXCH":   ["Enel Generacion Chile", "Enel Chile"],
    "LIPIGAS":    ["Empresas Lipigas", "Lipigas"],
    "NTGCLGAS":   ["Gasco"],
    "SOQUICOM":   ["SQM", "Soquimich"],
    "QUINENCO":   ["Quinenco"],
    "CENCOMALLS": ["Cencosud Shopping", "Cencomalls"],
}

# Mapeo ticker -> keywords de razon social en mayusculas (CMF usa mayusculas)
# Usamos substring matching con normalizacion de tildes
WATCHLIST_HECHOS_ESENCIALES = {
    "HABITAT":    ["afp habitat"],
    "ZOFRI":      ["zona franca de iquique", "zofri"],
    "PEHUENCHE":  ["pehuenche"],
    "TRICAHUE":   ["fondo de inversion tricahue", "tricahue capital"],
    "COLBUN":     ["colbun"],
    "ENELGXCH":   ["enel generacion", "enel chile"],
    "LIPIGAS":    ["empresas lipigas", "lipigas"],
    "NTGCLGAS":   ["empresas gasco", "gasco s.a."],
    "SOQUICOM":   ["sociedad quimica y minera"],
    "QUINENCO":   ["quinenco"],
    "CENCOMALLS": ["cencosud shopping"],
}

# URL del agregador de hechos esenciales (ultimos 7 dias)
VISFIN_HECHOS_URL = "https://visfin.cl/hechos-esenciales"

# Sitios objetivo (3 medios principales)
SITIOS = [
    {"name": "Diario Financiero", "domain": "df.cl"},
    {"name": "La Tercera",        "domain": "latercera.com"},
    {"name": "El Mercurio",       "domain": "elmercurio.com"},
]

# Solo noticias de los ultimos N dias
DIAS_VENTANA = 7

# Google News RSS endpoint
BASE_URL = "https://news.google.com/rss/search"

OUTPUT_FILE = SCRIPT_DIR / "noticias_watchlist.json"

# ============================================================
# FETCH
# ============================================================

def build_query_url(keyword, site):
    """Construye URL del feed RSS de Google News para keyword + site."""
    query = f'"{keyword}" site:{site}'
    params = {
        "q": query,
        "hl": "es-CL",
        "gl": "CL",
        "ceid": "CL:es-419",
    }
    return f"{BASE_URL}?{urllib.parse.urlencode(params)}"


def fetch_rss(url, timeout=20):
    """Descarga RSS y devuelve string XML."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Value Signal News Fetcher)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_rss(xml_text):
    """Parsea XML RSS y devuelve lista de items."""
    items = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.iter("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            pubdate_el = item.find("pubDate")
            source_el = item.find("source")

            title = (title_el.text or "").strip() if title_el is not None else ""
            link = (link_el.text or "").strip() if link_el is not None else ""
            pubdate = (pubdate_el.text or "").strip() if pubdate_el is not None else ""
            source = (source_el.text or "").strip() if source_el is not None else ""

            if title and link:
                items.append({
                    "title": title,
                    "link": link,
                    "pubdate": pubdate,
                    "source": source,
                })
    except ET.ParseError as e:
        log.warning(f"  Error parseando XML: {e}")
    return items


def parse_pubdate(pubdate_str):
    """Convierte 'Wed, 27 May 2026 19:49:00 GMT' -> datetime aware."""
    try:
        return datetime.strptime(pubdate_str, "%a, %d %b %Y %H:%M:%S %Z").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return None


def is_reciente(item, cutoff):
    """True si la noticia es mas reciente que cutoff."""
    dt = parse_pubdate(item.get("pubdate"))
    if dt is None:
        return False
    return dt >= cutoff


def clean_title(title):
    """Limpia el sufijo ' - Nombre Medio' que agrega Google al final."""
    # Patrones: " - Diario Financiero", " - La Tercera", etc.
    title = re.sub(r"\s+-\s+(Diario Financiero|La Tercera|El Mercurio|Pulso).*$", "", title)
    return title.strip()


def normalizar(s):
    """Quita tildes y baja a minusculas para comparar."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def es_titulo_basura(title):
    """Filtra titulos genericos como 'Ultimas noticias', 'Untitled', etc."""
    basura = [
        "ultimas noticias",
        "ultima hora",
        "lo que debes saber",
        "untitled",
        "noticias escritas por",
        "noticias de ",
        "noticias sobre ",
        ". noticias",
        "talento local",
        "cuales son las organizaciones",
        "abstracto - diario",
        "abstracto - el mercurio",
    ]
    t = normalizar(title)
    if any(b in t for b in basura):
        return True
    # Filtrar titulos que son solo numeros (IDs internos sueltos)
    titulo_strip = title.strip()
    if titulo_strip.isdigit():
        return True
    # Filtrar titulos muy cortos (menos de 15 caracteres) que probablemente
    # son fragmentos rotos (IDs con sufijo, etc.)
    if len(titulo_strip) < 15:
        return True
    return False


# ============================================================
# HECHOS ESENCIALES (scraping visfin.cl que agrega CMF)
# ============================================================

def fetch_hechos_esenciales():
    """
    Descarga la tabla de hechos esenciales de visfin.cl (ultimos 7 dias)
    y devuelve una lista de dicts: fecha, empresa, materia, link.
    """
    log.info("Descargando hechos esenciales de visfin.cl...")
    try:
        html = fetch_rss(VISFIN_HECHOS_URL, timeout=30)
    except Exception as e:
        log.error(f"  Error descargando visfin: {e}")
        return []

    # La tabla esta en formato HTML simple. Buscamos filas <tr> con <td>
    hechos = []
    # Patron: capturar filas que tengan 4 columnas (fecha, empresa, materia, link)
    # Usamos un regex tolerante
    row_pattern = re.compile(
        r'<tr[^>]*>\s*'
        r'<td[^>]*>(.*?)</td>\s*'      # fecha
        r'<td[^>]*>(.*?)</td>\s*'      # empresa
        r'<td[^>]*>(.*?)</td>\s*'      # materia
        r'<td[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>\s*</td>',
        re.DOTALL | re.IGNORECASE,
    )

    for match in row_pattern.finditer(html):
        fecha_str = match.group(1).strip()
        empresa = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        materia = re.sub(r"<[^>]+>", "", match.group(3)).strip()
        link = match.group(4).strip()
        doc_id = match.group(5).strip()

        # Filtros minimos
        if not empresa or not link or "cmfchile.cl" not in link:
            continue

        hechos.append({
            "fecha": fecha_str,
            "empresa": _html_lib.unescape(empresa),
            "materia": _html_lib.unescape(materia),
            "link": _html_lib.unescape(link),
            "doc_id": _html_lib.unescape(doc_id),
        })

    log.info(f"Hechos esenciales descargados: {len(hechos)}")
    return hechos


def filtrar_hechos_por_ticker(hechos, ticker, keywords):
    """Filtra los hechos que matchean alguna keyword del ticker."""
    resultado = []
    for h in hechos:
        empresa_norm = normalizar(h["empresa"])
        for kw in keywords:
            if kw in empresa_norm:
                resultado.append({
                    "title": f"{h['empresa']}: {h['materia']}",
                    "link": h["link"],
                    "pubdate": h["fecha"],
                    "source": "CMF (Hecho Esencial)",
                    "matched_keyword": kw,
                    "es_hecho_esencial": True,
                    "doc_id": h.get("doc_id", ""),
                    "empresa_cmf": h["empresa"],
                    "materia_cmf": h["materia"],
                })
                break  # un hecho solo cuenta una vez por ticker
    return resultado


# ============================================================
# MAIN
# ============================================================

def main():
    log.info("=" * 60)
    log.info("=== News Fetcher - Value Signal ===")
    log.info(f"Hora: {datetime.now().isoformat()}")
    log.info(f"Ventana: ultimos {DIAS_VENTANA} dias")
    log.info(f"Medios: {[s['name'] for s in SITIOS]}")
    log.info(f"Watchlist: {list(WATCHLIST_KEYWORDS.keys())}")

    # [PLAN-B] git neutralizado: publicar.py se encarga del push

    # Cargar IDs estables del JSON anterior para detectar cambios reales
    # - Hechos esenciales: doc_id (estable, no incluye token t=)
    # - Noticias: link (Google News no cambia)
    ids_anteriores_estables = set()
    if OUTPUT_FILE.exists():
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                data_anterior = json.load(f)
            for noticias in data_anterior.get("noticias_por_ticker", {}).values():
                for n in noticias:
                    if n.get("es_hecho_esencial") and n.get("doc_id"):
                        ids_anteriores_estables.add(f"cmf:{n['doc_id']}")
                    else:
                        ids_anteriores_estables.add(f"news:{n.get('link', '')}")
            log.info(f"IDs anteriores cargados: {len(ids_anteriores_estables)}")
        except Exception as e:
            log.warning(f"No se pudo leer JSON anterior: {e}")

    cutoff = datetime.now(timezone.utc) - timedelta(days=DIAS_VENTANA)
    log.info(f"Cutoff fecha: {cutoff.isoformat()}")

    resultado = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ventana_dias": DIAS_VENTANA,
        "medios": [s["name"] for s in SITIOS],
        "noticias_por_ticker": {},
        "stats": {
            "total_queries": 0,
            "queries_exitosas": 0,
            "queries_falladas": 0,
            "total_noticias_brutas": 0,
            "total_noticias_filtradas": 0,
        },
    }

    seen_links = set()  # para dedupe global

    # Descargar hechos esenciales UNA VEZ (es global, no por ticker)
    log.info("--- Cargando Hechos Esenciales (visfin) ---")
    hechos_globales = fetch_hechos_esenciales()

    # Robustez: si visfin devolvio 0 (blip transitorio), reusar los anteriores
    if len(hechos_globales) == 0 and OUTPUT_FILE.exists():
        log.warning("Visfin devolvio 0 hechos. Reusando hechos anteriores del JSON.")
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                data_prev = json.load(f)
            hechos_recuperados = []
            for noticias in data_prev.get("noticias_por_ticker", {}).values():
                for n in noticias:
                    if n.get("es_hecho_esencial") and n.get("doc_id"):
                        # Reconstruir formato de hecho_global
                        hechos_recuperados.append({
                            "fecha": n.get("pubdate", ""),
                            "empresa": n.get("empresa_cmf", n.get("title", "").split(":")[0]),
                            "materia": n.get("materia_cmf", ""),
                            "link": n.get("link", ""),
                            "doc_id": n.get("doc_id", ""),
                        })
            # Dedupe por doc_id
            seen_docs = set()
            hechos_globales = []
            for h in hechos_recuperados:
                if h["doc_id"] and h["doc_id"] not in seen_docs:
                    seen_docs.add(h["doc_id"])
                    hechos_globales.append(h)
            log.info(f"Hechos esenciales recuperados del JSON anterior: {len(hechos_globales)}")
        except Exception as e:
            log.error(f"No se pudieron recuperar hechos anteriores: {e}")

    resultado["stats"]["total_hechos_esenciales_brutos"] = len(hechos_globales)

    for ticker, keywords in WATCHLIST_KEYWORDS.items():
        log.info(f"--- {ticker} ---")
        noticias_ticker = []

        # Filtrar hechos esenciales relevantes para este ticker
        kw_hechos = WATCHLIST_HECHOS_ESENCIALES.get(ticker, [])
        hechos_ticker = filtrar_hechos_por_ticker(hechos_globales, ticker, kw_hechos)
        for h in hechos_ticker:
            if h["link"] not in seen_links:
                seen_links.add(h["link"])
                noticias_ticker.append(h)
        if hechos_ticker:
            log.info(f"  Hechos Esenciales: {len(hechos_ticker)}")

        for keyword in keywords:
            for sitio in SITIOS:
                resultado["stats"]["total_queries"] += 1
                url = build_query_url(keyword, sitio["domain"])
                try:
                    xml = fetch_rss(url)
                    items = parse_rss(xml)
                    resultado["stats"]["queries_exitosas"] += 1
                    resultado["stats"]["total_noticias_brutas"] += len(items)
                    log.info(f"  '{keyword}' @ {sitio['domain']}: {len(items)} items brutos")

                    for item in items:
                        # Filtro 1: reciente
                        if not is_reciente(item, cutoff):
                            continue
                        # Filtro 2: dedupe por link
                        if item["link"] in seen_links:
                            continue
                        # Filtro 3: titulo no basura
                        clean = clean_title(item["title"])
                        if es_titulo_basura(clean):
                            continue

                        seen_links.add(item["link"])
                        noticias_ticker.append({
                            "title": clean,
                            "link": item["link"],
                            "pubdate": item["pubdate"],
                            "source": item["source"] or sitio["name"],
                            "matched_keyword": keyword,
                        })
                except Exception as e:
                    resultado["stats"]["queries_falladas"] += 1
                    log.warning(f"  '{keyword}' @ {sitio['domain']}: ERROR {e}")

        # Ordenar por fecha descendente
        noticias_ticker.sort(
            key=lambda x: parse_pubdate(x["pubdate"]) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        resultado["noticias_por_ticker"][ticker] = noticias_ticker
        resultado["stats"]["total_noticias_filtradas"] += len(noticias_ticker)
        log.info(f"  {ticker}: {len(noticias_ticker)} noticias relevantes")

    # Guardar
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

    log.info("-" * 60)
    log.info(f"Guardado: {OUTPUT_FILE}")
    log.info(f"Stats: {resultado['stats']}")

    # Detectar si hubo cambios usando identificador ESTABLE:
    # - Hechos esenciales: doc_id (el numero CMF, no cambia entre fetches)
    # - Noticias: link (estable en Google News)
    ids_actuales = set()
    for noticias in resultado["noticias_por_ticker"].values():
        for n in noticias:
            if n.get("es_hecho_esencial") and n.get("doc_id"):
                ids_actuales.add(f"cmf:{n['doc_id']}")
            else:
                ids_actuales.add(f"news:{n['link']}")

    # ids_anteriores_estables ya fue cargado al inicio del main
    nuevos = ids_actuales - ids_anteriores_estables
    quitados = ids_anteriores_estables - ids_actuales
    hubo_cambios = len(nuevos) > 0 or len(quitados) > 0

    log.info(f"Cambios: {len(nuevos)} nuevos, {len(quitados)} quitados")

    if hubo_cambios:
        log.info("--- Hay cambios: committing ---")
        code, out, err = ejecutar_git(["add", "noticias_watchlist.json"])
        if code != 0:
            log.error(f"git add fallo: {err}")
            return

        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        msg = f"chore: update noticias watchlist {ts} (+{len(nuevos)}/-{len(quitados)}) [skip ci]"
        code, out, err = ejecutar_git(["commit", "-m", msg])
        if code != 0:
            log.warning(f"git commit dijo: {out or err}")
        else:
            log.info("git commit OK")

        code, out, err = ejecutar_git(["push"])
        if code != 0:
            log.error(f"git push fallo: {err}")
            return
        log.info("git push OK")
    else:
        log.info("Sin cambios en links, no commiteo")

    log.info(f"=== FIN ({datetime.now().strftime('%H:%M:%S')}) ===")


if __name__ == "__main__":
    main()
