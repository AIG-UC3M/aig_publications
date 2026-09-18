import json
import re
import requests
from collections import defaultdict

OPENALEX = "https://api.openalex.org/works"
OUTPUT_BIB = "publications.bib"


def normalize_spaces(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def normalize_doi(doi):
    if not doi:
        return ""

    doi = str(doi).strip()
    doi = re.sub(
        r"^https?://(dx\.)?doi\.org/",
        "",
        doi,
        flags=re.IGNORECASE,
    )
    doi = re.sub(
        r"^doi:\s*",
        "",
        doi,
        flags=re.IGNORECASE,
    )

    return doi.strip().rstrip(").,;")


def normalize_author_name(name):
    if not name:
        return ""

    return (
        normalize_spaces(name)
        .replace("‐", "-")
        .replace("–", "-")
        .replace("—", "-")
    )


def make_bibtex_key(authors, year):
    first_author = (
        normalize_author_name(authors[0])
        if authors
        else "publication"
    )

    surname = first_author.split()[-1]
    surname = re.sub(r"[^A-Za-z0-9]", "", surname).lower()

    return f"{surname}{year}"


def make_bibtex(
    title,
    authors,
    journal,
    year,
    volume="",
    issue="",
    pages="",
    doi="",
    key=None,
):
    if key is None:
        key = make_bibtex_key(authors, year)

    lines = [
        f"@article{{{key},",
        f"  title = {{{title}}},",
        "  author = {" + " and ".join(authors) + "},",
        f"  journal = {{{journal}}},",
    ]

    if volume:
        lines.append(f"  volume = {{{volume}}},")

    if issue:
        lines.append(f"  number = {{{issue}}},")

    if pages:
        lines.append(
            f"  pages = {{{pages.replace('-', '--')}}},"
        )

    lines.append(f"  year = {{{year}}},")

    if doi:
        lines.append(f"  doi = {{{doi}}}")

    lines.append("}")

    return "\n".join(lines)


def is_repository_doi(doi):
    if not doi:
        return False

    excluded = [
        "10.5281/zenodo.",
        "10.48550/arxiv.",
        "10.17632/",
        "10.6084/m9.figshare.",
        "10.31219/osf.io/",
    ]

    doi_lower = doi.lower()

    return any(
        doi_lower.startswith(prefix)
        for prefix in excluded
    )


def get_works(orcid):
    params = {
        "filter": f"author.orcid:{orcid},type:article",
        "per-page": 100,
    }

    response = requests.get(
        OPENALEX,
        params=params,
        timeout=60,
    )

    response.raise_for_status()

    return response.json().get("results", [])


def work_to_publication(work):
    title = normalize_spaces(
        work.get("display_name")
        or work.get("title")
        or ""
    )

    year = (
        work.get("publication_year")
        or work.get("year")
        or ""
    )

    doi = normalize_doi(work.get("doi") or "")

    if is_repository_doi(doi):
        return None

    authors = []

    for authorship in work.get("authorships", []):
        author = authorship.get("author") or {}
        display_name = normalize_author_name(
            author.get("display_name") or ""
        )

        if display_name:
            authors.append(display_name)

    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}

    journal = normalize_spaces(
        source.get("display_name") or ""
    )

    biblio = work.get("biblio") or {}

    volume = normalize_spaces(biblio.get("volume") or "")
    issue = normalize_spaces(biblio.get("issue") or "")
    first_page = normalize_spaces(biblio.get("first_page") or "")
    last_page = normalize_spaces(biblio.get("last_page") or "")

    if first_page and last_page:
        pages = f"{first_page}-{last_page}"
    elif first_page:
        pages = first_page
    elif last_page:
        pages = last_page
    else:
        pages = ""

    if "-" in pages:
        parts = [part.strip() for part in pages.split("-")]
        if len(parts) == 2 and parts[0] == parts[1]:
            pages = parts[0]

    key = make_bibtex_key(authors, year)

    bibtex = make_bibtex(
        title=title,
        authors=authors,
        journal=journal,
        year=year,
        volume=volume,
        issue=issue,
        pages=pages,
        doi=doi,
        key=key,
    )

    return {
        "title": title,
        "year": year,
        "doi": doi,
        "authors": authors,
        "journal": journal,
        "volume": volume,
        "issue": issue,
        "pages": pages,
        "bibtex": bibtex,
    }


def deduplicate_publications(publications):
    unique = {}

    for publication in publications:
        doi = normalize_doi(
            publication.get("doi", "")
        ).lower()

        title = normalize_spaces(
            publication.get("title", "")
        ).lower()

        key = f"doi:{doi}" if doi else f"title:{title}"
        unique[key] = publication

    return list(unique.values())


def ensure_unique_bibtex_keys(publications):
    counters = defaultdict(int)

    for publication in publications:
        base_key = make_bibtex_key(
            publication.get("authors", []),
            publication.get("year", ""),
        )

        counters[base_key] += 1
        count = counters[base_key]

        final_key = (
            base_key
            if count == 1
            else f"{base_key}{count}"
        )

        publication["bibtex"] = re.sub(
            r"@article\{[^,]+,",
            f"@article{{{final_key},",
            publication.get("bibtex", ""),
            count=1,
        )

    return publications


def generate_bibtex_file(publications):
    entries = []

    for publication in publications:
        bibtex = publication.get("bibtex", "").strip()

        if bibtex:
            entries.append(bibtex)

    if not entries:
        return ""

    return "\n\n".join(entries) + "\n"


def main():
    print("Leyendo researchers.json...")

    with open(
        "researchers.json",
        "r",
        encoding="utf-8",
    ) as file:
        data = json.load(file)

    researchers = data["researchers"]
    all_publications = []

    for researcher in researchers:
        name = researcher.get("name", "Unknown")
        orcid = researcher.get("orcid", "")

        if not orcid:
            print(f"⚠️ Sin ORCID para {name}")
            continue

        print(
            f"Buscando publicaciones de {name} ({orcid})..."
        )

        try:
            works = get_works(orcid)
        except Exception as exc:
            print(f"❌ Error con {name}: {exc}")
            continue

        print(f"   Encontrados: {len(works)}")

        for work in works:
            publication = work_to_publication(work)

            if publication is not None:
                all_publications.append(publication)

    all_publications = deduplicate_publications(
        all_publications
    )

    all_publications = ensure_unique_bibtex_keys(
        all_publications
    )

    all_publications.sort(
        key=lambda publication: (
            -int(publication.get("year", 0))
            if str(publication.get("year", "")).isdigit()
            else 0,
            publication.get("title", "").lower(),
        )
    )

    print(f"Generando {OUTPUT_BIB}...")

    with open(
        OUTPUT_BIB,
        "w",
        encoding="utf-8",
    ) as file:
        file.write(
            generate_bibtex_file(all_publications)
        )

    print("✅ Proceso terminado")
    print(
        f"   Publicaciones: {len(all_publications)}"
    )
    print(f"   BibTeX: {OUTPUT_BIB}")


if __name__ == "__main__":
    main()
