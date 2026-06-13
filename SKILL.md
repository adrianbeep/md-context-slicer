---
name: md-context-slicer
description: "Trigger: md-context-slicer, segmentar libro, separar capitulos, split book, parsear capitulos, extraer capitulos md, split md book, book splitter, dividir capitulos. Segmenta un libro en formato Markdown a partir de Form Feeds (saltos de página del PDF) o expresiones regulares y extrae sus capítulos a archivos individuales para optimizar el contexto de los agentes de IA."
license: MIT
metadata:
  author: adrianbeep
  version: "1.0"
---

# MD Context Slicer

## Problem It Solves

When reading large technical books converted from PDF to Markdown (via tools like Microsoft's MarkItDown), the resulting files are massive (often 1MB+ and 25,000+ lines). Loading these complete files into an LLM's context window is extremely wasteful and quickly inflates the input token count. 

This skill automates the division of large Markdown books into separate, clean files per chapter using Form Feed (`\x0c`) delimiters or structural regexes, ensuring you can read and analyze only the specific chapters required for a task.

## Activation Contract

Trigger this skill whenever the user requests to split a book into chapters, segment a book file, or parse chapters from a converted `.md` document.

### Execution Steps

1. **Identify Inputs**: Find the path of the input Markdown book file (e.g. `Fuentes/progit.md`) and confirm the destination output directory.
2. **Execute Parser Script**: Run the python helper script with the appropriate strategy:
   ```bash
   python3 "${SKILL_DIR:-.}/assets/split.py" --file <path_to_book.md> --output <path_to_destination_folder> --strategy <auto|prefix|numbered|titles> [--encoding <encoding>]
   ```
   * **Strategies**:
     * `auto` (Default): Automatically detects if the book uses H1/H2 prefaces (`prefix` mode) or standalone chapter numbers (`numbered` mode).
     * `prefix`: Looks for explicit chapter indicators (e.g., `Chapter 1`, `Capítulo 02`).
     * `numbered`: Handles books where pages start with a standalone number and the next line contains the title (e.g. *How Linux Works*).
     * `titles`: Matches page starts against a specified list of chapter names. Ideal for books without chapter prefixes (e.g. *Pro Git*). Use with `--titles` (comma-separated list) or `--titles-file` (file path).
   * **Options**:
     * `-e`, `--encoding`: Specifies the input file encoding (e.g., `latin-1`, `windows-1252`). Defaults to `utf-8`.
3. **Format Titles**: The script automatically detects chapters, handles multi-line titles, sanitizes filenames, normalizes letter-spaced headers, and prepends a proper `#` (H1) Markdown header.
4. **Exclude Noise**: The script ignores standard book overhead such as prefaces, licenses, and tables of contents, writing only active chapters.

## Requirements

- **Python 3.9+** (No external dependencies required, uses standard library).

## Quality Rules

- **Zero-loss Content**: The body text within each page must remain unmodified during extraction. Only structural Form Feeds and redundant line breaks are cleaned.
- **Strict File Names**: Chapter filenames must follow a padded format (e.g., `Chapter 01 - The Big Picture.md` or `Capítulo 02 - Conceptos Básicos.md`) to maintain alphabetized ordering in filesystem views. Filenames that match Windows reserved words (e.g., `CON`, `NUL`, `AUX`) are automatically prefixed with the chapter indicator to ensure cross-platform compatibility.
- **Isolated Output Directory**: The agent must specify a dedicated, separate folder as the output directory (e.g., `output_folder/book_chapters/`) instead of the root workspace or shared folders. This prevents polluting target directories with numerous individual chapter files.
- **H1 Header Injected**: The first line of each output file must start with `# [Chapter Name]` so it conforms to standard Markdown outline hierarchies.
