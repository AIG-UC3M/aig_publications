import json
import os
import re
import time
import html
from collections import Counter
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# CONFIGURACIÓN
# ============================================================

INPUT_FILE = "researchers1.json"

OUTPUT_JSON = "other_works.json"
OUTPUT_JSON_ALL = "other_works_all_orcid.json"
OUTPUT_JSON_BY_TYPE = "other_works_by_type.json"
OUTPUT_HTML = "other_works.html"
OUTPUT_BIB = "other_works.bib"

API_BASE = "https://pub.orcid.org/v3.0"

ROWS_PER_PAGE = 100
MAX_PAGES = 1000
REQUEST_TIMEOUT = 30

# Si True, cuando un contributor no tenga ORCID, se intenta
# identificarlo mediante nombre.
USE_NAME_FALLBACK = True

# Pausa entre peticiones a ORCID.
REQUEST_DELAY = 0.15


# ============================================================
# TOKEN ORCID
# ============================================================

ORCID_ACCESS_TOKEN = os.environ.get("ORCID_ACCESS_TOKEN")

if not ORCID_ACCESS_TOKEN:
    raise RuntimeError(
        "No se ha encontrado la variable de entorno ORCID_ACCESS_TOKEN."
    )


# ============================================================
# SESIÓN HTTP
# ============================================================

session = requests.Session()

retry = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    raise_on_status=False,
)

adapter = HTTPAdapter(max_retries=retry)

session.mount("https://", adapter)
session.mount("http://", adapter)

HEADERS = {
    "Authorization": f"Bearer {ORCID_ACCESS_TOKEN}",
    "Accept": "application/vnd.orcid+json",
}


# ============================================================
# UTILIDADES GENERALES
# ============================================================

def clean_text(value):
    """
    Limpia espacios y convierte None en cadena vacía.
    """
    if value is None:
        return ""

    value = str(value)

    value = re.sub(r"\s+", " ", value)

    return value.strip()


def safe_get(obj, *keys, default=None):
    """
    Acceso seguro a diccionarios anidados.
    """
    current = obj

    for key in keys:
        if not isinstance(current, dict):
            return default

        current = current.get(key)

        if current is None:
            return default

    return current


def normalize_orcid(value):
    """
    Normaliza un ORCID a:

        0000-0000-0000-0000

    Acepta también URLs de ORCID.
    """

    if not value:
        return ""

    value = str(value).strip()

    match = re.search(
        r"\b\d{4}-\d{4}-\d{4}-[\dXx]{4}\b",
        value
    )

    if not match:
        return ""

    return match.group(0).upper()


def normalize_name_text(name):
    """
    Normalización ligera para comparar nombres.

    No se utiliza para escribir el .bib.
    """

    if not name:
        return ""

    name = str(name).strip()

    # Elimina puntuación que no sea relevante para la comparación.
    name = re.sub(r"[.,;:()\[\]{}]", " ", name)

    # Guiones diferentes -> guion normal.
    name = name.replace("–", "-")
    name = name.replace("—", "-")
    name = name.replace("-", "-")

    # Espacios múltiples.
    name = re.sub(r"\s+", " ", name)

    return name.strip().casefold()


# ============================================================
# INVESTIGADORES
# ============================================================

def get_researcher_name(researcher):
    """
    Obtiene el nombre canónico de un investigador.

    Se intenta soportar distintas estructuras habituales
    de researchers1.json.
    """

    possible_keys = [
        "name",
        "canonical_name",
        "canonicalName",
        "display_name",
        "displayName",
        "author",
        "bibtex_name",
        "bibtexName",
    ]

    for key in possible_keys:
        value = researcher.get(key)

        if isinstance(value, str) and value.strip():
            return value.strip()

    raise ValueError(
        f"No se ha encontrado nombre para el investigador: {researcher}"
    )


def get_researcher_orcid(researcher):
    """
    Obtiene el ORCID del investigador.
    """

    possible_keys = [
        "orcid",
        "ORCID",
        "orcid_id",
        "orcidId",
        "orcid-id",
    ]

    for key in possible_keys:
        value = researcher.get(key)

        if value:
            normalized = normalize_orcid(value)

            if normalized:
                return normalized

    return ""


def load_researchers(filename):
    """
    Carga researchers1.json.

    Admite:
      - lista directamente
      - {"researchers": [...]}
      - {"investigadores": [...]}
    """

    with open(filename, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        researchers = data

    elif isinstance(data, dict):
        researchers = (
            data.get("researchers")
            or data.get("investigadores")
            or data.get("authors")
            or data.get("people")
        )

    else:
        researchers = None

    if not isinstance(researchers, list):
        raise ValueError(
            f"No se ha encontrado una lista de investigadores en {filename}"
        )

    return researchers


def build_canonical_author_map(researchers):
    """
    ORCID -> nombre canónico.

    El nombre canónico es EXACTAMENTE el que aparece
    en researchers1.json.
    """

    result = {}

    for researcher in researchers:
        name = get_researcher_name(researcher)
        orcid = get_researcher_orcid(researcher)

        if not orcid:
            print(
                f"WARNING: investigador sin ORCID: {name}"
            )
            continue

        if orcid in result and result[orcid] != name:
            raise ValueError(
                f"ORCID duplicado con nombres diferentes: "
                f"{orcid}: {result[orcid]} / {name}"
            )

        result[orcid] = name

    return result


# ============================================================
# NORMALIZACIÓN DE NOMBRES
# ============================================================

def name_signature(name):
    """
    Genera una firma conservadora para detectar variantes
    de un mismo autor cuando el registro ORCID no contiene
    contributor-orcid.

    Ejemplos que pueden acabar con una firma compatible:

        F. Díaz-de-María
        Fernando Díaz-de-María
        Díaz-de-María, F.
        Díaz-de-María, Fernando
    """

    if not name:
        return ""

    name = clean_text(name)

    # Normalizamos guiones.
    name = name.replace("–", "-")
    name = name.replace("—", "-")
    name = name.replace("-", "-")

    # Quitamos puntos para comparar iniciales.
    name = name.replace(".", "")

    # Formato:
    #     Apellido, Nombre
    if "," in name:
        surname_part, given_part = name.split(",", 1)

        surname_part = clean_text(surname_part)
        given_part = clean_text(given_part)

        first_initial = ""

        if given_part:
            first_initial = given_part[0]

        surname = surname_part

    else:
        parts = name.split()

        if not parts:
            return ""

        surname = parts[-1]
        first_initial = parts[0][0] if parts else ""

        # Para nombres del tipo:
        #
        # Fernando Díaz-de-María
        #
        # el apellido final ya contiene el guion.

    surname = normalize_name_text(surname)

    first_initial = normalize_name_text(first_initial)

    if not surname:
        return ""

    return f"{first_initial}|{surname}"


def build_name_fallback_map(researchers):
    """
    Construye:

        firma de nombre -> ORCID

    Solo conserva firmas inequívocas.

    Si dos investigadores distintos generan la misma firma,
    se elimina la firma para evitar una asignación incorrecta.
    """

    temporary = {}

    for researcher in researchers:
        name = get_researcher_name(researcher)
        orcid = get_researcher_orcid(researcher)

        if not orcid:
            continue

        signature = name_signature(name)

        if not signature:
            continue

        temporary.setdefault(signature, set()).add(orcid)

    result = {}

    for signature, orcids in temporary.items():

        if len(orcids) == 1:
            result[signature] = next(iter(orcids))

    return result


# ============================================================
# ORCID CONTRIBUTORS
# ============================================================

def extract_contributor_orcid(contributor):
    """
    Extrae el ORCID de un contributor ORCID.

    ORCID puede devolver estructuras ligeramente diferentes,
    por lo que se prueban varios campos.
    """

    if not isinstance(contributor, dict):
        return ""

    # contributor-orcid.path
    value = safe_get(
        contributor,
        "contributor-orcid",
        "path",
    )

    orcid = normalize_orcid(value)

    if orcid:
        return orcid

    # contributor-orcid.uri
    value = safe_get(
        contributor,
        "contributor-orcid",
        "uri",
    )

    orcid = normalize_orcid(value)

    if orcid:
        return orcid

    # contributor-orcid.value
    value = safe_get(
        contributor,
        "contributor-orcid",
        "value",
    )

    orcid = normalize_orcid(value)

    if orcid:
        return orcid

    # Algunas respuestas pueden contener content.
    value = safe_get(
        contributor,
        "contributor-orcid",
        "content",
    )

    orcid = normalize_orcid(value)

    if orcid:
        return orcid

    return ""


def extract_credit_name(contributor):
    """
    Obtiene credit-name de un contributor.
    """

    value = safe_get(
        contributor,
        "credit-name",
        "value",
    )

    if value:
        return clean_text(value)

    value = safe_get(
        contributor,
        "credit-name",
        "content",
    )

    if value:
        return clean_text(value)

    return ""


def extract_authors(
    work,
    canonical_by_orcid,
    fallback_by_name,
):
    """
    Extrae autores de una obra.

    PRIORIDAD:

    1. ORCID del contributor
    2. Firma de nombre, si es inequívoca
    3. credit-name original

    IMPORTANTE:

    El nombre canónico es el que se escribe en el BibTeX.
    Nunca se escriben aliases.
    """

    contributors = safe_get(
        work,
        "contributors",
        "contributor",
        default=[],
    )

    if not isinstance(contributors, list):
        return []

    authors = []

    for contributor in contributors:

        contributor_orcid = extract_contributor_orcid(
            contributor
        )

        credit_name = extract_credit_name(
            contributor
        )

        # ----------------------------------------------------
        # 1. ORCID
        # ----------------------------------------------------

        if contributor_orcid:

            canonical_name = canonical_by_orcid.get(
                contributor_orcid
            )

            if canonical_name:
                authors.append(canonical_name)
                continue

        # ----------------------------------------------------
        # 2. Fallback por nombre
        # ----------------------------------------------------

        if USE_NAME_FALLBACK and credit_name:

            signature = name_signature(
                credit_name
            )

            fallback_orcid = fallback_by_name.get(
                signature
            )

            if fallback_orcid:

                canonical_name = canonical_by_orcid.get(
                    fallback_orcid
                )

                if canonical_name:
                    authors.append(canonical_name)
                    continue

        # ----------------------------------------------------
        # 3. Si no podemos identificarlo,
        #    conservamos el credit-name original.
        # ----------------------------------------------------

        if credit_name:
            authors.append(credit_name)

    return authors


# ============================================================
# OBTENER OBRAS ORCID
# ============================================================

def get_json(url):
    """
    GET JSON con manejo básico de errores.
    """

    response = session.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"HTTP {response.status_code} para {url}\n"
            f"{response.text[:1000]}"
        )

    return response.json()


def get_works_summary(orcid):
    """
    Obtiene los resúmenes de obras de un ORCID.
    """

    works = []

    for page in range(MAX_PAGES):

        start = page * ROWS_PER_PAGE

        url = (
            f"{API_BASE}/{orcid}/works"
            f"?start={start}"
            f"&rows={ROWS_PER_PAGE}"
        )

        data = get_json(url)

        groups = data.get("group", [])

        if not groups:
            break

        for group in groups:

            summaries = group.get(
                "work-summary",
                [],
            )

            for summary in summaries:

                works.append(summary)

        if len(groups) < ROWS_PER_PAGE:
            break

        time.sleep(REQUEST_DELAY)

    return works


def get_work_detail(orcid, put_code):
    """
    Obtiene el detalle de una obra.
    """

    url = (
        f"{API_BASE}/{orcid}/work/{put_code}"
    )

    data = get_json(url)

    time.sleep(REQUEST_DELAY)

    return data


# ============================================================
# CAMPOS DE LAS OBRAS
# ============================================================

def get_title(work):
    """
    Obtiene el título principal.
    """

    title = safe_get(
        work,
        "title",
        "title",
        "value",
    )

    if title:
        return clean_text(title)

    title = safe_get(
        work,
        "title",
        "subtitle",
        "value",
    )

    if title:
        return clean_text(title)

    return ""


def get_year(work):
    """
    Obtiene el año de publicación.
    """

    candidates = [
        safe_get(
            work,
            "publication-date",
            "year",
            "value",
        ),
        safe_get(
            work,
            "publication-date",
            "month",
            "value",
        ),
    ]

    year = candidates[0]

    if year:
        try:
            return int(year)
        except (TypeError, ValueError):
            pass

    # Algunos registros pueden tener fecha en otros campos.
    for path in [
        ("publication-date", "year", "value"),
        ("publication-date", "year"),
    ]:
        value = safe_get(work, *path)

        if value:
            match = re.search(
                r"\b(19|20)\d{2}\b",
                str(value),
            )

            if match:
                return int(match.group(0))

    return None


def get_external_url(work):
    """
    Obtiene la URL externa principal.
    """

    url = safe_get(
        work,
        "url",
        "value",
    )

    if url:
        return clean_text(url)

    return ""


def get_work_type(work):
    """
    Devuelve el tipo ORCID original.
    """

    return clean_text(
        work.get("type", "")
    )


def classify_work(work):
    """
    Clasifica el trabajo para BibTeX / WordPress.

    IMPORTANTE:
    book-chapter se procesa ANTES que book para no
    caer accidentalmente en la categoría book.
    """

    work_type = (
        clean_text(
            work.get("type", "")
        )
        .lower()
    )

    # --------------------------------------------------------
    # CAPÍTULOS DE LIBRO
    # --------------------------------------------------------

    if work_type in {
        "book-chapter",
        "book chapter",
    }:
        return "incollection"

    # --------------------------------------------------------
    # LIBROS
    # --------------------------------------------------------

    if work_type in {
        "book",
        "edited-book",
    }:
        return "book"

    # --------------------------------------------------------
    # ARTÍCULOS
    # --------------------------------------------------------

    if work_type in {
        "journal-article",
        "article",
    }:
        return "article"

    # --------------------------------------------------------
    # CONGRESOS
    # --------------------------------------------------------

    if work_type in {
        "conference-paper",
        "conference-abstract",
    }:
        return "inproceedings"

    # --------------------------------------------------------
    # TESIS
    # --------------------------------------------------------

    if work_type in {
        "dissertation",
        "thesis",
    }:
        return "phdthesis"

    # --------------------------------------------------------
    # OTROS
    # --------------------------------------------------------

    return "misc"


# ============================================================
# NORMALIZACIÓN DE TÍTULOS
# ============================================================

def normalize_title(title):
    """
    Normaliza un título para deduplicación.
    """

    if not title:
        return ""

    title = str(title).casefold()

    title = (
        title
        .replace("–", "-")
        .replace("—", "-")
        .replace("-", "-")
    )

    title = re.sub(
        r"\s+",
        " ",
        title,
    )

    title = re.sub(
        r"[^\w\s-]",
        "",
        title,
    )

    return title.strip()


# ============================================================
# BIBTEX
# ============================================================

def bibtex_escape(value):
    """
    Escapado seguro para campos BibTeX.

    IMPORTANTE:
    Esta es la función que tenía el error de sintaxis.

    NO usamos:

        r"\"

    porque eso es un literal de string inválido en Python.

    En su lugar usamos "\\" para representar una
    barra invertida.
    """

    if value is None:
        return ""

    value = str(value)

    # Preservamos backslashes existentes.
    placeholder = "__BIBTEX_BACKSLASH__"

    value = value.replace(
        "\\",
        placeholder,
    )

    value = value.replace(
        "&",
        r"\&",
    )

    value = value.replace(
        "%",
        r"\%",
    )

    value = value.replace(
        "#",
        r"\#",
    )

    value = value.replace(
        "_",
        r"\_",
    )

    value = value.replace(
        "{",
        r"\{",
    )

    value = value.replace(
        "}",
        r"\}",
    )

    # Restauramos la barra invertida original.
    value = value.replace(
        placeholder,
        "\\",
    )

    return value


def unwrap_markdown_url(value):
    """
    Convierte:

        [https://example.com](https://example.com)

    en:

        https://example.com
    """

    if not value:
        return ""

    value = str(value).strip()

    match = re.fullmatch(
        r"\[([^\]]+)\]\(([^)]+)\)",
        value,
    )

    if match:
        return match.group(2)

    return value


def make_bibtex_key(publication):
    """
    Genera una clave BibTeX relativamente estable.
    """

    authors = publication.get(
        "authors",
        [],
    )

    year = publication.get(
        "year"
    )

    if authors:
        first_author = authors[0]

        first_author = (
            first_author
            .replace(
                ",",
                "",
            )
            .replace(
                ".",
                "",
            )
        )

        parts = first_author.split()

        surname = parts[-1] if parts else "Unknown"

    else:
        surname = "Unknown"

    surname = re.sub(
        r"[^A-Za-z0-9]+",
        "",
        surname,
    )

    title = publication.get(
        "title",
        "",
    )

    title_words = re.findall(
        r"[A-Za-z0-9]+",
        title,
    )

    title_part = (
        "".join(
            title_words[:2]
        )
        if title_words
        else "Work"
    )

    year_part = (
        str(year)
        if year
        else "nd"
    )

    return (
        f"{surname}"
        f"{title_part}"
        f"{year_part}"
    )


def publication_to_bibtex(publication):
    """
    Convierte una publicación a BibTeX.
    """

    bib_type = publication.get(
        "bibtex_type",
        "misc",
    )

    key = publication.get(
        "bibtex_key"
    )

    if not key:
        key = make_bibtex_key(
            publication
        )

    title = bibtex_escape(
        publication.get(
            "title",
            "",
        )
    )

    authors = publication.get(
        "authors",
        [],
    )

    author_string = " and ".join(
        bibtex_escape(author)
        for author in authors
        if author
    )

    year = publication.get(
        "year"
    )

    url = unwrap_markdown_url(
        publication.get(
            "url",
            "",
        )
    )

    lines = [
        f"@{bib_type}{{{key},",
    ]

    if title:
        lines.append(
            f"  title = {{{title}}},"
        )

    if author_string:
        lines.append(
            f"  author = {{{author_string}}},"
        )

    if year:
        lines.append(
            f"  year = {{{year}}},"
        )

    if url:
        lines.append(
            f"  url = {{{bibtex_escape(url)}}},"
        )

    # Información adicional si está disponible.
    journal = publication.get(
        "journal",
        "",
    )

    if journal:
        lines.append(
            f"  journal = {{{bibtex_escape(journal)}}},"
        )

    volume = publication.get(
        "volume",
        "",
    )

    if volume:
        lines.append(
            f"  volume = {{{bibtex_escape(volume)}}},"
        )

    issue = publication.get(
        "issue",
        "",
    )

    if issue:
        lines.append(
            f"  number = {{{bibtex_escape(issue)}}},"
        )

    pages = publication.get(
        "pages",
        "",
    )

    if pages:
        lines.append(
            f"  pages = {{{bibtex_escape(pages)}}},"
        )

    publisher = publication.get(
        "publisher",
        "",
    )

    if publisher:
        lines.append(
            f"  publisher = {{{bibtex_escape(publisher)}}},"
        )

    lines.append("}")

    return "\n".join(lines)


# ============================================================
# PROCESAMIENTO DE UNA OBRA
# ============================================================

def build_publication(
    work,
    source_orcid,
    canonical_by_orcid,
    fallback_by_name,
):
    """
    Convierte una obra ORCID en nuestro formato interno.
    """

    title = get_title(work)

    if not title:
        return None

    year = get_year(work)

    authors = extract_authors(
        work,
        canonical_by_orcid,
        fallback_by_name,
    )

    external_url = get_external_url(
        work
    )

    original_type = get_work_type(
        work
    )

    bibtex_type = classify_work(
        work
    )

    publication = {
        "title": title,
        "year": year,
        "authors": authors,
        "url": external_url,
        "type": original_type,
        "bibtex_type": bibtex_type,
        "source_orcid": source_orcid,
    }

    return publication


# ============================================================
# DEDUPLICACIÓN
# ============================================================

def publication_signature(publication):
    """
    Firma para deduplicar publicaciones.
    """

    title = normalize_title(
        publication.get(
            "title",
            "",
        )
    )

    year = publication.get(
        "year"
    )

    return (
        title,
        year,
    )


def deduplicate_publications(publications):
    """
    Elimina duplicados por título + año.

    Si aparece la misma publicación en varios ORCID,
    se conserva una sola.
    """

    result = []
    seen = set()

    for publication in publications:

        signature = publication_signature(
            publication
        )

        if signature in seen:
            continue

        seen.add(signature)

        result.append(
            publication
        )

    return result


# ============================================================
# JSON
# ============================================================

def save_json(filename, data):
    """
    Guarda JSON con UTF-8.
    """

    with open(
        filename,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# HTML
# ============================================================

def make_html(publications):
    """
    Genera una página HTML sencilla.
    """

    publications = sorted(
        publications,
        key=lambda x: (
            -(x.get("year") or 0),
            x.get("title", "").casefold(),
        ),
    )

    rows = []

    for publication in publications:

        title = html.escape(
            publication.get(
                "title",
                "",
            )
        )

        year = publication.get(
            "year",
            "",
        )

        authors = html.escape(
            ", ".join(
                publication.get(
                    "authors",
                    [],
                )
            )
        )

        url = publication.get(
            "url",
            "",
        )

        if url:
            url_html = (
                f'<a href="{html.escape(url)}" '
                f'target="_blank" '
                f'rel="noopener">{html.escape(url)}</a>'
            )
        else:
            url_html = ""

        rows.append(
            f"""
<tr>
  <td>{year or ""}</td>
  <td>{authors}</td>
  <td>{title}</td>
  <td>{url_html}</td>
</tr>
"""
        )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Other works</title>
<style>
body {{
    font-family: Arial, sans-serif;
    margin: 2rem;
}}

table {{
    border-collapse: collapse;
    width: 100%;
}}

th,
td {{
    border: 1px solid #ddd;
    padding: 8px;
    vertical-align: top;
}}

th {{
    background: #f2f2f2;
}}
</style>
</head>

<body>

<h1>Other works</h1>

<table>

<thead>
<tr>
  <th>Year</th>
  <th>Authors</th>
  <th>Title</th>
  <th>URL</th>
</tr>
</thead>

<tbody>
{''.join(rows)}
</tbody>

</table>

</body>
</html>
"""


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("ORCID OTHER WORKS")
    print("=" * 70)

    # --------------------------------------------------------
    # Cargar investigadores
    # --------------------------------------------------------

    researchers = load_researchers(
        INPUT_FILE
    )

    print(
        f"Investigadores cargados: "
        f"{len(researchers)}"
    )

    # --------------------------------------------------------
    # Mapa ORCID -> nombre canónico
    # --------------------------------------------------------

    canonical_by_orcid = (
        build_canonical_author_map(
            researchers
        )
    )

    print(
        f"ORCID canónicos: "
        f"{len(canonical_by_orcid)}"
    )

    # --------------------------------------------------------
    # Mapa fallback nombre -> ORCID
    # --------------------------------------------------------

    fallback_by_name = (
        build_name_fallback_map(
            researchers
        )
    )

    print(
        f"Firmas de nombres inequívocas: "
        f"{len(fallback_by_name)}"
    )

    # --------------------------------------------------------
    # Mostrar investigadores
    # --------------------------------------------------------

    print()
    print("Autores canónicos:")

    for orcid, name in sorted(
        canonical_by_orcid.items(),
        key=lambda x: x[1].casefold(),
    ):

        print(
            f"  {name} "
            f"-> {orcid}"
        )

    print()

    # --------------------------------------------------------
    # Obtener obras
    # --------------------------------------------------------

    all_publications = []

    all_orcid_records = []

    for index, researcher in enumerate(
        researchers,
        start=1,
    ):

        researcher_name = (
            get_researcher_name(
                researcher
            )
        )

        researcher_orcid = (
            get_researcher_orcid(
                researcher
            )
        )

        if not researcher_orcid:
            print(
                f"[{index}/{len(researchers)}] "
                f"{researcher_name}: SIN ORCID"
            )

            continue

        print(
            f"[{index}/{len(researchers)}] "
            f"{researcher_name} "
            f"({researcher_orcid})"
        )

        try:

            summaries = get_works_summary(
                researcher_orcid
            )

        except Exception as exc:

            print(
                f"  ERROR obteniendo obras: "
                f"{exc}"
            )

            continue

        print(
            f"  Obras encontradas: "
            f"{len(summaries)}"
        )

        for summary in summaries:

            put_code = summary.get(
                "put-code"
            )

            if not put_code:
                continue

            try:

                work = get_work_detail(
                    researcher_orcid,
                    put_code,
                )

            except Exception as exc:

                print(
                    f"  ERROR obra "
                    f"{put_code}: {exc}"
                )

                continue

            publication = build_publication(
                work,
                researcher_orcid,
                canonical_by_orcid,
                fallback_by_name,
            )

            if not publication:
                continue

            # Guardamos información completa
            # asociada al ORCID de origen.
            all_orcid_records.append(
                {
                    "source_orcid": researcher_orcid,
                    "source_researcher": researcher_name,
                    "put_code": put_code,
                    "publication": publication,
                }
            )

            all_publications.append(
                publication
            )

    # --------------------------------------------------------
    # Deduplicación
    # --------------------------------------------------------

    publications = (
        deduplicate_publications(
            all_publications
        )
    )

    print()
    print(
        f"Publicaciones antes de deduplicar: "
        f"{len(all_publications)}"
    )

    print(
        f"Publicaciones finales: "
        f"{len(publications)}"
    )

    # --------------------------------------------------------
    # Generar claves BibTeX
    # --------------------------------------------------------

    key_counter = Counter()

    for publication in publications:

        base_key = make_bibtex_key(
            publication
        )

        key_counter[base_key] += 1

        number = key_counter[
            base_key
        ]

        if number == 1:
            key = base_key

        else:
            key = (
                f"{base_key}_"
                f"{number}"
            )

        publication[
            "bibtex_key"
        ] = key

    # --------------------------------------------------------
    # JSON principal
    # --------------------------------------------------------

    save_json(
        OUTPUT_JSON,
        publications,
    )

    # --------------------------------------------------------
    # JSON con información de ORCID
    # --------------------------------------------------------

    save_json(
        OUTPUT_JSON_ALL,
        all_orcid_records,
    )

    # --------------------------------------------------------
    # JSON agrupado por tipo
    # --------------------------------------------------------

    by_type = {}

    for publication in publications:

        bib_type = publication.get(
            "bibtex_type",
            "misc",
        )

        by_type.setdefault(
            bib_type,
            [],
        ).append(
            publication
        )

    save_json(
        OUTPUT_JSON_BY_TYPE,
        by_type,
    )

    # --------------------------------------------------------
    # HTML
    # --------------------------------------------------------

    html_content = make_html(
        publications
    )

    with open(
        OUTPUT_HTML,
        "w",
        encoding="utf-8",
    ) as f:

        f.write(
            html_content
        )

    # --------------------------------------------------------
    # BIBTEX
    # --------------------------------------------------------

    bib_entries = []

    for publication in publications:

        bib_entries.append(
            publication_to_bibtex(
                publication
            )
        )

    bib_content = (
        "\n\n".join(
            bib_entries
        )
        + "\n"
    )

    with open(
        OUTPUT_BIB,
        "w",
        encoding="utf-8",
    ) as f:

        f.write(
            bib_content
        )

    # --------------------------------------------------------
    # Resumen
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("ARCHIVOS GENERADOS")
    print("=" * 70)

    print(
        f"  {OUTPUT_JSON}"
    )

    print(
        f"  {OUTPUT_JSON_ALL}"
    )

    print(
        f"  {OUTPUT_JSON_BY_TYPE}"
    )

    print(
        f"  {OUTPUT_HTML}"
    )

    print(
        f"  {OUTPUT_BIB}"
    )

    print()
    print("Tipos BibTeX:")

    type_counter = Counter(
        publication.get(
            "bibtex_type",
            "misc",
        )
        for publication in publications
    )

    for bib_type, count in sorted(
        type_counter.items()
    ):

        print(
            f"  {bib_type}: {count}"
        )

    print()
    print("Proceso terminado correctamente.")


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
