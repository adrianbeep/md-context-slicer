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

Once installed, the agent will dynamically trigger this skill when you ask to split a book or extract chapters. It executes the underlying engine:

```bash
python3 assets/split.py --file <path_to_book.md> --output <path_to_destination> --strategy <auto|prefix|numbered|titles> [--encoding <encoding>]
```

### Strategies:
- `auto` (default): Detects chapter prefixes or standalone numbers automatically.
- `prefix`: Looks for explicit headings (e.g. `Chapter 1`, `Capítulo 02`).
- `numbered`: Handles books starting chapters with standalone numbers (e.g. *How Linux Works*).
- `titles`: Matches page starts against a list of titles (using `--titles` or `--titles-file`).

### Options:
- `-e`, `--encoding`: Specifies the input file encoding (e.g., `utf-8`, `latin-1`, `windows-1252`).

## License

MIT
