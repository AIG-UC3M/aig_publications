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

# Si no hay ORCID en el contributor, se intenta reconocer
# el nombre mediante las equivalencias generadas.
USE_NAME_FALLBACK = True


# ============================================================
# SESIÓN HTTP
# ============================================================

TOKEN = os.environ.get("ORCID_ACCESS_TOKEN")

HEADERS = {
    "Accept": "application/json",
}

if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"


session = requests.Session()

retry = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)

adapter = HTTPAdapter(max_retries=retry)

session.mount("https://", adapter)
session.mount("http://", adapter)


# ============================================================
# UTILIDADES GENERALES
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = str(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_title(value):
    value = clean_text(value)
    return value.casefold()


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
    Extrae un ORCID de cualquier cadena que lo contenga.
    """
    if not value:
        return ""

    value = str(value)

    match = re.search(
        r"\b\d{4}-\d{4}-\d{4}-[\dXx]{4}\b",
        value,
    )

    if not match:
        return ""

    return match.group(0).upper()


# ============================================================
# INVESTIGADORES
# ============================================================

def get_researcher_name(researcher):
    """
    Obtiene el nombre canónico definido en researchers1.json.
    """
    possible_fields = [
        "name",
        "canonical_name",
        "display_name",
        "author",
        "nombre",
    ]

    for field in possible_fields:
        value = researcher.get(field)

        if value:
            return clean_text(value)

    return ""


def get_researcher_orcid(researcher):
    """
    Obtiene el ORCID del investigador.
    """
    possible_fields = [
        "orcid",
        "ORCID",
        "orcid_id",
        "orcidId",
    ]

    for field in possible_fields:
        value = researcher.get(field)

        if value:
            return normalize_orcid(value)

    return ""


def load_researchers():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        researchers = data

    elif isinstance(data, dict):
        # Intentamos encontrar la lista en las estructuras habituales.
        for key in ["researchers", "authors", "people", "items", "data"]:
            if isinstance(data.get(key), list):
                researchers = data[key]
                break
        else:
            raise ValueError(
                f"No se encontró una lista de investigadores en {INPUT_FILE}"
            )

    else:
        raise ValueError(
            f"Formato no reconocido en {INPUT_FILE}"
        )

    return researchers


def build_canonical_author_map(researchers):
    """
    ORCID -> nombre canónico.
    """
    canonical_by_orcid = {}

    for researcher in researchers:
        name = get_researcher_name(researcher)
        orcid = get_researcher_orcid(researcher)

        if not name or not orcid:
            continue

        canonical_by_orcid[orcid] = name

    return canonical_by_orcid


# ============================================================
# NORMALIZACIÓN DE NOMBRES
# ============================================================

def remove_accents(text):
    """
    Elimina acentos para poder comparar variantes antiguas.
    """
    import unicodedata

    text = unicodedata.normalize("NFD", text)

    return "".join(
        ch
        for ch in text
        if unicodedata.category(ch) != "Mn"
    )


def normalize_name_text(name):
    """
    Normalización conservadora para comparar nombres.

    No se utiliza para escribir el .bib.
    Solo para reconocer aliases.
    """
    name = clean_text(name)

    if not name:
        return ""

    name = remove_accents(name)

    name = name.casefold()

    # Apostrofes / caracteres raros
    name = name.replace("’", "'")

    # Puntuación irrelevante para la comparación
    name = re.sub(r"[.,;:()]+", " ", name)

    # Espacios múltiples
    name = re.sub(r"\s+", " ", name)

    return name.strip()


def name_signature(name):
    """
    Produce una firma relativamente conservadora.

    Ejemplos:

        F. Díaz-de-María
        Fernando Díaz-de-María
        Díaz-de-María, F.

    -> misma firma.

    No se intenta hacer fuzzy matching agresivo.
    """

    name = clean_text(name)

    if not name:
        return ""

    normalized = normalize_name_text(name)

    if "," in normalized:
        surname_part, given_part = normalized.split(",", 1)

        surname_part = surname_part.strip()
        given_part = given_part.strip()

        first_initial = ""

        if given_part:
            first_initial = given_part[0]

        return f"{first_initial}|{surname_part}"

    parts = normalized.split()

    if len(parts) < 2:
        return normalized

    # Para nuestros nombres, el apellido compuesto suele estar
    # unido mediante guiones.
    hyphenated = [
        p for p in parts
        if "-" in p
    ]

    if hyphenated:
        surname = hyphenated[-1]

    else:
        surname = parts[-1]

    first_initial = parts[0][0]

    return f"{first_initial}|{surname}"


def surname_signature(name):
    """
    Firma alternativa basada solamente en apellido.

    Se utiliza únicamente para construir aliases explícitos.
    """
    name = clean_text(name)

    if not name:
        return ""

    normalized = normalize_name_text(name)

    if "," in normalized:
        surname = normalized.split(",", 1)[0].strip()
        return surname

    parts = normalized.split()

    hyphenated = [
        p for p in parts
        if "-" in p
    ]

    if hyphenated:
        return hyphenated[-1]

    return parts[-1] if parts else ""


def generate_name_aliases(canonical_name):
    """
    Genera formas equivalentes del nombre canónico.

    IMPORTANTE:
    estas formas SOLO sirven para reconocimiento.

    NINGUNA se escribe en el .bib.
    """

    canonical = clean_text(canonical_name)

    if not canonical:
        return set()

    aliases = set()

    normalized = normalize_name_text(canonical)

    aliases.add(normalized)

    # --------------------------------------------------------
    # Caso:
    #
    # F. Díaz-de-María
    #
    # Añadimos:
    #   Díaz-de-María, F.
    # --------------------------------------------------------

    parts = normalized.split()

    if len(parts) >= 2:
        first = parts[0]
        surname = " ".join(parts[1:])

        aliases.add(
            normalize_name_text(
                f"{surname}, {first}"
            )
        )

        # Nombre completo -> inicial + apellido
        aliases.add(
            normalize_name_text(
                f"{first[0]}. {surname}"
            )
        )

    # --------------------------------------------------------
    # Si tiene formato:
    #
    # Díaz-de-María, F.
    # --------------------------------------------------------

    if "," in normalized:
        surname, given = normalized.split(",", 1)

        surname = surname.strip()
        given = given.strip()

        if given:
            aliases.add(
                normalize_name_text(
                    f"{given[0]}. {surname}"
                )
            )

            aliases.add(
                normalize_name_text(
                    f"{given} {surname}"
                )
            )

    # --------------------------------------------------------
    # Variante sin acentos.
    # --------------------------------------------------------

    aliases.add(
        remove_accents(normalized)
    )

    # --------------------------------------------------------
    # Variante compacta:
    #
    # Fernando Díaz-de-María
    # F Díaz-de-María
    # --------------------------------------------------------

    if len(parts) >= 2:
        surname = " ".join(parts[1:])

        aliases.add(
            normalize_name_text(
                f"{parts[0][0]} {surname}"
            )
        )

        aliases.add(
            normalize_name_text(
                f"{parts[0][0]}. {surname}"
            )
        )

    return {
        alias
        for alias in aliases
        if alias
    }


def build_name_fallback_map(researchers):
    """
    Construye:

        alias normalizado -> ORCID

    Solo conserva aliases inequívocos.

    Si un alias pudiera corresponder a dos investigadores,
    se elimina para evitar asignaciones erróneas.
    """

    alias_to_orcids = {}

    for researcher in researchers:
        canonical_name = get_researcher_name(researcher)
        orcid = get_researcher_orcid(researcher)

        if not canonical_name or not orcid:
            continue

        aliases = generate_name_aliases(canonical_name)

        # También añadimos firma de nombre.
        signature = name_signature(canonical_name)

        if signature:
            aliases.add(signature)

        for alias in aliases:
            alias_to_orcids.setdefault(alias, set()).add(orcid)

    # Solo aliases que apuntan a un único ORCID.
    unique_alias_map = {}

    for alias, orcids in alias_to_orcids.items():
        if len(orcids) == 1:
            unique_alias_map[alias] = next(iter(orcids))

    return unique_alias_map


# ============================================================
# ORCID CONTRIBUTORS
# ============================================================

def extract_contributor_orcid(contributor):
    """
    Extrae ORCID de las diferentes estructuras que puede devolver ORCID.
    """

    if not isinstance(contributor, dict):
        return ""

    candidates = [
        contributor.get("path"),
        contributor.get("uri"),
        contributor.get("value"),
        contributor.get("content"),
    ]

    for value in candidates:
        orcid = normalize_orcid(value)

        if orcid:
            return orcid

    # contributor-orcid puede estar anidado.
    nested = contributor.get("contributor-orcid")

    if isinstance(nested, dict):
        for key in ["path", "uri", "value", "content"]:
            orcid = normalize_orcid(nested.get(key))

            if orcid:
                return orcid

    elif nested:
        orcid = normalize_orcid(nested)

        if orcid:
            return orcid

    return ""


def extract_credit_name(contributor):
    """
    Extrae credit-name del contributor.
    """

    if not isinstance(contributor, dict):
        return ""

    credit_name = contributor.get("credit-name")

    if isinstance(credit_name, dict):
        value = (
            credit_name.get("value")
            or credit_name.get("content")
            or credit_name.get("path")
        )

        return clean_text(value)

    if credit_name:
        return clean_text(credit_name)

    # Algunas respuestas pueden usar credit-name dentro de una estructura.
    for key in ["name", "creditName"]:
        value = contributor.get(key)

        if isinstance(value, dict):
            value = (
                value.get("value")
                or value.get("content")
            )

        if value:
            return clean_text(value)

    return ""


# ============================================================
# AUTORES
# ============================================================

def canonicalize_author(
    credit_name,
    contributor_orcid,
    canonical_by_orcid,
    fallback_by_name,
):
    """
    Devuelve SIEMPRE una única representación.

    Prioridad:

        1. ORCID
        2. alias inequívoco
        3. nombre original
    """

    credit_name = clean_text(credit_name)
    contributor_orcid = normalize_orcid(contributor_orcid)

    # --------------------------------------------------------
    # 1. ORCID
    # --------------------------------------------------------

    if contributor_orcid:
        canonical = canonical_by_orcid.get(
            contributor_orcid
        )

        if canonical:
            return canonical

    # --------------------------------------------------------
    # 2. Alias / nombre antiguo
    # --------------------------------------------------------

    if USE_NAME_FALLBACK and credit_name:
        normalized = normalize_name_text(
            credit_name
        )

        # Coincidencia directa
        matched_orcid = fallback_by_name.get(
            normalized
        )

        if matched_orcid:
            canonical = canonical_by_orcid.get(
                matched_orcid
            )

            if canonical:
                return canonical

        # Coincidencia por firma
        signature = name_signature(
            credit_name
        )

        matched_orcid = fallback_by_name.get(
            signature
        )

        if matched_orcid:
            canonical = canonical_by_orcid.get(
                matched_orcid
            )

            if canonical:
                return canonical

    # --------------------------------------------------------
    # 3. No sabemos con seguridad quién es.
    #
    # Mejor conservar el dato original que asignarlo
    # incorrectamente a otra persona.
    # --------------------------------------------------------

    return credit_name


def extract_authors(
    work,
    canonical_by_orcid,
    fallback_by_name,
):
    """
    Devuelve una lista de autores ya normalizados.
    """

    contributors = (
        safe_get(
            work,
            "contributors",
            "contributor",
            default=[]
        )
        or []
    )

    authors = []

    for contributor in contributors:
        credit_name = extract_credit_name(
            contributor
        )

        contributor_orcid = extract_contributor_orcid(
            contributor
        )

        author = canonicalize_author(
            credit_name=credit_name,
            contributor_orcid=contributor_orcid,
            canonical_by_orcid=canonical_by_orcid,
            fallback_by_name=fallback_by_name,
        )

        if author:
            authors.append(author)

    return authors


# ============================================================
# ORCID API
# ============================================================

def orcid_get(url, params=None):
    response = session.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


def get_orcid_works(orcid):
    """
    Recupera todos los put-code de works del investigador.
    """

    works = []

    start = 0

    for _ in range(MAX_PAGES):

        url = (
            f"{API_BASE}/{orcid}/works"
        )

        params = {
            "start": start,
            "rows": ROWS_PER_PAGE,
        }

        data = orcid_get(
            url,
            params=params,
        )

        groups = (
            safe_get(
                data,
                "group",
                default=[]
            )
            or []
        )

        if not groups:
            break

        for group in groups:

            summaries = (
                group.get("work-summary", [])
                or []
            )

            for summary in summaries:

                put_code = summary.get(
                    "put-code"
                )

                if put_code is not None:
                    works.append(
                        int(put_code)
                    )

        if len(groups) < ROWS_PER_PAGE:
            break

        start += ROWS_PER_PAGE

        time.sleep(0.1)

    return sorted(set(works))


def get_work(orcid, put_code):
    url = (
        f"{API_BASE}/{orcid}/work/{put_code}"
    )

    return orcid_get(url)


# ============================================================
# TIPO DE PUBLICACIÓN
# ============================================================

def classify_work(work):
    """
    Clasificación para BibTeX.

    IMPORTANTE:
    book-chapter debe comprobarse antes de la categoría book.
    """

    work_type = clean_text(
        safe_get(
            work,
            "type",
            default=""
        )
    ).casefold()

    subtype = clean_text(
        safe_get(
            work,
            "sub-type",
            default=""
        )
    ).casefold()

    journal_title = clean_text(
        safe_get(
            work,
            "journal-title",
            "value",
            default=""
        )
    )

    # --------------------------------------------------------
    # Capítulos de libro
    # --------------------------------------------------------

    if work_type == "book-chapter":
        return "incollection"

    if subtype == "book-chapter":
        return "incollection"

    # --------------------------------------------------------
    # Libros
    # --------------------------------------------------------

    if work_type == "book":
        return "book"

    if subtype == "book":
        return "book"

    # --------------------------------------------------------
    # Proceedings / conference
    # --------------------------------------------------------

    if work_type in {
        "conference-paper",
        "conference-abstract",
    }:
        return "inproceedings"

    # --------------------------------------------------------
    # Artículos
    # --------------------------------------------------------

    if work_type in {
        "journal-article",
        "article",
    }:
        return "article"

    # --------------------------------------------------------
    # Otros
    # --------------------------------------------------------

    return "misc"


# ============================================================
# DATOS BIBLIOGRÁFICOS
# ============================================================

def extract_title(work):
    title = safe_get(
        work,
        "title",
        "title",
        "value",
        default=""
    )

    if not title:
        title = safe_get(
            work,
            "title",
            "value",
            default=""
        )

    return clean_text(title)


def extract_year(work):
    """
    Extrae el año de publicación.
    """

    year = safe_get(
        work,
        "publication-date",
        "year",
        "value",
        default=""
    )

    if year:
        return clean_text(year)

    year = safe_get(
        work,
        "publication-date",
        "year",
        default=""
    )

    return clean_text(year)


def extract_doi(work):
    """
    Extrae DOI.
    """

    doi = safe_get(
        work,
        "external-ids",
        "external-id",
        default=[]
    ) or []

    for item in doi:

        if not isinstance(item, dict):
            continue

        typ = clean_text(
            item.get("external-id-type")
        ).casefold()

        value = clean_text(
            item.get("external-id-value")
        )

        if typ == "doi" and value:
            return value

    return ""


def extract_url(work):
    """
    Busca una URL útil del trabajo.
    """

    url = safe_get(
        work,
        "url",
        "value",
        default=""
    )

    if url:
        return clean_text(url)

    external_ids = (
        safe_get(
            work,
            "external-ids",
            "external-id",
            default=[]
        )
        or []
    )

    for item in external_ids:

        if not isinstance(item, dict):
            continue

        value = clean_text(
            item.get("external-id-url")
        )

        if value:
            return value

    return ""


# ============================================================
# BIBTEX
# ============================================================

def bibtex_escape(value):
    """
    Escapado seguro para campos BibTeX.
    """

    if value is None:
        return ""

    value = str(value)

    # Preservamos backslashes existentes.
    placeholder = "__BIBTEX_BACKSLASH__"

    value = value.replace(
        "\\",
        placeholder
    )

    value = value.replace(
        "&",
        r"\&"
    )

    value = value.replace(
        "%",
        r"\%"
    )

    value = value.replace(
        "#",
        r"\#"
    )

    value = value.replace(
        "_",
        r"\_"
    )

    value = value.replace(
        "{",
        r"\{"
    )

    value = value.replace(
        "}",
        r"\}"
    )

    value = value.replace(
        placeholder,
        r"\"
    )

    return value


def unwrap_markdown_url(url):
    """
    Convierte:

        [https://example.com](https://example.com)

    en:

        https://example.com
    """

    if not url:
        return ""

    match = re.fullmatch(
        r"\[([^\]]+)\]\(([^)]+)\)",
        url.strip()
    )

    if match:
        return match.group(2)

    return url.strip()


def make_bibtex_key(
    authors,
    year,
    index,
):
    """
    Genera una clave BibTeX estable y razonablemente legible.
    """

    first_author = (
        authors[0]
        if authors
        else "Unknown"
    )

    first_author = remove_accents(
        first_author
    )

    first_author = re.sub(
        r"[^A-Za-z0-9]+",
        "",
        first_author
    )

    if not first_author:
        first_author = "Unknown"

    return (
        f"{first_author}"
        f"{year or 'nd'}"
        f"_{index}"
    )


def publication_to_bibtex(
    publication,
    index,
):
    """
    Convierte una publicación ya normalizada a BibTeX.
    """

    authors = publication.get(
        "authors",
        []
    )

    title = publication.get(
        "title",
        ""
    )

    year = publication.get(
        "year",
        ""
    )

    pub_type = publication.get(
        "bibtex_type",
        "misc"
    )

    doi = publication.get(
        "doi",
        ""
    )

    url = unwrap_markdown_url(
        publication.get(
            "url",
            ""
        )
    )

    key = make_bibtex_key(
        authors,
        year,
        index,
    )

    lines = [
        f"@{pub_type}{{{key},"
    ]

    if title:
        lines.append(
            f"  title = {{{bibtex_escape(title)}}},"
        )

    if authors:
        # AQUÍ está la parte importante:
        #
        # cada investigador aparece UNA sola vez
        # y únicamente con su nombre canónico.
        #
        # No se añaden aliases.
        author_string = " and ".join(
            authors
        )

        lines.append(
            f"  author = {{{bibtex_escape(author_string)}}},"
        )

    if year:
        lines.append(
            f"  year = {{{bibtex_escape(year)}}},"
        )

    if doi:
        lines.append(
            f"  doi = {{{bibtex_escape(doi)}}},"
        )

    if url:
        lines.append(
            f"  url = {{{bibtex_escape(url)}}},"
        )

    # Campos opcionales
    journal = publication.get(
        "journal",
        ""
    )

    booktitle = publication.get(
        "booktitle",
        ""
    )

    if journal:
        lines.append(
            f"  journal = {{{bibtex_escape(journal)}}},"
        )

    if booktitle:
        lines.append(
            f"  booktitle = {{{bibtex_escape(booktitle)}}},"
        )

    lines.append("}")

    return "\n".join(lines)


# ============================================================
# PROCESAMIENTO
# ============================================================

def process_researchers():
    researchers = load_researchers()

    print(
        f"Investigadores cargados: {len(researchers)}"
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
        f"ORCID con nombre canónico: "
        f"{len(canonical_by_orcid)}"
    )

    # --------------------------------------------------------
    # Mapa alias -> ORCID
    # --------------------------------------------------------

    fallback_by_name = (
        build_name_fallback_map(
            researchers
        )
    )

    print(
        f"Aliases inequívocos generados: "
        f"{len(fallback_by_name)}"
    )

    all_publications = []
    seen = set()

    # --------------------------------------------------------
    # Recuperación de trabajos
    # --------------------------------------------------------

    for researcher in researchers:

        researcher_name = get_researcher_name(
            researcher
        )

        researcher_orcid = get_researcher_orcid(
            researcher
        )

        if not researcher_orcid:
            print(
                f"[AVISO] Sin ORCID: "
                f"{researcher_name}"
            )
            continue

        print(
            f"\nProcesando: "
            f"{researcher_name} "
            f"({researcher_orcid})"
        )

        try:
            put_codes = get_orcid_works(
                researcher_orcid
            )

        except Exception as exc:
            print(
                f"[ERROR] Works {researcher_orcid}: "
                f"{exc}"
            )
            continue

        print(
            f"  Works encontrados: "
            f"{len(put_codes)}"
        )

        for put_code in put_codes:

            unique_key = (
                researcher_orcid,
                put_code,
            )

            if unique_key in seen:
                continue

            seen.add(unique_key)

            try:
                work = get_work(
                    researcher_orcid,
                    put_code
                )

            except Exception as exc:
                print(
                    f"[ERROR] Work "
                    f"{put_code}: {exc}"
                )
                continue

            title = extract_title(work)

            if not title:
                continue

            authors = extract_authors(
                work,
                canonical_by_orcid,
                fallback_by_name,
            )

            year = extract_year(work)

            doi = extract_doi(work)

            url = extract_url(work)

            bibtex_type = classify_work(
                work
            )

            journal = clean_text(
                safe_get(
                    work,
                    "journal-title",
                    "value",
                    default=""
                )
            )

            publication = {
                "orcid": researcher_orcid,
                "put_code": put_code,
                "title": title,
                "authors": authors,
                "year": year,
                "doi": doi,
                "url": url,
                "bibtex_type": bibtex_type,
                "journal": journal,
            }

            all_publications.append(
                publication
            )

            time.sleep(0.05)

    # --------------------------------------------------------
    # Deduplicación por título + año
    # --------------------------------------------------------

    dedup = {}

    for publication in all_publications:

        key = (
            normalize_title(
                publication.get(
                    "title",
                    ""
                )
            ),
            publication.get(
                "year",
                ""
            ),
        )

        if key not in dedup:
            dedup[key] = publication

    publications = list(
        dedup.values()
    )

    publications.sort(
        key=lambda x: (
            x.get("year", ""),
            x.get("title", "").casefold(),
        ),
        reverse=True,
    )

    print(
        f"\nPublicaciones finales: "
        f"{len(publications)}"
    )

    # ========================================================
    # JSON PRINCIPAL
    # ========================================================

    with open(
        OUTPUT_JSON,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            publications,
            f,
            ensure_ascii=False,
            indent=2,
        )

    # ========================================================
    # JSON AGRUPADO POR ORCID
    # ========================================================

    by_orcid = {}

    for publication in publications:

        orcid = publication.get(
            "orcid",
            ""
        )

        by_orcid.setdefault(
            orcid,
            []
        ).append(
            publication
        )

    with open(
        OUTPUT_JSON_ALL,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            by_orcid,
            f,
            ensure_ascii=False,
            indent=2,
        )

    # ========================================================
    # JSON POR TIPO
    # ========================================================

    by_type = {}

    for publication in publications:

        pub_type = publication.get(
            "bibtex_type",
            "misc"
        )

        by_type.setdefault(
            pub_type,
            []
        ).append(
            publication
        )

    with open(
        OUTPUT_JSON_BY_TYPE,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            by_type,
            f,
            ensure_ascii=False,
            indent=2,
        )

    # ========================================================
    # BIBTEX
    # ========================================================

    bib_entries = []

    for index, publication in enumerate(
        publications,
        start=1,
    ):

        bib_entries.append(
            publication_to_bibtex(
                publication,
                index,
            )
        )

    with open(
        OUTPUT_BIB,
        "w",
        encoding="utf-8",
    ) as f:

        f.write(
            "\n\n".join(
                bib_entries
            )
        )

        f.write("\n")

    # ========================================================
    # HTML
    # ========================================================

    html_parts = [
        "<!DOCTYPE html>",
        "<html lang=\"es\">",
        "<head>",
        "<meta charset=\"utf-8\">",
        "<title>Publicaciones</title>",
        "</head>",
        "<body>",
        "<div class=\"publications\">",
    ]

    current_year = None

    for publication in publications:

        year = publication.get(
            "year",
            ""
        )

        if year != current_year:

            if current_year is not None:
                html_parts.append(
                    "</section>"
                )

            html_parts.append(
                f"<section class=\"year\" "
                f"data-year=\"{html.escape(year)}\">"
            )

            html_parts.append(
                f"<h2>{html.escape(year)}</h2>"
            )

            current_year = year

        title = html.escape(
            publication.get(
                "title",
                ""
            )
        )

        authors = html.escape(
            ", ".join(
                publication.get(
                    "authors",
                    []
                )
            )
        )

        doi = publication.get(
            "doi",
            ""
        )

        url = unwrap_markdown_url(
            publication.get(
                "url",
                ""
            )
        )

        html_parts.append(
            "<article class=\"publication\">"
        )

        html_parts.append(
            f"<h3>{title}</h3>"
        )

        if authors:
            html_parts.append(
                f"<p class=\"authors\">"
                f"{authors}</p>"
            )

        if doi:
            doi_url = (
                "https://doi.org/"
                + quote(
                    doi,
                    safe="/:()"
                )
            )

            html_parts.append(
                f'<p class="doi">'
                f'<a href="{html.escape(doi_url, quote=True)}">'
                f'{html.escape(doi)}'
                f'</a></p>'
            )

        elif url:
            html_parts.append(
                f'<p class="url">'
                f'<a href="{html.escape(url, quote=True)}">'
                f'{html.escape(url)}'
                f'</a></p>'
            )

        html_parts.append(
            "</article>"
        )

    if current_year is not None:
        html_parts.append(
            "</section>"
        )

    html_parts.extend([
        "</div>",
        "</body>",
        "</html>",
    ])

    with open(
        OUTPUT_HTML,
        "w",
        encoding="utf-8",
    ) as f:

        f.write(
            "\n".join(
                html_parts
            )
        )

    # ========================================================
    # RESUMEN
    # ========================================================

    type_counter = Counter(
        publication.get(
            "bibtex_type",
            "misc"
        )
        for publication in publications
    )

    print("\n==============================")
    print("PROCESO TERMINADO")
    print("==============================")

    print(
        f"Publicaciones: {len(publications)}"
    )

    print(
        f"BibTeX: {OUTPUT_BIB}"
    )

    print(
        f"HTML: {OUTPUT_HTML}"
    )

    print(
        f"JSON: {OUTPUT_JSON}"
    )

    print("\nTipos:")

    for pub_type, count in sorted(
        type_counter.items()
    ):
        print(
            f"  {pub_type}: {count}"
        )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    process_researchers()
