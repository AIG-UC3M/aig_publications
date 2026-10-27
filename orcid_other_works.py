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
