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
OUTPUT_ALL = "other_works_all_orcid.json"
OUTPUT_BY_TYPE = "other_works_by_type.json"
OUTPUT_HTML = "other_works.html"
OUTPUT_BIB = "other_works.bib"

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
        "El workflow debe generar el token antes de ejecutar el script."
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
# UTILIDADES
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    return str(value).strip()


def normalize_title(title):
    if not title:
        return ""

    title = str(title).lower()
    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"[^\w\s]", "", title)

    return title.strip()


def safe_get(url, params=None):
    try:
        response = session.get(
            url,
            headers=HEADERS,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()
        return response.json()

    except requests.RequestException as error:
        print(f"      ERROR HTTP: {error}")
        return None

    except ValueError as error:
        print(f"      ERROR JSON: {error}")
        return None


# ============================================================
# FECHAS
# ============================================================

def get_date(date_obj):
    if not isinstance(date_obj, dict):
        return ""

    year_obj = date_obj.get("year") or {}
    month_obj = date_obj.get("month") or {}
    day_obj = date_obj.get("day") or {}

    year = (
        year_obj.get("value")
        if isinstance(year_obj, dict)
        else None
    )

    month = (
        month_obj.get("value")
        if isinstance(month_obj, dict)
        else None
    )

    day = (
        day_obj.get("value")
        if isinstance(day_obj, dict)
        else None
    )

    if not year:
        return ""

    year = str(year)
    month = str(month).zfill(2) if month else "01"
    day = str(day).zfill(2) if day else "01"

    return f"{year}-{month}-{day}"


def get_year(date_obj):
    if not isinstance(date_obj, dict):
        return ""

    year_obj = date_obj.get("year") or {}

    if not isinstance(year_obj, dict):
        return ""

    return clean_text(year_obj.get("value"))


# ============================================================
# ORCID: OBTENER WORK GROUPS
# ============================================================

def get_orcid_work_groups(orcid):
    all_groups = []
    start = 0
    seen_group_keys = set()

    for page_number in range(MAX_PAGES):
        params = {
            "start": start,
            "rows": ROWS_PER_PAGE,
        }

        print(
            f"      Página ORCID "
            f"{start}-{start + ROWS_PER_PAGE}"
        )

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
            print("      AVISO: no hay grupos nuevos.")
            break

        all_groups.extend(new_groups)

        if len(groups) < ROWS_PER_PAGE:
            break

        start += len(groups)

    print(
        f"      Grupos obtenidos: {len(all_groups)}"
    )

    return all_groups


def extract_all_summaries(groups):
    summaries = []
    seen_put_codes = set()

    for group in groups:
        if not isinstance(group, dict):
            continue

        group_summaries = group.get("work-summary") or []

        if not isinstance(group_summaries, list):
            group_summaries = [group_summaries]

        for summary in group_summaries:
            if not isinstance(summary, dict):
                continue

            put_code = summary.get("put-code")

            if put_code is None:
                continue

            put_code = str(put_code)

            if put_code in seen_put_codes:
                continue

            seen_put_codes.add(put_code)
            summaries.append(summary)

    return summaries


def get_orcid_work(orcid, put_code):
    return safe_get(
        f"{API_BASE}/{orcid}/work/{put_code}"
    )


# ============================================================
# EXTRACCIÓN DE DATOS
# ============================================================

def extract_external_ids(work):
    result = {
        "doi": "",
        "pmid": "",
        "pmcid": "",
        "isbn": "",
        "issn": "",
        "other_ids": [],
    }

    if not isinstance(work, dict):
        return result

    external_ids = work.get("external-ids") or {}
    external_id_list = (
        external_ids.get("external-id") or []
    )

    if not isinstance(external_id_list, list):
        external_id_list = [external_id_list]

    for item in external_id_list:
        if not isinstance(item, dict):
            continue

        id_type = clean_text(
            item.get("external-id-type")
        ).lower()

        value = clean_text(
            item.get("external-id-value")
            or item.get("value")
        )

        if not value:
            continue

        if id_type == "doi":
            doi = value.lower()

            doi = re.sub(
                r"^https?://doi\.org/",
                "",
                doi,
            )

            doi = doi.replace(
                "doi:",
                "",
            ).strip()

            result["doi"] = doi

        elif id_type in ("pmid", "pubmed"):
            result["pmid"] = value

        elif id_type == "pmcid":
            result["pmcid"] = value

        elif id_type == "isbn":
            result["isbn"] = value

        elif id_type in ("issn", "eissn"):
            result["issn"] = value

        else:
            result["other_ids"].append(
                {
                    "type": id_type,
                    "value": value,
                }
            )

    return result


def extract_title(work):
    if not isinstance(work, dict):
        return ""

    title_obj = work.get("title") or {}

    if not isinstance(title_obj, dict):
        return clean_text(title_obj)

    title = title_obj.get("title") or {}

    if isinstance(title, dict):
        return clean_text(
            title.get("value")
            or title.get("content")
        )

    return clean_text(title)


def extract_subtitle(work):
    if not isinstance(work, dict):
        return ""

    title_obj = work.get("title") or {}

    if not isinstance(title_obj, dict):
        return ""

    subtitle = title_obj.get("subtitle") or {}

    if isinstance(subtitle, dict):
        return clean_text(
            subtitle.get("value")
            or subtitle.get("content")
        )

    return clean_text(subtitle)


def extract_translated_title(work):
    if not isinstance(work, dict):
        return ""

    translated = work.get("translated-title") or {}

    if isinstance(translated, dict):
        return clean_text(
            translated.get("value")
            or translated.get("content")
        )

    return clean_text(translated)


def extract_journal(work):
    if not isinstance(work, dict):
        return ""

    journal = work.get("journal-title") or {}

    if isinstance(journal, dict):
        return clean_text(
            journal.get("value")
            or journal.get("content")
        )

    return clean_text(journal)


def extract_url(work):
    if not isinstance(work, dict):
        return ""

    url_obj = work.get("url")

    if isinstance(url_obj, dict):
        return clean_text(
            url_obj.get("value")
            or url_obj.get("content")
        )

    return clean_text(url_obj)


def extract_short_description(work):
    if not isinstance(work, dict):
        return ""

    return clean_text(
        work.get("short-description")
    )


def extract_language(work):
    if not isinstance(work, dict):
        return ""

    return clean_text(
        work.get("language-code")
    )


def extract_country(work):
    if not isinstance(work, dict):
        return ""

    return clean_text(
        work.get("country")
    )


def extract_citation(work):
    if not isinstance(work, dict):
        return {
            "type": "",
            "value": "",
        }

    citation = work.get("citation") or {}

    if not isinstance(citation, dict):
        return {
            "type": "",
            "value": "",
        }

    return {
        "type": clean_text(
            citation.get("citation-type")
        ),
        "value": clean_text(
            citation.get("citation-value")
        ),
    }


def extract_authors(work):
    authors = []

    if not isinstance(work, dict):
        return authors

    contributors = work.get("contributors") or {}
    contributor_list = (
        contributors.get("contributor") or []
    )

    if not isinstance(contributor_list, list):
        contributor_list = [contributor_list]

    for contributor in contributor_list:
        if not isinstance(contributor, dict):
            continue

        credit_name = contributor.get(
            "credit-name"
        ) or {}

        if isinstance(credit_name, dict):
            name = clean_text(
                credit_name.get("value")
                or credit_name.get("content")
            )
        else:
            name = clean_text(credit_name)

        if name:
            authors.append(name)

    return authors


# ============================================================
# CONVERSIÓN A NUESTRO FORMATO
# ============================================================

def work_to_publication(work, summary=None):
    if not isinstance(work, dict):
        work = {}

    if not isinstance(summary, dict):
        summary = {}

    title = (
        extract_title(work)
        or extract_title(summary)
    )

    subtitle = (
        extract_subtitle(work)
        or extract_subtitle(summary)
    )

    translated_title = (
        extract_translated_title(work)
        or extract_translated_title(summary)
    )

    journal = (
        extract_journal(work)
        or extract_journal(summary)
    )

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

    if not year and date:
        year = date[:4]

    identifiers = extract_external_ids(work)

    if not identifiers["doi"]:
        summary_ids = extract_external_ids(summary)

        for key in (
            "doi",
            "pmid",
            "pmcid",
            "isbn",
            "issn",
        ):
            if (
                not identifiers[key]
                and summary_ids[key]
            ):
                identifiers[key] = summary_ids[key]

    url = (
        extract_url(work)
        or extract_url(summary)
    )

    short_description = (
        extract_short_description(work)
        or extract_short_description(summary)
    )

    language = (
        extract_language(work)
        or extract_language(summary)
    )

    country = (
        extract_country(work)
        or extract_country(summary)
    )

    citation = extract_citation(work)

    authors = extract_authors(work)

    if not authors:
        authors = extract_authors(summary)

    put_code = (
        work.get("put-code")
        or summary.get("put-code")
    )

    source = (
        work.get("source")
        or summary.get("source")
        or {}
    )

    source_name = ""

    if isinstance(source, dict):
        source_name = clean_text(
            source.get("source-name")
        )

    return {
        "put_code": (
            str(put_code)
            if put_code is not None
            else ""
        ),
        "title": title,
        "subtitle": subtitle,
        "translated_title": translated_title,
        "type": work_type,
        "journal": journal,
        "date": date,
        "year": year,
        "doi": identifiers["doi"],
        "pmid": identifiers["pmid"],
        "pmcid": identifiers["pmcid"],
        "isbn": identifiers["isbn"],
        "issn": identifiers["issn"],
        "other_ids": identifiers["other_ids"],
        "url": url,
        "authors": authors,
        "source": source_name,
        "short_description": short_description,
        "language": language,
        "country": country,
        "citation_type": citation["type"],
        "citation_value": citation["value"],
        "raw_type": work_type,
    }


# ============================================================
# CLASIFICACIÓN
# ============================================================

def looks_like_article(pub):
    if not isinstance(pub, dict):
        return False

    title = clean_text(pub.get("title"))

    if not title:
        return False

    work_type = clean_text(
        pub.get("type")
    ).lower()

    journal = clean_text(
        pub.get("journal")
    )

    doi = clean_text(
        pub.get("doi")
    )

    article_types = {
        "journal-article",
        "article",
        "journal article",
    }

    if work_type in article_types:
        return True

    if work_type == "other" and journal:
        return True

    if not work_type and journal:
        return True

    if journal and doi:
        return True

    return False


def looks_like_conference(pub):
    if not isinstance(pub, dict):
        return False

    title = clean_text(
        pub.get("title")
    )

    journal = clean_text(
        pub.get("journal")
    )

    work_type = clean_text(
        pub.get("type")
    ).lower()

    if not title:
        return False

    article_types = {
        "journal-article",
        "article",
        "journal article",
    }

    if work_type in article_types:
        return False

    conference_types = {
        "conference-paper",
        "conference-abstract",
        "conference-output",
        "conference-poster",
        "conference-presentation",
        "conference-proceedings",
        "conference proceeding",
        "conference paper",
        "conference abstract",
        "conference poster",
        "conference presentation",
        "proceedings",
    }

    if work_type in conference_types:
        return True

    combined_type = (
        work_type
        .replace("_", "-")
        .replace(" ", "-")
    )

    if "conference" in combined_type:
        return True

    if (
        "congreso" in combined_type
        or "congress" in combined_type
    ):
        return True

    keywords = (
        "conference",
        "congress",
        "congreso",
        "symposium",
        "symposio",
        "workshop",
        "meeting",
        "proceedings",
        "abstract book",
        "libro de resúmenes",
        "libro de resumenes",
        "comunicación oral",
        "comunicacion oral",
        "póster",
        "poster",
    )

    text_to_check = (
        f"{title} {journal}"
    ).lower()

    if any(
        keyword in text_to_check
        for keyword in keywords
    ):
        return True

    return False


def is_other_work(pub):
    """
    Devuelve True únicamente para trabajos que
    no están cubiertos por nuestros dos extractores
    actuales:
      - artículos
      - congresos
    """
    return (
        not looks_like_article(pub)
        and not looks_like_conference(pub)
    )


# ============================================================
# DEDUPLICACIÓN
# ============================================================

def publication_key(pub):
    doi = clean_text(
        pub.get("doi")
    ).lower()

    if doi:
        return (
            "doi",
            doi,
        )

    pmid = clean_text(
        pub.get("pmid")
    ).lower()

    if pmid:
        return (
            "pmid",
            pmid,
        )

    pmcid = clean_text(
        pub.get("pmcid")
    ).lower()

    if pmcid:
        return (
            "pmcid",
            pmcid,
        )

    title = normalize_title(
        pub.get("title")
    )

    year = clean_text(
        pub.get("year")
    )

    work_type = clean_text(
        pub.get("type")
    ).lower()

    return (
        "fallback",
        title,
        year,
        work_type,
    )


def deduplicate_publications(publications):
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


def bibtex_entry_type(work_type):
    mapping = {
        "book": "book",
        "edited-book": "book",
        "book-chapter": "incollection",
        "dissertation-thesis": "phdthesis",
        "preprint": "misc",
        "working-paper": "techreport",
        "report": "techreport",
        "technical-standard": "misc",
        "software": "software",
        "dataset": "misc",
        "data-set": "misc",
        "data-management-plan": "misc",
        "research-tool": "misc",
        "research-technique": "misc",
        "lecture-speech": "misc",
        "learning-object": "misc",
        "review": "article",
        "book-review": "article",
        "journal-issue": "misc",
        "translation": "misc",
        "annotation": "misc",
        "transcription": "misc",
        "blog-post": "misc",
        "magazine-article": "article",
        "newspaper-article": "article",
        "other": "misc",
    }

    return mapping.get(
        work_type.lower(),
        "misc",
    )


def make_bibtex_key(pub, index):
    authors = pub.get("authors") or []

    if authors:
        first_author = re.sub(
            r"[^A-Za-z0-9]",
            "",
            authors[0],
        ).lower()
    else:
        first_author = "work"

    year = pub.get("year") or "nd"

    return (
        f"{first_author}"
        f"{year}"
        f"{index}"
    )


def publication_to_bibtex(pub, index):
    work_type = clean_text(
        pub.get("type")
    )

    entry_type = bibtex_entry_type(
        work_type
    )

    key = make_bibtex_key(
        pub,
        index,
    )

    authors = pub.get("authors") or []

    author_text = " and ".join(
        bibtex_escape(author)
        for author in authors
    )

    lines = [
        f"@{entry_type}{{{key},",
    ]

    if pub.get("title"):
        lines.append(
            "  title = "
            f"{{{bibtex_escape(pub['title'])}}},"
        )

    if author_text:
        lines.append(
            "  author = "
            f"{{{author_text}}},"
        )

    if pub.get("journal"):
        if entry_type == "article":
            field_name = "journal"
        else:
            field_name = "booktitle"

        lines.append(
            f"  {field_name} = "
            f"{{{bibtex_escape(pub['journal'])}}},"
        )

    if pub.get("year"):
        lines.append(
            "  year = "
            f"{{{bibtex_escape(pub['year'])}}},"
        )

    if pub.get("doi"):
        lines.append(
            "  doi = "
            f"{{{bibtex_escape(pub['doi'])}}},"
        )

    if pub.get("isbn"):
        lines.append(
            "  isbn = "
            f"{{{bibtex_escape(pub['isbn'])}}},"
        )

    if pub.get("issn"):
        lines.append(
            "  issn = "
            f"{{{bibtex_escape(pub['issn'])}}},"
        )

    if pub.get("url"):
        lines.append(
            "  url = "
            f"{{{bibtex_escape(pub['url'])}}},"
        )

    lines.append("}")

    return "\n".join(lines)


def save_bibtex(publications, filename):
    entries = []

    for index, pub in enumerate(
        publications,
        start=1,
    ):
        entries.append(
            publication_to_bibtex(
                pub,
                index,
            )
        )

    with open(
        filename,
        "w",
        encoding="utf-8",
    ) as file:
        file.write(
            "\n\n".join(entries)
        )

        if entries:
            file.write("\n")


# ============================================================
# HTML
# ============================================================

def save_html(publications, filename):
    rows = []

    for pub in publications:
        title = html.escape(
            pub.get("title") or ""
        )

        work_type = html.escape(
            pub.get("type") or ""
        )

        journal = html.escape(
            pub.get("journal") or ""
        )

        year = html.escape(
            pub.get("year") or ""
        )

        researcher = html.escape(
            pub.get("researcher") or ""
        )

        doi = clean_text(
            pub.get("doi")
        )

        url = clean_text(
            pub.get("url")
        )

        if doi:
            doi_url = (
                "https://doi.org/"
                + quote(doi)
            )

            doi_html = (
                f'<a href="{html.escape(doi_url)}" '
                'target="_blank" rel="noopener">'
                f'{html.escape(doi)}'
                "</a>"
            )
        else:
            doi_html = ""

        if url:
            url_html = (
                f'<a href="{html.escape(url)}" '
                'target="_blank" rel="noopener">'
                "Enlace"
                "</a>"
            )
        else:
            url_html = ""

        rows.append(
            "<tr>"
            f"<td>{researcher}</td>"
            f"<td>{work_type}</td>"
            f"<td>{title}</td>"
            f"<td>{journal}</td>"
            f"<td>{year}</td>"
            f"<td>{doi_html}</td>"
            f"<td>{url_html}</td>"
            "</tr>"
        )

    document = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport"
      content="width=device-width, initial-scale=1">
<title>Otros trabajos ORCID</title>

<style>
body {{
    font-family: Arial, sans-serif;
    margin: 30px;
}}

table {{
    border-collapse: collapse;
    width: 100%;
}}

th, td {{
    border: 1px solid #ccc;
    padding: 8px;
    vertical-align: top;
    text-align: left;
}}

th {{
    background: #eee;
}}

a {{
    overflow-wrap: anywhere;
}}
</style>
</head>

<body>

<h1>Otros trabajos ORCID</h1>

<table>
<thead>
<tr>
<th>Investigador</th>
<th>Tipo</th>
<th>Título</th>
<th>Revista / colección</th>
<th>Año</th>
<th>DOI</th>
<th>URL</th>
</tr>
</thead>

<tbody>
{"".join(rows)}
</tbody>

</table>

</body>
</html>
"""

    with open(
        filename,
        "w",
        encoding="utf-8",
    ) as file:
        file.write(document)


# ============================================================
# INVESTIGADORES
# ============================================================

def load_researchers(filename):
    with open(
        filename,
        "r",
        encoding="utf-8",
    ) as file:
        data = json.load(file)

    if isinstance(data, dict):
        for key in (
            "researchers",
            "investigadores",
            "people",
        ):
            if key in data:
                data = data[key]
                break

    if not isinstance(data, list):
        raise ValueError(
            "researchers1.json debe contener "
            "una lista de investigadores."
        )

    return data


def get_researcher_name(researcher):
    if not isinstance(researcher, dict):
        return "Investigador"

    for key in (
        "name",
        "nombre",
        "full_name",
        "fullname",
    ):
        if researcher.get(key):
            return clean_text(
                researcher[key]
            )

    return "Investigador"


def get_researcher_orcid(researcher):
    if not isinstance(researcher, dict):
        return ""

    for key in (
        "orcid",
        "ORCID",
        "orcid_id",
        "orcidId",
    ):
        value = researcher.get(key)

        if value:
            value = clean_text(value)

            value = re.sub(
                r"^https?://orcid\.org/",
                "",
                value,
                flags=re.IGNORECASE,
            )

            return value.strip()

    return ""


# ============================================================
# PROCESAR INVESTIGADOR
# ============================================================

def process_researcher(
    researcher,
    position,
    total,
):
    name = get_researcher_name(
        researcher
    )

    orcid = get_researcher_orcid(
        researcher
    )

    print()
    print(
        f"[{position}/{total}] {name}"
    )

    print(
        f"      ORCID: {orcid}"
    )

    if not orcid:
        print(
            "      ERROR: investigador "
            "sin ORCID."
        )
        return []

    groups = get_orcid_work_groups(
        orcid
    )

    summaries = extract_all_summaries(
        groups
    )

    print(
        f"      Work summaries: "
        f"{len(summaries)}"
    )

    publications = []
    total_summaries = len(summaries)

    for index, summary in enumerate(
        summaries,
        start=1,
    ):
        put_code = summary.get(
            "put-code"
        )

        if put_code is None:
            continue

        if (
            index == 1
            or index % 25 == 0
            or index == total_summaries
        ):
            print(
                f"      Obras: "
                f"{index}/{total_summaries}"
            )

        work = get_orcid_work(
            orcid,
            put_code,
        )

        if work is None:
            work = summary

        try:
            pub = work_to_publication(
                work,
                summary,
            )

            pub["researcher"] = name
            pub["orcid"] = orcid

            publications.append(pub)

        except Exception as error:
            print(
                "      AVISO: error "
                f"procesando put-code "
                f"{put_code}: {error}"
            )

    print(
        f"      Obras recuperadas: "
        f"{len(publications)}"
    )

    return publications


# ============================================================
# GUARDAR JSON
# ============================================================

def save_json(data, filename):
    with open(
        filename,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# MAIN
# ============================================================

def main():
    start_time = time.time()

    researchers = load_researchers(
        INPUT_FILE
    )

    print(
        f"Investigadores encontrados: "
        f"{len(researchers)}"
    )

    all_publications = []

    for position, researcher in enumerate(
        researchers,
        start=1,
    ):
        publications = process_researcher(
            researcher,
            position,
            len(researchers),
        )

        all_publications.extend(
            publications
        )

    print()
    print("=" * 70)
    print("PROCESAMIENTO TERMINADO")
    print("=" * 70)

    print(
        "Trabajos recuperados de ORCID: "
        f"{len(all_publications)}"
    )

    # Guardamos absolutamente todo lo recuperado
    # antes de aplicar ningún filtro.
    save_json(
        all_publications,
        OUTPUT_ALL,
    )

    unique_publications = (
        deduplicate_publications(
            all_publications
        )
    )

    print(
        "Trabajos tras deduplicación: "
        f"{len(unique_publications)}"
    )

    other_works = []

    for pub in unique_publications:
        if is_other_work(pub):
            other_works.append(pub)

    # Orden cronológico descendente
    other_works.sort(
        key=lambda pub: (
            -int(pub["year"])
            if str(pub.get("year", "")).isdigit()
            else 0,
            pub.get("title", "").lower(),
        )
    )

    save_json(
        other_works,
        OUTPUT_JSON,
    )

    # ========================================================
    # AGRUPAR POR TIPO
    # ========================================================

    by_type = {}

    for pub in other_works:
        work_type = (
            clean_text(pub.get("type"))
            or "unknown"
        )

        by_type.setdefault(
            work_type,
            [],
        ).append(pub)

    save_json(
        by_type,
        OUTPUT_BY_TYPE,
    )

    # ========================================================
    # BIBTEX Y HTML
    # ========================================================

    save_bibtex(
        other_works,
        OUTPUT_BIB,
    )

    save_html(
        other_works,
        OUTPUT_HTML,
    )

    elapsed = (
        time.time()
        - start_time
    )

    # ========================================================
    # RESUMEN
    # ========================================================

    type_counter = Counter(
        clean_text(pub.get("type"))
        or "unknown"
        for pub in other_works
    )

    print()
    print("=" * 70)
    print("RESULTADOS")
    print("=" * 70)

    print(
        f"Trabajos ORCID:          "
        f"{len(all_publications)}"
    )

    print(
        f"Tras deduplicación:      "
        f"{len(unique_publications)}"
    )

    print(
        f"Otros trabajos incluidos:"
        f" {len(other_works)}"
    )

    print(
        f"Tiempo total:            "
        f"{elapsed:.1f} segundos"
    )

    print()
    print("TIPOS ENCONTRADOS")
    print("-" * 70)

    for work_type, count in sorted(
        type_counter.items(),
        key=lambda item: (
            -item[1],
            item[0].lower(),
        ),
    ):
        print(
            f"{work_type}: {count}"
        )

    print()
    print("Archivos generados:")
    print(
        f"  - {OUTPUT_ALL}"
    )
    print(
        f"  - {OUTPUT_JSON}"
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
        "NOTA: "
        "este script excluye los trabajos "
        "que ya son clasificados por nuestros "
        "extractores de artículos y congresos."
    )


# ============================================================
# EJECUCIÓN
# ============================================================

if __name__ == "__main__":
    main()
