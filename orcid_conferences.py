
import json
import os
import re
import time
import html
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# CONFIGURACIÓN
# ============================================================

INPUT_FILE = "researchers1.json"

OUTPUT_JSON = "conference_publications.json"
OUTPUT_ALL = "conference_publications_all_orcid.json"
OUTPUT_EXCLUDED = "conference_publications_excluded.json"
OUTPUT_HTML = "conference_publications.html"
OUTPUT_BIB = "conference_publications.bib"

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
# ORCID: OBTENER GRUPOS DE TRABAJOS
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
            print("      AVISO: no hay grupos nuevos.")
            break

        all_groups.extend(new_groups)

        if len(groups) < ROWS_PER_PAGE:
            break

        start += len(groups)

    print(f"      Grupos obtenidos: {len(all_groups)}")

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
    url = f"{API_BASE}/{orcid}/work/{put_code}"
    return safe_get(url)


# ============================================================
# EXTRACCIÓN DE DATOS
# ============================================================

def extract_external_ids(work):
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

        id_type = clean_text(
            item.get("external-id-type")
        ).lower()

        value = clean_text(
            item.get("external-id-value")
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
            doi = doi.replace("doi:", "").strip()
            result["doi"] = doi

        elif id_type in ("pmid", "pubmed"):
            result["pmid"] = value

        elif id_type == "pmcid":
            result["pmcid"] = value

        else:
            result["other_ids"].append({
                "type": id_type,
                "value": value,
            })

    return result


def extract_title(work):
    if not isinstance(work, dict):
        return ""

    title_obj = work.get("title") or {}

    if not isinstance(title_obj, dict):
        return clean_text(title_obj)

    title = title_obj.get("title") or {}

    if isinstance(title, dict):
        return clean_text(title.get("value"))

    return clean_text(title)


def extract_journal(work):
    if not isinstance(work, dict):
        return ""

    journal = work.get("journal-title") or {}

    if isinstance(journal, dict):
        return clean_text(journal.get("value"))

    return clean_text(journal)


def extract_url(work):
    if not isinstance(work, dict):
        return ""

    url_obj = work.get("url")

    if isinstance(url_obj, dict):
        return clean_text(url_obj.get("value"))

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
            name = clean_text(credit_name.get("value"))
        else:
            name = clean_text(credit_name)

        if name:
            authors.append(name)

    return authors


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

    if not year and date:
        year = date[:4]

    identifiers = extract_external_ids(work)

    if not identifiers["doi"]:
        summary_ids = extract_external_ids(summary)

        for key in ("doi", "pmid", "pmcid"):
            if not identifiers[key] and summary_ids[key]:
                identifiers[key] = summary_ids[key]

    url = extract_url(work) or extract_url(summary)

    authors = extract_authors(work)

    if not authors:
        authors = extract_authors(summary)

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
# CLASIFICACIÓN DE COMUNICACIONES DE CONGRESOS
# ============================================================

def looks_like_conference(pub):
    """
    Identifica posibles comunicaciones de congresos.

    Se incluyen:
    - conference-paper
    - conference-abstract
    - conference proceeding
    - conference presentation
    - tipos cuyo texto mencione conference/congreso
    - registros con palabras clave de congreso en el título o revista

    Se excluyen los artículos de revista claramente identificados.
    """

    if not isinstance(pub, dict):
        return False

    title = clean_text(pub.get("title"))
    journal = clean_text(pub.get("journal"))
    work_type = clean_text(pub.get("type")).lower()

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

    combined_type = work_type.replace("_", "-").replace(" ", "-")

    if "conference" in combined_type:
        return True

    if "congreso" in combined_type or "congress" in combined_type:
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

    text_to_check = f"{title} {journal}".lower()

    if any(keyword in text_to_check for keyword in keywords):
        return True

    return False


# ============================================================
# DEDUPLICACIÓN
# ============================================================

def publication_key(pub):
    doi = clean_text(pub.get("doi")).lower()

    if doi:
        return ("doi", doi)

    pmid = clean_text(pub.get("pmid")).lower()

    if pmid:
        return ("pmid", pmid)

    pmcid = clean_text(pub.get("pmcid")).lower()

    if pmcid:
        return ("pmcid", pmcid)

    return (
        "fallback",
        normalize_title(pub.get("title")),
        clean_text(pub.get("year")),
        normalize_title(pub.get("journal")),
        clean_text(pub.get("orcid")),
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


def make_bibtex_key(pub, index):
    authors = pub.get("authors") or []

    if authors:
        first_author = re.sub(
            r"[^A-Za-z0-9]",
            "",
            authors[0],
        )
    else:
        first_author = "Author"

    year = pub.get("year") or "nd"

    return f"{first_author}{year}{index}"


def publication_to_bibtex(pub, index):
    key = make_bibtex_key(pub, index)

    authors = pub.get("authors") or []

    author_text = " and ".join(
        bibtex_escape(author)
        for author in authors
    )

    lines = [
        f"@inproceedings{{{key},",
    ]

    if pub.get("title"):
        lines.append(
            f"  title = {{{bibtex_escape(pub['title'])}}},"
        )

    if author_text:
        lines.append(
            f"  author = {{{author_text}}},"
        )

    if pub.get("journal"):
        lines.append(
            f"  booktitle = {{{bibtex_escape(pub['journal'])}}},"
        )

    if pub.get("year"):
        lines.append(
            f"  year = {{{bibtex_escape(pub['year'])}}},"
        )

    if pub.get("doi"):
        lines.append(
            f"  doi = {{{bibtex_escape(pub['doi'])}}},"
        )

    if pub.get("url"):
        lines.append(
            f"  url = {{{bibtex_escape(pub['url'])}}},"
        )

    lines.append("}")

    return "\n".join(lines)


def save_bibtex(publications, filename):
    entries = []

    for index, pub in enumerate(publications, start=1):
        entries.append(
            publication_to_bibtex(pub, index)
        )

    with open(filename, "w", encoding="utf-8") as file:
        file.write("\n\n".join(entries))


# ============================================================
# HTML
# ============================================================

def save_html(publications, filename):
    rows = []

    for pub in publications:
        title = html.escape(pub.get("title") or "")
        journal = html.escape(pub.get("journal") or "")
        year = html.escape(pub.get("year") or "")
        work_type = html.escape(pub.get("type") or "")
        researcher = html.escape(pub.get("researcher") or "")

        doi = pub.get("doi") or ""
        url = pub.get("url") or ""

        if doi:
            doi_url = "https://doi.org/" + quote(doi)

            doi_html = (
                f'<a href="{html.escape(doi_url)}" '
                f'target="_blank" rel="noopener">'
                f'{html.escape(doi)}</a>'
            )
        else:
            doi_html = ""

        if url:
            url_html = (
                f'<a href="{html.escape(url)}" '
                f'target="_blank" rel="noopener">'
                "Enlace</a>"
            )
        else:
            url_html = ""

        rows.append(
            "<tr>"
            f"<td>{researcher}</td>"
            f"<td>{title}</td>"
            f"<td>{journal}</td>"
            f"<td>{year}</td>"
            f"<td>{work_type}</td>"
            f"<td>{doi_html}</td>"
            f"<td>{url_html}</td>"
            "</tr>"
        )

    document = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Comunicaciones de congresos</title>
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
<h1>Comunicaciones de congresos</h1>
<table>
<thead>
<tr>
<th>Investigador</th>
<th>Título</th>
<th>Congreso o publicación</th>
<th>Año</th>
<th>Tipo ORCID</th>
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

    with open(filename, "w", encoding="utf-8") as file:
        file.write(document)


# ============================================================
# INVESTIGADORES
# ============================================================

def load_researchers(filename):
    with open(filename, "r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, dict):
        for key in ("researchers", "investigadores", "people"):
            if key in data:
                data = data[key]
                break

    if not isinstance(data, list):
        raise ValueError(
            "researchers1.json debe contener una lista de investigadores."
        )

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
    summaries = extract_all_summaries(groups)

    print(f"      Work summaries: {len(summaries)}")

    publications = []
    total_summaries = len(summaries)

    for index, summary in enumerate(summaries, start=1):
        put_code = summary.get("put-code")

        if put_code is None:
            continue

        if (
            index == 1
            or index % 25 == 0
            or index == total_summaries
        ):
            print(f"      Obras: {index}/{total_summaries}")

        work = get_orcid_work(orcid, put_code)

        if work is None:
            work = summary

        try:
            pub = work_to_publication(work, summary)

            pub["researcher"] = name
            pub["orcid"] = orcid

            publications.append(pub)

        except Exception as error:
            print(
                f"      AVISO: error procesando "
                f"put-code {put_code}: {error}"
            )

    print(f"      Obras recuperadas: {len(publications)}")

    return publications


# ============================================================
# GUARDAR JSON
# ============================================================

def save_json(data, filename):
    with open(filename, "w", encoding="utf-8") as file:
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

    researchers = load_researchers(INPUT_FILE)

    print(
        f"Investigadores encontrados: {len(researchers)}"
    )

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

    print(
        f"Trabajos recuperados de ORCID: {len(all_publications)}"
    )

    save_json(all_publications, OUTPUT_ALL)

    unique_publications = deduplicate_publications(
        all_publications
    )

    print(
        f"Trabajos después de deduplicar: "
        f"{len(unique_publications)}"
    )

    conferences = []
    excluded = []

    for pub in unique_publications:
        if looks_like_conference(pub):
            conferences.append(pub)
        else:
            excluded.append(pub)

    save_json(excluded, OUTPUT_EXCLUDED)
    save_json(conferences, OUTPUT_JSON)

    save_bibtex(conferences, OUTPUT_BIB)
    save_html(conferences, OUTPUT_HTML)

    elapsed = time.time() - start_time

    print()
    print("=" * 70)
    print("RESULTADOS")
    print("=" * 70)

    print(f"Trabajos ORCID:        {len(all_publications)}")
    print(f"Tras deduplicación:    {len(unique_publications)}")
    print(f"Congresos incluidos:   {len(conferences)}")
    print(f"Trabajos excluidos:    {len(excluded)}")
    print(f"Tiempo total:          {elapsed:.1f} segundos")

    print()
    print("Archivos generados:")
    print(f"  - {OUTPUT_ALL}")
    print(f"  - {OUTPUT_EXCLUDED}")
    print(f"  - {OUTPUT_JSON}")
    print(f"  - {OUTPUT_BIB}")
    print(f"  - {OUTPUT_HTML}")

    print()
    print("IMPORTANTE:")

    print(
        f"Si una publicación aparece en {OUTPUT_ALL} "
        f"pero no en {OUTPUT_BIB}, ha sido excluida "
        "por la clasificación."
    )

    print(
        f"Si no aparece en {OUTPUT_ALL}, el problema "
        "está en la recuperación desde ORCID."
    )


# ============================================================
# EJECUCIÓN
# ============================================================

if __name__ == "__main__":
    main()
