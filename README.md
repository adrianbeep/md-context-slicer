# md-context-slicer

An Agent Skill to optimize context sizes for AI agents by slicing large Markdown books into atomic chapter files.

## Problem It Solves

When AI agents process technical books or massive Markdown documents, loading the entire file easily exhausts context limits and inflates input token costs. 

`md-context-slicer` solves this by programmatically dividing books into clean, separate, and properly structured chapter files (using Form Feed `\x0c` control characters or dynamic structural regexes) so the agent can read and process only the specific chapters required for a task.

## Installation

You can install this skill into your local agent environment using `skills.sh` / `npx skills`:

```bash
npx skills add adrianbeep/md-context-slicer
```

## Structure

This repository follows the Agent Skills standard:

- **`SKILL.md`**: Main manifest and instructions that the agent loads to understand how and when to use this skill.
- **`assets/split.py`**: Highly optimized, memory-efficient Python 3 engine designed to perform the actual file slicing with an $O(1)$ RAM footprint.

## How to Use

Once installed, the agent will dynamically trigger this skill when you ask to split a book or extract chapters. You can also run the underlying engine manually from your terminal:

```bash
python3 assets/split.py --file <path_to_book.md> --output <path_to_destination> --strategy <auto|prefix|numbered|titles> [--encoding <encoding>]
```

### Command Line Examples:
- **Auto-detect strategy:**
  ```bash
  python3 assets/split.py -f book.md -o output/
  ```
- **Force a specific strategy (e.g., numbered chapters) with Latin-1 encoding:**
  ```bash
  python3 assets/split.py -f book.md -o output/ -s numbered -e latin-1
  ```
- **Provide a list of manual titles to split by:**
  ```bash
  python3 assets/split.py -f book.md -o output/ -s titles --titles "Introduction,Core Concepts,Summary"
  ```

### Strategies:
- `auto` (default): Automatically scans the first 50 pages and decides if the book uses headings (`prefix` mode) or standalone numbers (`numbered` mode).
- `prefix`: Looks for explicit chapter indicators (e.g. `Chapter 1`, `Capítulo 02`).
- `numbered`: Handles books where pages start with a standalone number and the next line contains the title.
- `titles`: Matches page starts against a specified list of chapter names. Ideal for books without chapter prefixes (e.g. *Pro Git*).

### Options:
- `-e`, `--encoding`: Specifies the input file encoding (e.g., `utf-8`, `latin-1`, `windows-1252`). Defaults to `utf-8`.
- `-t`, `--titles`: Comma-separated list of chapter titles (used with `titles` strategy).
- `--titles-file`: Path to a text file containing chapter titles, one per line (used with `titles` strategy).
- `--chunk-lines`: Number of lines per file when splitting by size as a fallback (defaults to `2000`).

## Key Features

- **Memory Efficient ($O(1)$ RAM):** Uses Python's native `BufferedReader` to stream files line by line, preventing memory spikes even on huge 100MB+ documents.
- **Bilingual Support (EN/ES):** The parser heuristics (regex connectors, end-of-book markers, chapter prefixes) are pre-configured and optimized specifically for English and Spanish literature.
- **Duplication Protection:** If a book has chapters with duplicate names (e.g. multiple "Summary" sections), it appends incrementing suffixes (`_1`, `_2`) to prevent overwriting.
- **Windows Safety:** Automatically prefix filenames that clash with OS reserved names (`CON`, `PRN`, `AUX`, `NUL`, etc.) to prevent silent file creation errors.
- **OCR Text Normalization:** Automatically joins spaced-out letters created by PDF extractors (e.g., `T H E  B I G` -> `THE BIG`).

## Requirements

- **Python 3.9+** (No external dependencies required, uses standard library).
- **Supported Languages:** English and Spanish (due to grammar-specific heuristics).

## License

MIT
