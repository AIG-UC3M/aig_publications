import os
import sys
import re

def find_input_bib_file():
    """Busca el archivo .bib en el directorio actual."""
    candidates = [
        "publications.bib", 
        "conference_publications.bib",
        "other_works.bib"
        "input.bib"
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
            
    bib_files = [f for f in os.listdir('.') if f.endswith('.bib') and not f.endswith('_clean.bib')]
    if bib_files:
        return bib_files[0]
        
    return None

def normalize_specific_author(author):
    """
    Normaliza variaciones conocidas de los 5 autores objetivo hacia su firma oficial.
    """
    clean_a = author.strip()
    # Normalizar espacios múltiples
    clean_a = re.sub(r'\s+', ' ', clean_a)
    
    # 1. Fernando Díaz de María
    if re.search(r'D[ií]az\s*de\s*Mar[ií]a', clean_a, re.IGNORECASE):
        return "Díaz-de-María, F."
        
    # 2. Ascensión Gallardo Antolín
    if re.search(r'Gallardo\s*[-_]?\s*Antol[ií]n', clean_a, re.IGNORECASE):
        return "Gallardo-Antolín, A."
        
    # 3. Carmen Peláez Moreno
    if re.search(r'Pel[aá]ez\s*[-_]?\s*Moreno', clean_a, re.IGNORECASE):
        return "Peláez-Moreno, C."
        
    # 4. Iván González Díaz
    # Captura variaciones con un solo apellido o ambos ("Ivan Gonzalez", "González Díaz, Iván", etc.)
    if re.search(r'Gonz[aá]lez(\s*D[ií]az)?', clean_a, re.IGNORECASE) and re.search(r'(Iv[aá]n|I\.)', clean_a, re.IGNORECASE):
        return "González-Díaz,I."
        
    # 5. Miguel Ángel Fernández Torres
    if re.search(r'Fern[aá]ndez\s*Torres', clean_a, re.IGNORECASE):
        return "Fernández-Torres, M. A."

    # Si no es uno de los 5 objetivos principales, aplicar normalización genérica
    return generic_clean_author(clean_a)

def format_single_name(name_str):
    """Convierte un nombre a iniciales (ej: 'Juan Manuel' -> 'J. M.')."""
    tokens = name_str.split()
    initials = []
    for tok in tokens:
        if re.match(r'^[A-ZÀ-Ý]\.$', tok, re.IGNORECASE):
            initials.append(tok.upper())
        elif '.' in tok:
            sub_toks = [f"{t.strip('.').upper()}." for t in tok.split('.') if t]
            initials.extend(sub_toks)
        else:
            clean_tok = re.sub(r'[^a-zA-ZáéíóúÁÉÍÓÚñÑüÜàèìòùÀÈÌÒÙ]', '', tok)
            if clean_tok:
                initials.append(f"{clean_tok[0].upper()}.")
    return " ".join(initials)

def generic_clean_author(author_str):
    """Normalización general para el resto de autores."""
    if ',' in author_str:
        parts = author_str.split(',', 1)
        last_name = parts[0].strip()
        first_names = parts[1].strip()
        formatted_initials = format_single_name(first_names)
        return f"{last_name}, {formatted_initials}" if formatted_initials else last_name
    else:
        tokens = author_str.split()
        if len(tokens) == 1:
            return tokens[0]
        else:
            last_name = tokens[-1]
            first_names = " ".join(tokens[:-1])
            formatted_initials = format_single_name(first_names)
            return f"{last_name}, {formatted_initials}" if formatted_initials else last_name

def process_author_field(author_field_str):
    """Procesa todo el campo de autores separando por 'and'."""
    # Limpiar saltos de línea y espacios dobles
    clean_field = re.sub(r'\s+', ' ', author_field_str.strip())
    authors = [a.strip() for a in clean_field.split(' and ')]
    normalized_authors = [normalize_specific_author(a) for a in authors if a]
    return " and ".join(normalized_authors)

def process_bibtex_file(input_filename, output_filename):
    print(f"Procesando: '{input_filename}'...")
    with open(input_filename, 'r', encoding='utf-8') as f:
        content = f.read()

    # Expresión regular para reemplazar el campo 'author = {...}' o 'author = "..."'
    def replace_author_match(match):
        author_content = match.group(2)
        cleaned_authors = process_author_field(author_content)
        return f"author = {{{cleaned_authors}}}"

    # Reemplazar todos los campos author en el archivo
    updated_content = re.sub(
        r'author\s*=\s*([\{"])(.*?)([\}"])', 
        replace_author_match, 
        content, 
        flags=re.DOTALL | re.IGNORECASE
    )

    with open(output_filename, 'w', encoding='utf-8') as f:
        f.write(updated_content)

    print(f"¡Listo! Archivo corregido guardado en: '{output_filename}'")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        infile = sys.argv[1]
        outfile = sys.argv[2] if len(sys.argv) > 2 else infile
    else:
        infile = find_input_bib_file()
        outfile = infile if infile else "conference_publications_clean.bib"

    if not infile or not os.path.exists(infile):
        print("Error: No se encontró ningún archivo .bib.")
        sys.exit(1)

    process_bibtex_file(infile, outfile)
