    for name, raw_value in entry["fields"].items():
        lines.append(f"  {name} = {raw_value},")
    if len(lines) > 1:
        lines[-1] = lines[-1].rstrip(",")
    lines.append("}")
    return "\n".join(lines)


def merge_entries(orcid_entries: list[dict], manual_entries: list[dict]) -> list[dict]:
    merged = []
    positions = {}

    # Insertar primero ORCID; luego la entrada manual completa los campos y
    # tiene prioridad en caso de discrepancia.
    for source, entries in (("orcid", orcid_entries), ("manual", manual_entries)):
        for entry in entries:
            entry["origin"] = source
            key = dedupe_key(entry)
            if key not in positions:
                positions[key] = len(merged)
                merged.append({
                    "type": entry["type"],
                    "key": entry["key"],
                    "fields": dict(entry["fields"]),
                    "origin": source,
                })
                continue

            existing = merged[positions[key]]
            if source == "manual":
                # Conserva los datos manuales; rellena los campos ausentes con ORCID.
                combined = dict(entry["fields"])
                for field, value in existing["fields"].items():
                    if field not in combined or not value_text(combined[field]):
                        combined[field] = value
                existing["fields"] = combined
                existing["type"] = entry["type"] or existing["type"]
                existing["key"] = entry["key"] or existing["key"]
                existing["origin"] = "manual+orcid"
            else:
                for field, value in entry["fields"].items():
                    if field not in existing["fields"] or not value_text(existing["fields"][field]):
                        existing["fields"][field] = value

    # Orden cronológico descendente y luego título.
    merged.sort(key=lambda e: (
        -(int(entry_year(e)) if entry_year(e).isdigit() else 0),
        normalize_title(e["fields"].get("title", "")),
    ))
    return merged


def field_display(entry: dict, name: str) -> str:
    return html.unescape(value_text(entry["fields"].get(name, "")))


def make_html(entries: list[dict]) -> str:
    chunks = [
        "<!doctype html>",
        '<html lang="es"><head><meta charset="utf-8">',
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>Congresos y comunicaciones</title>",
        "<style>body{font-family:Arial,sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem;line-height:1.5}"
        "li{margin:0 0 1rem} .meta{color:#555;font-size:.95em}</style>",
        "</head><body><h1>Congresos y comunicaciones</h1><ol>",
    ]
    for entry in entries:
        fields = entry["fields"]
        title = field_display(entry, "title") or entry["key"]
        authors = field_display(entry, "author")
        year = field_display(entry, "year")
        venue = field_display(entry, "booktitle") or field_display(entry, "journal")
        url = field_display(entry, "url")
        doi = normalize_doi(fields.get("doi", ""))
        if not url and doi:
            url = "https://doi.org/" + doi

        meta = " · ".join(x for x in [authors, venue, year] if x)
        title_html = html.escape(title)
        if url.startswith(("https://", "http://")):
            title_html = f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{title_html}</a>'

        chunks.append("<li>")
        chunks.append(f"<strong>{title_html}</strong>")
        if meta:
            chunks.append(f'<div class="meta">{html.escape(meta)}</div>')
        chunks.append("</li>")
    chunks.extend(["</ol></body></html>"])
    return "\n".join(chunks)


def main() -> None:
    if not ORCID_BIB.exists():
        raise FileNotFoundError(
            "No existe conference_publications.bib. Ejecuta primero orcid_conferences.py."
        )
    if not MANUAL_BIB.exists():
        raise FileNotFoundError(
            "No existe conferences_and_meetings.bib. Añade el archivo al repositorio."
        )

    orcid_text = read_text(ORCID_BIB)
    manual_text = read_text(MANUAL_BIB)

    orcid_entries = parse_bibtex(orcid_text)
    manual_entries = parse_bibtex(manual_text)

    # Conservar una copia de la salida original de ORCID antes de sustituirla.
    shutil.copyfile(ORCID_BIB, ORCID_BACKUP)

    merged = merge_entries(orcid_entries, manual_entries)
    OUTPUT_BIB.write_text(
        "\n\n".join(render_entry(entry) for entry in merged) + ("\n" if merged else ""),
        encoding="utf-8",
    )
    OUTPUT_HTML.write_text(make_html(merged), encoding="utf-8")

    print(f"Entradas ORCID: {len(orcid_entries)}")
    print(f"Entradas manuales: {len(manual_entries)}")
    print(f"Entradas finales únicas: {len(merged)}")
    print(f"BibTeX combinado: {OUTPUT_BIB}")
    print(f"Copia ORCID original: {ORCID_BACKUP}")
    print(f"HTML combinado: {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
