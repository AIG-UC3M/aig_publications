import json
import os
import re
import time
import html
import unicodedata
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# CONFIGURACIÓN
# ============================================================

INPUT_FILE = "researchers1.json"

OUTPUT_JSON = "publications.json"
OUTPUT_ALL = "publications_all_orcid.json"
OUTPUT_EXCLUDED = "publications_excluded.json"
OUTPUT_HTML = "publications.html"
OUTPUT_BIB = "publications.bib"

API_BASE = "https://pub.orcid.org/v3.0"

ROWS_PER_PAGE = 100
MAX_PAGES = 1000
REQUEST_TIMEOUT = 30


# ============================================================
# TOKEN ORCID
# ============================================================

TOKEN = os.getenv("ORCID_ACCESS_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "No se ha encontrado ORCID_ACCESS_TOKEN.\n"
        "Configura primero la variable de entorno con tu token ORCID."
    )


HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.orcid+json",
}


# ============================================================
# SESIÓN HTTP
# ============================================================

session = requests.Session()

retry = Retry(
    total=5,
    connect=5,
    read=5,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    respect_retry_after_header=True,
)

adapter = HTTPAdapter(
    max_retries=retry,
    pool_connections=20,
    pool_maxsize=20,
)

session.mount("https://", adapter)
session.mount("http://", adapter)


# ============================================================
# UTILIDADES DE LIMPIEZA Y NORMALIZACIÓN
# ============================================================

def safe_get(url, params=None):
    """GET seguro con manejo de errores."""
    try:
        response = session.get(
            url,
            headers=HEADERS,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()
        return response.json()

    except requests.RequestException as e:
        print(f"      ERROR HTTP: {e}")
        return None

    except ValueError as e:
        print(f"      ERROR JSON: {e}")
        return None


def clean_text(value):
    """Convierte cualquier valor a texto limpio."""
    if value is None:
        return ""
    return str(value).strip()


def normalize_doi(doi):
    """
    Limpia y estandariza cualquier DOI a una forma canónica pura.
    Ejemplo: 'https://doi.org/10.1109/ICIP.2018.8451566' -> '10.1109/icip.2018.8451566'
    """
    if not doi:
        return ""
    doi = clean_text(doi).lower()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.IGNORECASE)
    return doi.strip()


def normalize_title(title):
    """
    Normalización estricta de título eliminando acentos,
    puntuaciones y espacios irrelevantes.
    """
    if not title:
        return ""

    title = clean_text(title)
    # Descomponer caracteres diacríticos (acentos) y eliminarlos
    title = unicodedata.normalize("NFKD", title)
    title = "".join(c for c in title if not unicodedata.combining(c))
    title = title.lower()

    # Reemplazar guiones y barras por espacios
    title = re.sub(r"[-_/\\]", " ", title)
    # Conservar solo caracteres alfanuméricos y espacios
    title = re.sub(r"[^\w\s]", "", title)
    # Colapsar espacios múltiples
    title = re.sub(r"\s+", " ", title)

    return title.strip()


# ============================================================
# NORMALIZACIÓN DE AUTORES
# ============================================================

def normalize_author_key(name):
    """Crea una clave de comparación ignorando acentos y variaciones."""
    name = clean_text(name).lower()
    name = unicodedata.normalize("NFD", name)
    name = "".join(
        char for char in name
        if unicodedata.category(char) != "Mn"
    )

    name = name.replace("–", "-").replace("—", "-").replace("‑", "-")
    name = re.sub(r"\s*-\s*", "-", name)
    name = re.sub(r"\s+", " ", name)

    return name.strip()


AUTHOR_NORMALIZATION = {
    normalize_author_key("F. Díaz-de-María"): "F. Díaz-de-María",
    normalize_author_key("Fernando Díaz de María"): "F. Díaz-de-María",
    normalize_author_key("Fernando Díaz-de-María"): "F. Díaz-de-María",
    normalize_author_key("Fernando Diaz-de-Maria"): "F. Díaz-de-María",
    normalize_author_key("Fernando Diaz De Maria"): "F. Díaz-de-María",
    normalize_author_key("ASCENSIÓN GALLARDO-ANTOLÍN"): "A. Gallardo-Antolín",
    normalize_author_key("Ascensión Gallardo-Antolín"): "A. Gallardo-Antolín",
    normalize_author_key("Carmen Peláez-Moreno"): "C. Peláez-Moreno",
    normalize_author_key("Carmen Pelaez-Moreno"): "C. Peláez-Moreno",
    normalize_author_key("Carmen Peláez"): "C. Peláez-Moreno",
    normalize_author_key("Carmen Pelaez"): "C. Peláez-Moreno",
}


def normalize_author_name(name):
    key = normalize_author_key(name)
    return AUTHOR_NORMALIZATION.get(key, clean_text(name))


# ============================================================
# FECHAS
# ============================================================

def get_date(date_obj):
    """Extrae una fecha ORCID tolerando campos incompletos."""
    if not isinstance(date_obj, dict):
        return ""

    year_obj = date_obj.get("year") or {}
    month_obj = date_obj.get("month") or {}
    day_obj = date_obj.get("day") or {}

    year = year_obj.get("value") if isinstance(year_obj, dict) else None
    month = month_obj.get("value") if isinstance(month_obj, dict) else None
    day = day_obj.get("value") if isinstance(day_obj, dict) else None

    if not year:
        return ""

    year = str(year)
    month = str(month).zfill(2) if month else "01"
    day = str(day).zfill(2) if day else "01"

    return f"{year}-{month}-{day}"


def get_year(date_obj):
    """Extrae solamente el año."""
    if not isinstance(date_obj, dict):
        return ""
    year_obj = date_obj.get("year") or {}
    if not isinstance(year_obj, dict):
        return ""
    return clean_text(year_obj.get("value"))


# ============================================================
# ORCID - WORK GROUPS Y EXTRACCIÓN PREFERIDA
# ============================================================

def get_orcid_work_groups(orcid):
    """Obtiene todas las páginas de /works."""
    all_groups = []
    start = 0
    seen_group_keys = set()

    for page_number in range(MAX_PAGES):
        params = {
            "start": start,
            "rows": ROWS_PER_PAGE,
        }

        print(f"      Página ORCID {start}-{start + ROWS_PER_PAGE}")

        data = safe_get(
            f"{API_BASE}/{orcid}/works",
            params=params,
        )

        if not data:
            break

        groups = data.get("group") or []

        if not groups:
            print("      No hay más grupos.")
            break

        print(f"      Grupos recibidos: {len(groups)}")

        new_groups = []

        for group in groups:
            if not isinstance(group, dict):
                continue

            summaries = group.get("work-summary") or []
            if not isinstance(summaries, list):
                summaries = [summaries]

            put_codes = []
            for summary in summaries:
                if not isinstance(summary, dict):
                    continue
                put_code = summary.get("put-code")
                if put_code is not None:
                    put_codes.append(str(put_code))

            group_key = tuple(sorted(put_codes))

            if group_key and group_key in seen_group_keys:
                continue

            if group_key:
                seen_group_keys.add(group_key)

            new_groups.append(group)

        if not new_groups:
            print("      AVISO: la página no contiene grupos nuevos.")
            break

        all_groups.extend(new_groups)

        if len(groups) < ROWS_PER_PAGE:
            break

        start += len(groups)

    print(f"      Grupos obtenidos: {len(all_groups)}")
    return all_groups


def extract_preferred_summaries(groups):
    """
    CORRECCIÓN CLAVE: Extrae SOLAMENTE el primer work-summary (el preferido)
    de cada grupo de ORCID. Esto evita descargar duplicados del mismo autor.
    """
    preferred_summaries = []

    for group in groups:
        if not isinstance(group, dict):
            continue

        group_summaries = group.get("work-summary") or []
        if not isinstance(group_summaries, list):
            group_summaries = [group_summaries]

        if group_summaries and isinstance(group_summaries[0], dict):
            preferred_summaries.append(group_summaries[0])

    return preferred_summaries


def get_orcid_work(orcid, put_code):
    """Obtiene un work completo."""
    return safe_get(f"{API_BASE}/{orcid}/work/{put_code}")


# ============================================================
# IDENTIFICADORES EXTERNOS Y DATOS DEL WORK
# ============================================================

def extract_external_ids(work):
    """Extrae DOI, PMID, PMCID normalizados."""
    result = {
        "doi": "",
        "pmid": "",
        "pmcid": "",
        "other_ids": [],
    }

    if not isinstance(work, dict):
        return result

    external_ids = work.get("external-ids") or {}
    external_id_list = external_ids.get("external-id") or []

    if not isinstance(external_id_list, list):
        external_id_list = [external_id_list]

    for item in external_id_list:
        if not isinstance(item, dict):
            continue

        id_type = clean_text(item.get("external-id-type")).lower()
        value = clean_text(item.get("external-id-value") or item.get("value"))

        if not value:
            continue

        if id_type == "doi":
            result["doi"] = normalize_doi(value)

        elif id_type in ("pmid", "pubmed"):
            result["pmid"] = value.lower().strip()

        elif id_type == "pmcid":
            result["pmcid"] = value.lower().strip()

        else:
            result["other_ids"].append({"type": id_type, "value": value})

    return result


def extract_title(work):
    if not isinstance(work, dict):
        return ""
    title_obj = work.get("title") or {}
    if not isinstance(title_obj, dict):
        return clean_text(title_obj)
    title = title_obj.get("title") or {}
    if isinstance(title, dict):
        return clean_text(title.get("value") or title.get("content"))
    return clean_text(title)


def extract_journal(work):
    if not isinstance(work, dict):
        return ""
    journal = work.get("journal-title") or {}
    if isinstance(journal, dict):
        return clean_text(journal.get("value") or journal.get("content"))
    return clean_text(journal)


def extract_url(work):
    if not isinstance(work, dict):
        return ""
    url_obj = work.get("url")
    if isinstance(url_obj, dict):
        return clean_text(url_obj.get("value") or url_obj.get("content"))
    return clean_text(url_obj)


def extract_authors(work):
    authors = []
    if not isinstance(work, dict):
        return authors

    contributors = work.get("contributors") or {}
    contributor_list = contributors.get("contributor") or []

    if not isinstance(contributor_list, list):
        contributor_list = [contributor_list]

    for contributor in contributor_list:
        if not isinstance(contributor, dict):
            continue

        credit_name = contributor.get("credit-name") or {}
        if isinstance(credit_name, dict):
            name = clean_text(credit_name.get("value") or credit_name.get("content"))
        else:
            name = clean_text(credit_name)

        if name:
            authors.append(normalize_author_name(name))

    return authors


# ============================================================
# CONVERTIR WORK A PUBLICACIÓN
# ============================================================

def work_to_publication(work, summary=None):
    if not isinstance(work, dict):
        work = {}
    if not isinstance(summary, dict):
        summary = {}

    title = extract_title(work) or extract_title(summary)
    journal = extract_journal(work) or extract_journal(summary)

    work_type = (
        clean_text(work.get("type"))
        or clean_text(summary.get("type"))
    )

    publication_date = (
        work.get("publication-date")
        or summary.get("publication-date")
        or {}
    )

    date = get_date(publication_date)
    year = get_year(publication_date)

    if not year:
        year = date[:4] if date else ""

    identifiers = extract_external_ids(work)

    if not identifiers["doi"]:
        summary_ids = extract_external_ids(summary)
        for key in ("doi", "pmid", "pmcid"):
            if summary_ids[key] and not identifiers[key]:
                identifiers[key] = summary_ids[key]

    url = extract_url(work) or extract_url(summary)
    authors = extract_authors(work) or extract_authors(summary)
    put_code = work.get("put-code") or summary.get("put-code")

    source = work.get("source") or summary.get("source") or {}
    source_name = ""
    if isinstance(source, dict):
        source_name = clean_text(source.get("source-name"))

    return {
        "put_code": str(put_code) if put_code is not None else "",
        "title": title,
        "type": work_type,
        "journal": journal,
        "date": date,
        "year": year,
        "doi": identifiers["doi"],
        "pmid": identifiers["pmid"],
        "pmcid": identifiers["pmcid"],
        "url": url,
        "authors": authors,
        "source": source_name,
        "raw_type": work_type,
    }


# ============================================================
# CLASIFICACIÓN DE ARTÍCULOS
# ============================================================

def looks_like_article(pub):
    """Decide si una publicación debe entrar en el resultado de artículos."""
    if not isinstance(pub, dict):
        return False

    title = clean_text(pub.get("title"))
    if not title:
        return False

    work_type = clean_text(pub.get("type")).lower()
    journal = clean_text(pub.get("journal"))
    doi = clean_text(pub.get("doi"))

    # Ampliamos los tipos aceptados desde ORCID
    article_types = {
        "journal-article",
        "article",
        "journal article",
        "conference-paper",
        "conference-abstract",
        "publication-issue",
        "other",
    }

    if work_type in article_types:
        return True

    # Si tiene DOI o Revista/Journal asociada, considerarlo artículo independientemente del tipo
    if journal or doi:
        return True

    return False


# ============================================================
# DEDUPLICACIÓN GLOBAL
# ============================================================

def publication_key(pub):
    """
    CORRECCIÓN CLAVE: Genera una clave de deduplicación robusta.
    1. Si hay DOI, PMID o PMCID limpios -> Coincidencia exacta.
    2. Si no hay ID -> Compara (Título Normalizado + Año) SIN requerir que
       el nombre del journal coincida de forma estricta.
    """
    doi = normalize_doi(pub.get("doi"))
    if doi:
        return ("doi", doi)

    pmid = clean_text(pub.get("pmid")).lower()
    if pmid:
        return ("pmid", pmid)

    pmcid = clean_text(pub.get("pmcid")).lower()
    if pmcid:
        return ("pmcid", pmcid)

    # Clave de respaldo agresiva basada solo en Título Normalizado y Año
    title = normalize_title(pub.get("title"))
    year = clean_text(pub.get("year"))

    return ("fallback", title, year)


def deduplicate_publications(publications):
    """Elimina publicaciones duplicadas."""
    seen = set()
    result = []

    for pub in publications:
        key = publication_key(pub)

        if key in seen:
            continue

        seen.add(key)
        result.append(pub)

    return result


# ============================================================
# BIBTEX
# ============================================================

def bibtex_escape(text):
    if text is None:
        return ""
    text = str(text)
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "&": r"\&",
        "%": r"\%",
        "#": r"\#",
        "_": r"\_",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def make_bibtex_key(pub, index):
    authors = pub.get("authors") or []
    if authors:
        first_author = re.sub(r"[^A-Za-z0-9]", "", authors[0])
    else:
        first_author = "Author"

    year = pub.get("year") or "nd"
    return f"{first_author}{year}{index}"


def publication_to_bibtex(pub, index):
    key = make_bibtex_key(pub, index)
    authors = pub.get("authors") or []

    author_text = " and ".join(
        bibtex_escape(author) for author in authors
    )

    lines = [f"@article{{{key},"]

    if pub.get("title"):
        lines.append(f"  title = {{{bibtex_escape(pub['title'])}}},")

    if author_text:
        lines.append(f"  author = {{{author_text}}},")

    if pub.get("journal"):
        lines.append(f"  journal = {{{bibtex_escape(pub['journal'])}}},")

    if pub.get("year"):
        lines.append(f"  year = {{{bibtex_escape(pub['year'])}}},")

    if pub.get("doi"):
        lines.append(f"  doi = {{{bibtex_escape(pub['doi'])}}},")

    if pub.get("url"):
        lines.append(f"  url = {{{bibtex_escape(pub['url'])}}},")

    lines.append("}")
    return "\n".join(lines)


def save_bibtex(publications, filename):
    entries = [
        publication_to_bibtex(pub, index)
        for index, pub in enumerate(publications, start=1)
    ]
    with open(filename, "w", encoding="utf-8") as file:
        file.write("\n\n".join(entries))


# ============================================================
# HTML Y JSON
# ============================================================

def save_html(publications, filename):
    rows = []
    for pub in publications:
        title = html.escape(pub.get("title") or "")
        journal = html.escape(pub.get("journal") or "")
        year = html.escape(pub.get("year") or "")
        doi = pub.get("doi") or ""
        url = pub.get("url") or ""

        if doi:
            doi_url = "https://doi.org/" + quote(doi)
            doi_html = f'<a href="{html.escape(doi_url)}">{html.escape(doi)}</a>'
        else:
            doi_html = ""

        url_html = f'<a href="{html.escape(url)}">enlace</a>' if url else ""

        rows.append(
            f"<tr><td>{title}</td><td>{journal}</td><td>{year}</td>"
            f"<td>{doi_html}</td><td>{url_html}</td></tr>"
        )

    document = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Publicaciones</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 30px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 8px; vertical-align: top; }}
th {{ background: #eee; }}
</style>
</head>
<body>
<h1>Publicaciones</h1>
<table>
<thead>
<tr><th>Título</th><th>Revista</th><th>Año</th><th>DOI</th><th>URL</th></tr>
</thead>
<tbody>
{"".join(rows)}
</tbody>
</table>
</body>
</html>"""

    with open(filename, "w", encoding="utf-8") as file:
        file.write(document)


def save_json(data, filename):
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def load_researchers(filename):
    with open(filename, "r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, dict):
        for key in ("researchers", "investigadores", "people"):
            if key in data:
                data = data[key]
                break

    if not isinstance(data, list):
        raise ValueError("researchers1.json debe contener una lista de investigadores.")

    return data


def get_researcher_name(researcher):
    if not isinstance(researcher, dict):
        return "Investigador"
    for key in ("name", "nombre", "full_name", "fullname"):
        if researcher.get(key):
            return clean_text(researcher[key])
    return "Investigador"


def get_researcher_orcid(researcher):
    if not isinstance(researcher, dict):
        return ""
    for key in ("orcid", "ORCID", "orcid_id", "orcidId"):
        value = researcher.get(key)
        if value:
            value = clean_text(value)
            value = value.replace("https://orcid.org/", "").replace("http://orcid.org/", "")
            return value.strip()
    return ""


# ============================================================
# PROCESAR INVESTIGADOR
# ============================================================

def process_researcher(researcher, position, total):
    name = get_researcher_name(researcher)
    orcid = get_researcher_orcid(researcher)

    print()
    print(f"[{position}/{total}] {name}")
    print(f"      ORCID: {orcid}")

    if not orcid:
        print("      ERROR: investigador sin ORCID.")
        return []

    groups = get_orcid_work_groups(orcid)
    # Extraer únicamente el registro representativo por cada grupo de ORCID
    summaries = extract_preferred_summaries(groups)

    print(f"      Grupos de obras únicos: {len(summaries)}")

    publications = []
    total_summaries = len(summaries)

    for index, summary in enumerate(summaries, start=1):
        put_code = summary.get("put-code")

        if put_code is None:
            continue

        if index == 1 or index % 25 == 0 or index == total_summaries:
            print(f"      Obras procesadas: {index}/{total_summaries}")

        work = get_orcid_work(orcid, put_code)

        if work is None:
            work = summary

        try:
            pub = work_to_publication(work, summary)
            pub["researcher"] = name
            pub["orcid"] = orcid
            publications.append(pub)

        except Exception as error:
            print(f"      AVISO: error procesando put-code {put_code}: {error}")

    print(f"      Obras recuperadas: {len(publications)}")
    return publications


# ============================================================
# MAIN
# ============================================================

def main():
    start_time = time.time()

    researchers = load_researchers(INPUT_FILE)
    print(f"Investigadores encontrados: {len(researchers)}")

    all_publications = []

    for position, researcher in enumerate(researchers, start=1):
        publications = process_researcher(
            researcher,
            position,
            len(researchers),
        )
        all_publications.extend(publications)

    print()
    print("=" * 70)
    print("PROCESAMIENTO TERMINADO")
    print("=" * 70)

    print(f"Trabajos recuperados de ORCID: {len(all_publications)}")
    save_json(all_publications, OUTPUT_ALL)

    unique_publications = deduplicate_publications(all_publications)
    print(f"Trabajos después de deduplicar: {len(unique_publications)}")

    articles = []
    excluded = []

    for pub in unique_publications:
        if looks_like_article(pub):
            articles.append(pub)
        else:
            excluded.append(pub)

    save_json(excluded, OUTPUT_EXCLUDED)
    save_json(articles, OUTPUT_JSON)
    save_bibtex(articles, OUTPUT_BIB)
    save_html(articles, OUTPUT_HTML)

    elapsed = time.time() - start_time

    print()
    print("=" * 70)
    print("RESULTADOS")
    print("=" * 70)

    print(f"Trabajos ORCID:        {len(all_publications)}")
    print(f"Tras deduplicación:    {len(unique_publications)}")
    print(f"Artículos incluidos:   {len(articles)}")
    print(f"Trabajos excluidos:    {len(excluded)}")
    print(f"Tiempo total:          {elapsed:.1f} segundos")


if __name__ == "__main__":
    main()
