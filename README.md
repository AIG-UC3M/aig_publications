# Sistema de Gestión Bibliográfica Automatizada desde ORCID y OpenAlex

Este repositorio contiene un sistema automatizado desarrollado en Python y GitHub Actions para la extracción, normalización, clasificación, deduplicación y generación de bibliografías académicas en formatos **BibTeX (`.bib`)**, **JSON** y **HTML** a partir de los perfiles de **ORCID** y la API de **OpenAlex**.

---

## 📋 Índice

- [Descripción General](#-descripción-general)
- [Diagrama Explicativo del Flujo del Sistema](#-diagrama-explicativo-del-flujo-del-sistema)
- [Detalle de la Normalización y Deduplicación de Autores](#-detalle-de-la-normalización-y-deduplicación-de-autores)
- [Explicación Detallada del Flujo de GitHub Actions](#-explicación-detallada-del-flujo-de-github-actions)
- [Estructura del Repositorio](#-estructura-del-repositorio)
  - [Scripts en Python (`.py`)](#scripts-en-python-py)
  - [Workflows de GitHub Actions (`.yml`)](#workflows-de-github-actions-yml)
- [Características Principales](#-características-principales)
- [Archivos de Entrada y Salida](#-archivos-de-entrada-y-salida)
  - [Entrada](#entrada)
  - [Salida Generada](#salida-generada)
- [Configuración y Requisitos](#-configuración-y-requisitos)
  - [Requisitos de Python](#requisitos-de-python)
  - [Secretos de GitHub Actions](#secretos-de-github-actions)
- [Guía de Uso](#-guía-de-uso)
  - [Ejecución en Local](#ejecución-en-local)
  - [Ejecución Automatizada en GitHub Actions](#ejecución-automatizada-en-github-actions)
- [Normalización y Deduplicación de Autores](#-normalización-y-deduplicación-de-autores)

---

## 🔍 Descripción General

El sistema permite mantener actualizada automáticamente la producción científica de un grupo de investigadores. A partir de un archivo JSON con los datos y ORCID ID de cada investigador, el sistema realiza solicitudes a las API públicas de ORCID (v3.0) y OpenAlex para:

1. **Recuperar** todas las obras registradas.
2. **Clasificar** los trabajos en publicaciones en revistas, comunicaciones a congresos y otros trabajos académicos (libros, capítulos, informes, preprints, patentes, etc.).
3. **Normalizar** los nombres de los autores y escapar caracteres especiales para garantizar la sintaxis correcta en BibTeX.
4. **Deduplicar** registros mediante DOI, PMID, PMCID o coincidencia de título, año y revista.
5. **Generar** automáticamente archivos `.bib`, `.json` y `.html` listos para su difusión o inclusión en la web institucional.

---


## 🔄 Diagrama Explicativo del Flujo del Sistema

El siguiente diagrama detalla la arquitectura completa del repositorio, ilustrando cómo interactúan los datos de entrada, los workflows de CI/CD (Integración Continua y Despliegue Continuo), las API externas, los scripts de procesamiento y la publicación final:

```
flowchart TD
    subgraph Entrada [Entrada de Datos]
        A[researchers1.json] --> |ORCID IDs / Nombres| B
    end

    subgraph Workflows [GitHub Actions CI/CD]
        Cron[Cron Semanal: Lunes 06:05] --> |Trigger| GHA_Update
        PushBib[Push en **.bib] --> |Trigger| GHA_Norm
        GHA_Update[Workflows update-*.yml]
        GHA_Norm[Workflow normalize_bib.yml]
    end

    subgraph Auth [Autenticación & API Client]
        GHA_Update --> |OAuth POST| OAuth[API ORCID /oauth/token]
        OAuth --> |ORCID_ACCESS_TOKEN| B[Scripts Python: orcid_*.py / generate_bibtex.py]
        B --> |Peticiones REST v3.0| ORCID_API[API Pública ORCID OpenAlex]
    end

    subgraph Process [Procesamiento y Normalización]
        ORCID_API -->;|JSON crudo| C[Clasificación por Tipo de Obra]
        C --> D[Normalización de Autores]
        D --> |1. Mapeo por ORCID ID| D1[Nombre Canónico]
        D --> |2. Fallback Firma/Regex| D2[Iniciales y Apellidos]
        D --> E[Deduplicación Jerárquica]
        E --> |DOI >; PMID >; PMCID >; Título+Año| F[Escapado de Caracteres BibTeX]
    end

    subgraph NormStep [Normalización Post-Process]
        GHA_Norm --> N1[normalize_bib.py]
        F --> |Genera .bib| N1
        N1 --> |Limpia y estandariza author = {...}| N2[Archivos BibTeX Limpios]
    end

    subgraph Salida [Generación de Artefactos]
        F --> OutJSON[Archivos .json]
        F --> OutHTML[Archivos .html]
        N2 --> OutBIB[Archivos .bib Finales]
        OutJSON ; OutHTML ; OutBIB --> |Git Commit ; Push| Repo[Repositorio GitHub main]
    end

```

---

## 📁 Estructura del Repositorio

```
.
├── .github/workflows/
│   ├── update-publications.yml   # Workflow semanal para artículos de revista
│   ├── update_conferences.yml    # Workflow semanal para actas/comunicaciones de congresos
│   ├── update-other-works.yml    # Workflow semanal para otros trabajos académicos
│   └── normalize_bib.yml         # Workflow disparado al modificar archivos .bib
│
├── orcid_publications.py              # Extracción y procesado de artículos de revista desde ORCID
├── orcid_conferences.py               # Extracción y procesado de ponencias/congresos desde ORCID
├── orcid_other_works.py               # Extracción y procesado de otros trabajos desde ORCID
├── generate_bibtex.py                 # Extracción y generación de BibTeX desde OpenAlex API
├── normalize_bib.py                   # Limpieza y normalización de firmas de autores en BibTeX
│
├── researchers1.json                  # Archivo de entrada con el listado de investigadores y ORCIDs
└── README.md                          # Documentación del proyecto
```

### Scripts en Python (`.py`)

- **`orcid_publications.py`**:
  - Conecta a la API v3.0 de ORCID.
  - Procesa la lista de investigadores de `researchers1.json`.
  - Filtra artículos de revista e implemta deduplicación y normalización.
  - Genera `publications.bib`, `publications.json` y `publications.html`.

- **`orcid_conferences.py`**:
  - Extrae comunicaciones, ponencias, pósteres y resúmenes de congresos desde ORCID.
  - Implementa filtros específicos (`looks_like_conference`).
  - Genera `conference_publications.bib`, `conference_publications.json` y `conference_publications.html`.

- **`orcid_other_works.py`**:
  - Recupera libros, capítulos, informes, preprints, patentes y datasets desde ORCID.
  - Genera `other_works.bib`, `other_works.json` y `other_works.html`.

- **`generate_bibtex.py`**:
  - Consulta la API de OpenAlex para obtener publicaciones por ORCID.
  - Descarta preprints y repositorios de datos (Zenodo, arXiv, Figshare, OSF).
  - Genera `publications.bib` con claves únicas autor-año.

- **`normalize_bib.py`**:
  - Normaliza firmas de autores en archivos `.bib` a iniciales y apellidos oficiales.

---

## 🛠️ Configuración y Requisitos

### Requisitos de Python

- Python 3.11+
- Librerías: `requests`

### Secretos de GitHub Actions

Configurar en el repositorio (`Settings > Secrets > Actions`):
- `ORCID_CLIENT_ID`
- `ORCID_CLIENT_SECRET`

---


## 👤 Detalle de la Normalización y Deduplicación de Autores

El proceso de normalización de nombres de autores se ejecuta en tres capas complementarias dentro del código para garantizar firmas bibliográficas uniformes y sin errores de sintaxis:

### Capa A: Normalización Directa por ORCID ID y Mapeo Canónico
En la extracción de datos desde ORCID (`orcid_other_works.py`), el sistema prioriza la identidad única del investigador antes que el texto registrado en el trabajo:
1. **Mapa Canónico por ORCID**: Se construye una tabla `canonical_by_orcid` en la que cada `ORCID ID` extraído de `researchers1.json` se vincula estrictamente a su nombre oficial preferido.
2. **Sustitución Directa**: Al leer los contribuidores de cada obra, si el contribuidor tiene un `contributor-orcid` coincidente, el script reemplaza el nombre escrito por el nombre canónico oficial.
3. **Fallback por Firma de Nombre**: Si el registro en ORCID carece del ID del coautor, se genera una firma conservadora (`name_signature`) basada en la primera inicial y el primer apellido normalizado para identificar al investigador.

### Capa B: Mapeo por Diccionario de Variantes
En `orcid_publications.py` y `orcid_conferences.py`, se utiliza una función de normalización de claves (`normalize_author_key`) que elimina tildes, diacríticos, mayúsculas y guiones, mapeando variantes frecuentes a su firma oficial.

### Capa C: Normalización Estricta y Escapado en BibTeX
Cuando se procesan o refinan los archivos `.bib`, el script `normalize_bib.py` realiza un escaneo de los campos `author = {...}` separando cada autor por el delimitador `and`:
1. **Captura por Expresión Regular**: Para los investigadores principales, evalúa patrones de búsqueda flexibilizados (ej. `Díaz de María`) y reescribe la entrada exactamente como la firma oficial.
2. **Formateo Genérico de Coautores**: Para el resto de autores no principales (`generic_clean_author`), convierte los nombres de pila en iniciales en mayúsculas (ej. "Juan Manuel" -> "J. M.") y los estructura bajo el formato estándar `Apellido, Iniciales`.
3. **Escapado Seguro de BibTeX**: Escapa caracteres reservados en LaTeX/BibTeX (tales como `&`, `%`, `#`, `_`, `{`, `}`) agregando barras invertidas para evitar fallos de sintaxis en procesadores bibliográficos.

### Tabla de Mapeo de Investigadores Principales

| Nombre Canónico | Variantes Detectadas y Normalizadas |
| :--- | :--- |
| **Díaz-de-María, F.** | `Fernando Díaz de María`, `Diaz-De-Maria, F.`, `Fernando, D.-D.-M.` |
| **Gallardo-Antolín, A.** | `Ascensión Gallardo Antolín`, `Gallardo_Antolín, A.`, `ASCENSIÓN GALLARDO-ANTOLÍN` |
| **Peláez-Moreno, C.** | `Carmen Peláez Moreno`, `Carmen Pelaez-Moreno`, `Carmen Peláez` |
| **González-Díaz, I.** | `Iván González Díaz`, `González Díaz, Iván`, `Gonzalez-Diaz, I.` |
| **Fernández-Torres, M. A.** | `Miguel Ángel Fernández Torres`, `Fernández Torres, M.A.` |

---


## ⚙️ Explicación Detallada del Flujo de GitHub Actions

La automatización de la integración continua (CI/CD) se organiza mediante cuatro workflows YAML dentro de `.github/workflows/`:

### Eventos Disparadores (`on:`)
- **Programación Semanal (`schedule`)**: Los workflows `update-publications.yml`, `update_conferences.yml` y `update-other-works.yml` están configurados mediante expresiones cron (`cron: "5 6 * * 1"`) para ejecutarse automáticamente todos los **lunes a las 06:05 AM** (hora de Europa/Madrid).
- **Ejecución Manual (`workflow_dispatch`)**: Permite a cualquier administrador desencadenar los workflows bajo demanda desde la pestaña *Actions* de GitHub.
- **Disparador por Cambio de Archivo (`push`)**: El workflow `normalize_bib.yml` se activa automáticamente en cualquier evento `push` hacia la rama `main` que modifique o cree archivos con extensión `**.bib`.

### Secuencia de Pasos en el Runner
Cada runner de GitHub Actions (`ubuntu-latest` / `ubuntu-24.04`) ejecuta la siguiente secuencia ordenada:

1. **Descarga del Repositorio**: Descarga el historial completo del código fuente usando `actions/checkout@v6`.
2. **Configuración del Entorno Python**: Inicializa el entorno Python 3.11/3.x (`actions/setup-python@v7`) e instala la librería `requests` mediante `pip`.
3. **Obtención Dinámica del Token OAuth de ORCID**:
   - Accede a los secretos guardados en el repositorio (`secrets.ORCID_CLIENT_ID` y `secrets.ORCID_CLIENT_SECRET`).
   - Ejecuta un script inline de Python que realiza un `POST` a la API de ORCID solicitando un token de acceso público (`/read-public`).
   - Exporta el token obtenido a la variable de entorno del flujo `GITHUB_ENV` como `ORCID_ACCESS_TOKEN`.
4. **Procesamiento y Filtrado**: Ejecuta el script correspondiente (`orcid_publications.py`, `orcid_conferences.py`, `orcid_other_works.py` o `normalize_bib.py`).
5. **Control de Concurrencia y Sincronización Git**:
   - Utiliza la directiva `concurrency: group: actualizar-main` para evitar ejecuciones simultáneas que pudieran sobreescribir la rama `main`.
   - Configura la identidad del bot (`github-actions[bot]`).
   - Verifica si existen cambios con `git diff --cached --quiet`. Si hay archivos modificados o creados, realiza un `git commit`, ejecuta un `git pull --rebase origin main` para evitar conflictos y publica los resultados con `git push origin main`.

### Control de Concurrencia y Permisos
- **`permissions: contents: write`**: Otorga al bot de GitHub Actions permisos de escritura para realizar commits y pushes sobre la rama principal.
- **`concurrency`**: Asegura que si dos tareas se disparan a la vez, se ejecuten en serie para prevenir colisiones de git rebase.

---

