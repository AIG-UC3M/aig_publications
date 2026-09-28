import json
import os
import re
import time
import html
import unicodedata
from collections import Counter
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# CONFIGURACIÓN
# ============================================================

INPUT_FILE = "researchers1.json"

OUTPUT_OTHER = "other_works.json"
OUTPUT_ALL = "other_works_all_orcid.json"
OUTPUT_BY_TYPE = "other_works_by_type.json"
OUTPUT_HTML = "other_works.html"
OUTPUT_BIB = "other_works.bib"

API_BASE = "https://pub.orcid.org/v3.0"

ROWS_PER_PAGE = 100
MAX_PAGES = 1000
REQUEST_TIMEOUT = 30

# Si True, cuando un autor NO tiene ORCID en el registro,
# se intenta una identificación conservadora por nombre.
#
# Esto sirve especialmente para trabajos antiguos donde ORCID
# no está informado para todos los autores.
USE_NAME_FALLBACK = True


# ============================================================
# SESIÓN HTTP
# ============================================================

TOKEN = os.getenv("ORCID_ACCESS_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "No se ha encontrado ORCID_ACCESS_TOKEN en las variables de entorno."
    )

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.orcid+json",
}

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


# ============================================================
# UTILIDADES GENERALES
# ============================================================

ORCID_RE = re.compile(
    r"\b\d{4}-\d{4}-\d{4}-[\dXx]{4}\b"
)


def clean_text(value):
    """
    Convierte cualquier valor a texto limpio.
    """
    if value is None:
        return ""

    if isinstance(value, str):
        return html.unescape(value).strip()

    return str(value).strip()


def normalize_orcid(value):
    """
    Normaliza un ORCID para poder compararlo de forma fiable.

    Acepta:
        0000-0001-2345-6789
        https://orcid.org/0000-0001-2345-6789
        http://orcid.org/0000-0001-2345-6789
        orcid.org/0000-0001-2345-6789
    """

    if value is None:
        return ""

    value = clean_text(value)

    if not value:
        return ""

    match = ORCID_RE.search(value)

    if not match:
        return ""

    return match.group(0).upper()


def normalize_title(value):
    """
    Normalización de títulos para deduplicación.
    """

    value = clean_text(value)

    if not value:
        return ""

    value = unicodedata.normalize("NFKC", value)
    value = value.lower()

    # Quitar puntuación dejando letras/números
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)

    # Espacios múltiples
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def safe_get(data, *keys, default=None):
    """
    Acceso seguro a diccionarios anidados.
    """

    current = data

    for key in keys:
        if not isinstance(current, dict):
            return default

        current = current.get(key)

        if current is None:
            return default

    return current


def unwrap_markdown_url(value):
    """
    Si por algún motivo ORCID devuelve una URL con formato Markdown:

        [https://example.com](https://example.com)

    devuelve solamente:

        https://example.com
    """

    value = clean_text(value)

    if not value:
        return ""

    match = re.match(
        r"^\[[^\]]+\]\((https?://[^)]+)\)$",
        value,
        flags=re.IGNORECASE,
    )

    if match:
        return match.group(1)

    return value


# ============================================================
# NOMBRES DE AUTORES
# ============================================================

def normalize_name_key(name):
    """
    Normalización fuerte para comparar nombres SOLO como fallback.

    Ejemplo:

        "F. Díaz-de-María"
        "Diaz-de-Maria, F."
        "DÍAZ-DE-MARÍA, F."

    producen claves similares.

    IMPORTANTE:
    esta función NO se utiliza para escribir el nombre en el .bib.
    El .bib siempre utiliza el nombre canónico.
    """

    name = clean_text(name)

    if not name:
        return ""

    name = unicodedata.normalize("NFKD", name)

    # Quitar diacríticos
    name = "".join(
        char
        for char in name
        if not unicodedata.combining(char)
    )

    name = name.lower()

    # Separar puntuación
    name = re.sub(r"[^a-z0-9]+", " ", name)

    name = re.sub(r"\s+", " ", name)

    return name.strip()


def name_signature(name):
    """
    Firma conservadora para el fallback por nombre.

    Intenta identificar:

        F. Díaz-de-María
        Díaz-de-María, F.
        Francisco Díaz-de-María
        Francisco J. Díaz-de-María

    como la misma combinación de primera inicial + apellido.

    NO sustituye al ORCID.
    Solo se usa cuando el registro NO proporciona ORCID
    para el contribuidor.
    """

    name = clean_text(name)

    if not name:
        return ""

    # Caso "Apellido, Nombre"
    if "," in name:
        surname, given = name.split(",", 1)
        surname = surname.strip()
        given = given.strip()
    else:
        parts = name.split()

        if len(parts) < 2:
            return ""

        surname = parts[-1]
        given = " ".join(parts[:-1])

    surname_key = normalize_name_key(surname)

    if not surname_key:
        return ""

    given_parts = re.findall(
        r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+",
        given,
    )

    if not given_parts:
        return ""

    first_initial = normalize_name_key(given_parts[0])

    if not first_initial:
        return ""

    first_initial = first_initial[0]

    return f"{first_initial}|{surname_key}"


# ============================================================
# INVESTIGADORES
# ============================================================

def get_researcher_name(researcher):
    """
    Obtiene el nombre canónico del investigador.
    """

    for key in (
        "name",
        "nombre",
        "full_name",
        "fullname",
    ):
        value = researcher.get(key)

        if value:
            return clean_text(value)

    return ""


def get_researcher_orcid(researcher):
    """
    Obtiene y normaliza el ORCID del investigador.
    """

    for key in (
        "orcid",
        "ORCID",
        "orcid_id",
        "orcidId",
    ):
        value = researcher.get(key)

        if value:
            return normalize_orcid(value)

    return ""


def load_researchers(filename):
    """
    Carga researchers1.json.
    """

    with open(filename, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data

    if isinstance(data, dict):

        for key in (
            "researchers",
            "investigadores",
            "authors",
            "items",
            "data",
        ):
            if isinstance(data.get(key), list):
                return data[key]

    raise ValueError(
        f"No se ha encontrado una lista de investigadores en {filename}"
    )


def build_canonical_author_map(researchers):
    """
    Construye:

        ORCID -> nombre canónico

    Este es el mecanismo PRINCIPAL de normalización.
    """

    canonical_by_orcid = {}

    for researcher in researchers:

        name = get_researcher_name(researcher)
        orcid = get_researcher_orcid(researcher)

        if not name:
            continue

        if not orcid:
            print(
                f"AVISO: investigador sin ORCID: {name}"
            )
            continue

        previous = canonical_by_orcid.get(orcid)

        if previous and previous != name:

            print(
                "AVISO: ORCID con dos nombres canónicos distintos:"
            )
            print(f"  ORCID: {orcid}")
            print(f"  anterior: {previous}")
            print(f"  nuevo:    {name}")
            print(
                f"  Se utilizará: {name}"
            )

        canonical_by_orcid[orcid] = name

    return canonical_by_orcid


def build_name_fallback_map(researchers):
    """
    Construye una tabla de fallback:

        firma de nombre -> ORCID

    Solo se crean entradas cuando la firma es única.

    Esto evita que dos investigadores con una misma inicial
    y apellido puedan provocar una sustitución ambigua.
    """

    candidates = {}

    for researcher in researchers:

        name = get_researcher_name(researcher)
        orcid = get_researcher_orcid(researcher)

        if not name or not orcid:
            continue

        signature = name_signature(name)

        if not signature:
            continue

        candidates.setdefault(signature, set()).add(orcid)

    fallback = {}

    for signature, orcids in candidates.items():

        if len(orcids) == 1:
            fallback[signature] = next(iter(orcids))

    return fallback


# ============================================================
# FECHAS
# ============================================================

def extract_date(date_obj):
    """
    Extrae año, mes y día de una estructura ORCID.
    """

    if not isinstance(date_obj, dict):
        return None, None, None

    year = safe_get(date_obj, "year", "value")
    month = safe_get(date_obj, "month", "value")
    day = safe_get(date_obj, "day", "value")

    try:
        year = int(year) if year else None
    except (ValueError, TypeError):
        year = None

    try:
        month = int(month) if month else None
    except (ValueError, TypeError):
        month = None

    try:
        day = int(day) if day else None
    except (ValueError, TypeError):
        day = None

    return year, month, day


# ============================================================
# ORCID WORKS
# ============================================================

def get_orcid_work_groups(orcid):
    """
    Recupera todos los grupos de works de un ORCID.
    """

    all_groups = []
    seen_put_codes = set()

    for page in range(MAX_PAGES):

        offset = page * ROWS_PER_PAGE

        url = (
            f"{API_BASE}/{orcid}/works"
            f"?offset={offset}&rows={ROWS_PER_PAGE}"
        )

        response = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            print(
                f"ERROR ORCID works {orcid}: "
                f"HTTP {response.status_code}"
            )
            break

        data = response.json()

        groups = data.get("group", [])

        if not groups:
            break

        new_groups = 0

        for group in groups:

            summaries = group.get("work-summary", [])

            group_put_codes = []

            for summary in summaries:

                put_code = summary.get("put-code")

                if put_code is not None:
                    group_put_codes.append(str(put_code))

            # Evitar repetir grupos
            if any(
                code in seen_put_codes
                for code in group_put_codes
            ):
                continue

            for code in group_put_codes:
                seen_put_codes.add(code)

            all_groups.append(group)
            new_groups += 1

        if len(groups) < ROWS_PER_PAGE:
            break

        if new_groups == 0:
            break

        time.sleep(0.1)

    return all_groups


def extract_all_summaries(groups):
    """
    Extrae todos los work-summary de los grupos.
    """

    summaries = []

    for group in groups:

        for summary in group.get("work-summary", []):

            if isinstance(summary, dict):
                summaries.append(summary)

    return summaries


def get_orcid_work(orcid, put_code):
    """
    Recupera el detalle completo de un work.
    """

    url = f"{API_BASE}/{orcid}/work/{put_code}"

    response = session.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code != 200:
        print(
            f"ERROR ORCID work {orcid}/{put_code}: "
            f"HTTP {response.status_code}"
        )
        return None

    return response.json()


# ============================================================
# IDENTIFICACIÓN DE CONTRIBUIDORES
# ============================================================

def extract_contributor_orcid(contributor):
    """
    Extrae el ORCID de un contributor ORCID.

    La estructura puede variar, por ejemplo:

        contributor-orcid:
            path: "0000-0001-2345-6789"

    o:

        contributor-orcid:
            uri: "https://orcid.org/0000-0001-2345-6789"
    """

    if not isinstance(contributor, dict):
        return ""

    value = contributor.get("contributor-orcid")

    if not value:
        return ""

    candidates = []

    if isinstance(value, dict):

        for key in (
            "path",
            "uri",
            "value",
            "content",
        ):
            candidate = value.get(key)

            if candidate:
                candidates.append(candidate)

    elif isinstance(value, str):
        candidates.append(value)

    for candidate in candidates:

        orcid = normalize_orcid(candidate)

        if orcid:
            return orcid

    return ""


def extract_credit_name(contributor):
    """
    Extrae el nombre mostrado por ORCID.
    """

    if not isinstance(contributor, dict):
        return ""

    credit_name = contributor.get("credit-name")

    if isinstance(credit_name, dict):
        value = credit_name.get("value")

        if value:
            return clean_text(value)

    if isinstance(credit_name, str):
        return clean_text(credit_name)

    return ""


def extract_authors(
    work,
    canonical_by_orcid,
    fallback_by_name=None,
):
    """
    Devuelve la lista de autores que aparecerá en el .bib.

    PRIORIDAD:

    1. ORCID del contribuidor -> nombre canónico.
    2. Si no hay ORCID:
       fallback conservador por nombre.
    3. Si no se puede identificar:
       nombre original de ORCID.

    Muy importante:
    cuando encontramos el ORCID de un investigador,
    NO conservamos su nombre original.
    Utilizamos exclusivamente el nombre canónico.
    """

    authors = []

    contributors = safe_get(
        work,
        "contributors",
        "contributor",
        default=[],
    )

    if not isinstance(contributors, list):
        return authors

    for contributor in contributors:

        credit_name = extract_credit_name(contributor)

        if not credit_name:
            continue

        contributor_orcid = extract_contributor_orcid(
            contributor
        )

        canonical_name = None

        # ----------------------------------------------------
        # 1. NORMALIZACIÓN POR ORCID
        # ----------------------------------------------------

        if contributor_orcid:

            canonical_name = canonical_by_orcid.get(
                contributor_orcid
            )

        # ----------------------------------------------------
        # 2. FALLBACK POR NOMBRE
        # ----------------------------------------------------

        if (
            canonical_name is None
            and USE_NAME_FALLBACK
            and fallback_by_name
            and not contributor_orcid
        ):

            signature = name_signature(credit_name)

            fallback_orcid = fallback_by_name.get(
                signature
            )

            if fallback_orcid:

                canonical_name = canonical_by_orcid.get(
                    fallback_orcid
                )

        # ----------------------------------------------------
        # 3. RESULTADO FINAL
        # ----------------------------------------------------

        if canonical_name:
            authors.append(canonical_name)
        else:
            authors.append(credit_name)

    return authors


# ============================================================
# IDENTIFICADORES EXTERNOS
# ============================================================

def extract_external_ids(work):
    """
    Extrae DOI, PMID, PMCID, ISBN, ISSN, etc.
    """

    result = {
        "doi": "",
        "pmid": "",
        "pmcid": "",
        "isbn": "",
        "issn": "",
        "others": [],
    }

    external_ids = safe_get(
        work,
        "external-ids",
        "external-id",
        default=[],
    )

    if not isinstance(external_ids, list):
        return result

    for item in external_ids:

        if not isinstance(item, dict):
            continue

        id_type = clean_text(
            item.get("external-id-type")
        ).lower()

        value = clean_text(
            item.get("external-id-value")
        )

        if not value:
            continue

        # DOI
        if id_type == "doi":

            value = re.sub(
                r"^https?://doi\.org/",
                "",
                value,
                flags=re.IGNORECASE,
            )

            value = re.sub(
                r"^doi:\s*",
                "",
                value,
                flags=re.IGNORECASE,
            )

            result["doi"] = value.strip()

        elif id_type == "pmid":
            result["pmid"] = value

        elif id_type == "pmcid":
            result["pmcid"] = value

        elif id_type == "isbn":
            result["isbn"] = value

        elif id_type == "issn":
            result["issn"] = value

        else:
            result["others"].append(
                {
                    "type": id_type,
                    "value": value,
                }
            )

    return result


# ============================================================
# DATOS DEL WORK
# ============================================================

def extract_title(work):
    title = safe_get(
        work,
        "title",
        "title",
        "value",
        default="",
    )

    return clean_text(title)


def extract_subtitle(work):
    subtitle = safe_get(
        work,
        "title",
        "subtitle",
        "value",
        default="",
    )

    return clean_text(subtitle)


def extract_translated_title(work):
    translated = safe_get(
        work,
        "title",
        "translated-title",
        "value",
        default="",
    )

    return clean_text(translated)


def extract_journal(work):
    journal = safe_get(
        work,
        "journal-title",
        "value",
        default="",
    )

    return clean_text(journal)


def extract_url(work):
    """
    Obtiene una URL limpia.

    Si ORCID devuelve una URL como Markdown,
    se convierte a URL normal.
    """

    url = safe_get(
        work,
        "url",
        "value",
        default="",
    )

    return unwrap_markdown_url(url)


def extract_short_description(work):
    value = work.get("short-description", "")

    return clean_text(value)


def extract_language(work):
    value = work.get("language-code", "")

    return clean_text(value)


def extract_country(work):
    value = safe_get(
        work,
        "country",
        "value",
        default="",
    )

    return clean_text(value)


def extract_citation(work):
    citation = work.get("citation")

    if isinstance(citation, dict):

        value = citation.get("citation-value")

        if value:
            return clean_text(value)

    return ""


# ============================================================
# CLASIFICACIÓN
# ============================================================

def classify_work(work):
    """
    Clasifica un trabajo en article / conference / other.
    """

    work_type = clean_text(
        work.get("type")
    ).lower()

    subtype = clean_text(
        work.get("journal-title", {}).get("value")
        if isinstance(work.get("journal-title"), dict)
        else ""
    )

    if work_type in {
        "journal-article",
        "article",
        "review",
    }:
        return "article"

    if work_type in {
        "conference-paper",
        "conference-abstract",
        "conference-poster",
    }:
        return "conference"

    if work_type in {
        "book",
        "book-chapter",
        "book-review",
    }:
        return "book"

    if work_type in {
        "book-chapter",
    }:
        return "incollection"

    if "conference" in work_type:
        return "conference"

    return "other"


def bibtex_entry_type(publication):
    """
    Tipo BibTeX.

    Ajusta esto si tu instalación de bibfilter necesita
    tipos concretos.
    """

    pub_type = publication.get("type")

    if pub_type == "article":
        return "article"

    if pub_type == "conference":
        return "inproceedings"

    if pub_type == "book":
        return "book"

    if pub_type == "incollection":
        return "incollection"

    return "misc"


# ============================================================
# WORK -> PUBLICACIÓN
# ============================================================

def work_to_publication(
    work,
    summary,
    researcher_name,
    researcher_orcid,
    canonical_by_orcid,
    fallback_by_name,
):
    """
    Convierte un work ORCID en nuestro formato interno.
    """

    title = extract_title(work)

    subtitle = extract_subtitle(work)

    if subtitle:
        title = f"{title}: {subtitle}"

    translated_title = extract_translated_title(work)

    journal = extract_journal(work)

    url = extract_url(work)

    description = extract_short_description(work)

    language = extract_language(work)

    country = extract_country(work)

    citation = extract_citation(work)

    authors = extract_authors(
        work,
        canonical_by_orcid,
        fallback_by_name,
    )

    external_ids = extract_external_ids(work)

    publication_date = work.get(
        "publication-date"
    )

    year, month, day = extract_date(
        publication_date
    )

    if not year:
        year, month, day = extract_date(
            summary.get("publication-date")
        )

    work_type = classify_work(work)

    put_code = work.get("put-code")

    publication = {
        "title": title,
        "translated_title": translated_title,
        "authors": authors,
        "researcher": researcher_name,
        "orcid": researcher_orcid,
        "year": year,
        "month": month,
        "day": day,
        "type": work_type,
        "journal": journal,
        "url": url,
        "description": description,
        "language": language,
        "country": country,
        "citation": citation,
        "put_code": put_code,
        "orcid_work_url": (
            f"https://orcid.org/{researcher_orcid}"
            if researcher_orcid
            else ""
        ),
        **external_ids,
    }

    return publication


# ============================================================
# DEDUPLICACIÓN
# ============================================================

def publication_dedupe_key(publication):
    """
    Clave para detectar publicaciones repetidas.
    """

    doi = clean_text(
        publication.get("doi")
    ).lower()

    if doi:
        return ("doi", doi)

    pmid = clean_text(
        publication.get("pmid")
    ).lower()

    if pmid:
        return ("pmid", pmid)

    pmcid = clean_text(
        publication.get("pmcid")
    ).lower()

    if pmcid:
        return ("pmcid", pmcid)

    title = normalize_title(
        publication.get("title")
    )

    year = publication.get("year")

    pub_type = publication.get("type")

    return (
        "fallback",
        title,
        year,
        pub_type,
    )


def deduplicate_publications(publications):
    """
    Elimina duplicados conservando el primer registro.
    """

    seen = set()
    result = []

    for publication in publications:

        key = publication_dedupe_key(
            publication
        )

        if key in seen:
            continue

        seen.add(key)

        result.append(publication)

    return result


# ============================================================
# BIBTEX
# ============================================================

def bibtex_escape(value):
    """
    Escape correcto para BibTeX.

    Importante:
    - "_" -> "\\_"
    - "&" -> "\\&"
    - "%" -> "\\%"
    - "#" -> "\\#"

    Y se evita generar dobles escapes innecesarios.
    """

    value = clean_text(value)

    if not value:
        return ""

    # Utilizamos un marcador temporal para la barra invertida.
    placeholder = "\x00"

    value = value.replace("\\", placeholder)

    value = value.replace("{", r"\{")
    value = value.replace("}", r"\}")
    value = value.replace("&", r"\&")
    value = value.replace("%", r"\%")
    value = value.replace("#", r"\#")
    value = value.replace("_", r"\_")

    value = value.replace(
        placeholder,
        r"\textbackslash{}",
    )

    return value


def bibtex_author_name(author):
    """
    Devuelve el nombre del autor tal como debe aparecer
    en BibTeX.

    NO modifica el nombre canónico.
    """

    return clean_text(author)


def make_bibtex_key(publication, index):
    """
    Genera una clave BibTeX estable.
    """

    authors = publication.get(
        "authors",
        [],
    )

    if authors:
        first_author = authors[0]
    else:
        first_author = "unknown"

    normalized = unicodedata.normalize(
        "NFKD",
        first_author,
    )

    normalized = normalized.encode(
        "ascii",
        "ignore",
    ).decode("ascii")

    normalized = re.sub(
        r"[^A-Za-z0-9]+",
        "",
        normalized,
    )

    normalized = normalized or "unknown"

    year = publication.get("year")

    if not year:
        year = "nd"

    return f"{normalized}{year}_{index}"


def publication_to_bibtex(
    publication,
    index,
):
    """
    Convierte una publicación a BibTeX.
    """

    entry_type = bibtex_entry_type(
        publication
    )

    key = make_bibtex_key(
        publication,
        index,
    )

    authors = publication.get(
        "authors",
        [],
    )

    # IMPORTANTE:
    # Aquí no se generan variantes.
    # Se utiliza exactamente la forma canónica.
    author_value = " and ".join(
        bibtex_author_name(author)
        for author in authors
        if author
    )

    lines = [
        f"@{entry_type}{{{key},"
    ]

    if publication.get("title"):
        lines.append(
            "  title = {%s},"
            % bibtex_escape(
                publication["title"]
            )
        )

    if author_value:
        lines.append(
            "  author = {%s},"
            % bibtex_escape(
                author_value
            )
        )

    if publication.get("journal"):
        if entry_type == "article":
            lines.append(
                "  journal = {%s},"
                % bibtex_escape(
                    publication["journal"]
                )
            )
        elif entry_type == "inproceedings":
            lines.append(
                "  booktitle = {%s},"
                % bibtex_escape(
                    publication["journal"]
                )
            )

    if publication.get("year"):
        lines.append(
            "  year = {%s},"
            % str(publication["year"])
        )

    if publication.get("doi"):
        lines.append(
            "  doi = {%s},"
            % bibtex_escape(
                publication["doi"]
            )
        )

    if publication.get("isbn"):
        lines.append(
            "  isbn = {%s},"
            % bibtex_escape(
                publication["isbn"]
            )
        )

    if publication.get("issn"):
        lines.append(
            "  issn = {%s},"
            % bibtex_escape(
                publication["issn"]
            )
        )

    if publication.get("url"):

        clean_url = unwrap_markdown_url(
            publication["url"]
        )

        lines.append(
            "  url = {%s},"
            % bibtex_escape(
                clean_url
            )
        )

    lines.append("}")

    return "\n".join(lines)


def save_bibtex(publications, filename):
    """
    Guarda el archivo BibTeX completo.
    """

    entries = []

    for index, publication in enumerate(
        publications,
        start=1,
    ):

        entries.append(
            publication_to_bibtex(
                publication,
                index,
            )
        )

    content = "\n\n".join(entries)

    with open(
        filename,
        "w",
        encoding="utf-8",
    ) as f:
        f.write(content)

        if content:
            f.write("\n")


# ============================================================
# HTML
# ============================================================

def html_escape(value):
    return html.escape(
        clean_text(value),
        quote=True,
    )


def publication_to_html(publication):
    """
    Convierte una publicación a una fila HTML.
    """

    title = html_escape(
        publication.get("title")
    )

    year = publication.get("year") or ""

    authors = "; ".join(
        publication.get("authors", [])
    )

    authors = html_escape(authors)

    journal = html_escape(
        publication.get("journal")
    )

    url = unwrap_markdown_url(
        publication.get("url", "")
    )

    if url:
        title_html = (
            f'<a href="{html_escape(url)}" '
            f'target="_blank" '
            f'rel="noopener">{title}</a>'
        )
    else:
        title_html = title

    return (
        "<tr>"
        f"<td>{year}</td>"
        f"<td>{title_html}</td>"
        f"<td>{authors}</td>"
        f"<td>{journal}</td>"
        "</tr>"
    )


def save_html(publications, filename):
    """
    Guarda un HTML sencillo.
    """

    rows = "\n".join(
        publication_to_html(pub)
        for pub in publications
    )

    content = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
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

th, td {{
    border: 1px solid #ddd;
    padding: 8px;
    vertical-align: top;
}}

th {{
    text-align: left;
}}
</style>
</head>
<body>

<table>
<thead>
<tr>
    <th>Año</th>
    <th>Título</th>
    <th>Autores</th>
    <th>Revista</th>
</tr>
</thead>
<tbody>
{rows}
</tbody>
</table>

</body>
</html>
"""

    with open(
        filename,
        "w",
        encoding="utf-8",
    ) as f:
        f.write(content)


# ============================================================
# PROCESAMIENTO DE INVESTIGADOR
# ============================================================

def process_researcher(
    researcher,
    position,
    total,
    canonical_by_orcid,
    fallback_by_name,
):
    """
    Procesa todos los trabajos de un investigador.
    """

    name = get_researcher_name(
        researcher
    )

    orcid = get_researcher_orcid(
        researcher
    )

    if not name:
        print(
            f"[{position}/{total}] "
            "Investigador sin nombre. Se omite."
        )
        return []

    if not orcid:
        print(
            f"[{position}/{total}] "
            f"{name}: sin ORCID. Se omite."
        )
        return []

    print(
        f"[{position}/{total}] "
        f"{name} ({orcid})"
    )

    groups = get_orcid_work_groups(
        orcid
    )

    summaries = extract_all_summaries(
        groups
    )

    print(
        f"    Works encontrados: {len(summaries)}"
    )

    publications = []

    for summary in summaries:

        put_code = summary.get(
            "put-code"
        )

        if put_code is None:
            continue

        work = get_orcid_work(
            orcid,
            put_code,
        )

        if not work:
            continue

        publication = work_to_publication(
            work=work,
            summary=summary,
            researcher_name=name,
            researcher_orcid=orcid,
            canonical_by_orcid=canonical_by_orcid,
            fallback_by_name=fallback_by_name,
        )

        publications.append(
            publication
        )

        time.sleep(0.05)

    print(
        f"    Publications procesadas: "
        f"{len(publications)}"
    )

    return publications


# ============================================================
# AGRUPACIÓN
# ============================================================

def group_by_type(publications):
    result = {}

    for publication in publications:

        pub_type = publication.get(
            "type",
            "other",
        )

        result.setdefault(
            pub_type,
            []
        ).append(publication)

    return result


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("ORCID -> publicaciones -> BibTeX")
    print("=" * 70)

    # --------------------------------------------------------
    # 1. Cargar investigadores
    # --------------------------------------------------------

    researchers = load_researchers(
        INPUT_FILE
    )

    print(
        f"Investigadores cargados: "
        f"{len(researchers)}"
    )

    # --------------------------------------------------------
    # 2. Construir mapa ORCID -> nombre canónico
    # --------------------------------------------------------

    canonical_by_orcid = (
        build_canonical_author_map(
            researchers
        )
    )

    print(
        f"Nombres canónicos con ORCID: "
        f"{len(canonical_by_orcid)}"
    )

    # --------------------------------------------------------
    # 3. Construir fallback de nombres
    # --------------------------------------------------------

    fallback_by_name = (
        build_name_fallback_map(
            researchers
        )
    )

    print(
        f"Firmas de nombre para fallback: "
        f"{len(fallback_by_name)}"
    )

    # --------------------------------------------------------
    # 4. Procesar investigadores
    # --------------------------------------------------------

    all_publications = []

    total = len(researchers)

    for position, researcher in enumerate(
        researchers,
        start=1,
    ):

        publications = process_researcher(
            researcher=researcher,
            position=position,
            total=total,
            canonical_by_orcid=canonical_by_orcid,
            fallback_by_name=fallback_by_name,
        )

        all_publications.extend(
            publications
        )

    print()
    print(
        f"Publicaciones totales ORCID: "
        f"{len(all_publications)}"
    )

    # --------------------------------------------------------
    # 5. Guardar TODAS las publicaciones
    # --------------------------------------------------------

    with open(
        OUTPUT_ALL,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            all_publications,
            f,
            ensure_ascii=False,
            indent=2,
        )

    # --------------------------------------------------------
    # 6. Deduplicar
    # --------------------------------------------------------

    publications = deduplicate_publications(
        all_publications
    )

    print(
        f"Después de deduplicar: "
        f"{len(publications)}"
    )

    # --------------------------------------------------------
    # 7. Guardar other_works.json
    # --------------------------------------------------------

    with open(
        OUTPUT_OTHER,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            publications,
            f,
            ensure_ascii=False,
            indent=2,
        )

    # --------------------------------------------------------
    # 8. Agrupar por tipo
    # --------------------------------------------------------

    grouped = group_by_type(
        publications
    )

    with open(
        OUTPUT_BY_TYPE,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            grouped,
            f,
            ensure_ascii=False,
            indent=2,
        )

    # --------------------------------------------------------
    # 9. Ordenar
    # --------------------------------------------------------

    publications.sort(
        key=lambda pub: (
            -(pub.get("year") or 0),
            normalize_title(
                pub.get("title")
            ),
        )
    )

    # --------------------------------------------------------
    # 10. BibTeX
    # --------------------------------------------------------

    save_bibtex(
        publications,
        OUTPUT_BIB,
    )

    # --------------------------------------------------------
    # 11. HTML
    # --------------------------------------------------------

    save_html(
        publications,
        OUTPUT_HTML,
    )

    # --------------------------------------------------------
    # 12. Resumen
    # --------------------------------------------------------

    type_counter = Counter(
        pub.get("type", "other")
        for pub in publications
    )

    print()
    print("=" * 70)
    print("RESUMEN")
    print("=" * 70)

    print(
        f"Investigadores:       {len(researchers)}"
    )

    print(
        f"Works ORCID:          {len(all_publications)}"
    )

    print(
        f"Publicaciones únicas: {len(publications)}"
    )

    print()

    for pub_type, count in sorted(
        type_counter.items()
    ):
        print(
            f"  {pub_type:15s}: {count}"
        )

    print()
    print("Archivos generados:")

    print(
        f"  - {OUTPUT_ALL}"
    )

    print(
        f"  - {OUTPUT_OTHER}"
    )

    print(
        f"  - {OUTPUT_BY_TYPE}"
    )

    print(
        f"  - {OUTPUT_HTML}"
    )

    print(
        f"  - {OUTPUT_BIB}"
    )

    print()
    print(
        "Normalización de autores: "
        "ORCID -> nombre canónico"
    )

    print(
        "Fallback por nombre: "
        f"{'ACTIVADO' if USE_NAME_FALLBACK else 'DESACTIVADO'}"
    )

    print("=" * 70)


if __name__ == "__main__":
    main()
