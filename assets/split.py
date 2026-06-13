#!/usr/bin/env python3
import re
import sys
import argparse
import itertools
import logging
from pathlib import Path
from typing import Optional
from collections.abc import Generator

# Configuration constants for performance tuning and architectural consistency
MAX_SCAN_WINDOW_BYTES = 5 * 1024 * 1024       # 5 MB scanning window for Form Feeds
SCAN_CHUNK_SIZE_BYTES = 65536                 # 64 KB block size for scanning buffer
STRATEGY_SAMPLE_PAGES = 50                    # Number of pages to sample for auto-detection
MAX_CHAPTER_TITLE_LENGTH = 100                # Maximum characters for a valid chapter title
CONFIDENCE_THRESHOLD = 3                      # Confidence threshold (hits) for early exit
STREAM_IO_BLOCK_SIZE_BYTES = 1048576          # 1 MB block size for streaming file reads
AVG_WORD_LEN_THRESHOLD = 1.5                  # Average length threshold to detect spaced-out letters
MAX_TITLE_LINE_LENGTH = 80                    # Maximum length of a line within a title
SAMPLE_LINES_COUNT = 2                        # Lines to retrieve for strategy sampling
HEADER_SCAN_LINES_COUNT = 10                   # Lines to scan for header checks

# Windows reserved filenames that cannot be written directly to disk
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
}

# Configure logging framework hierarchy
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger("book-splitter")

# Precompiled regular expressions for performance optimization
PREFIX_REGEX = re.compile(r"^(?:#+\s*)?(Chapter|Cap[íi]tulo)\s*(\d+|[IVXLCDMivxlcdm]+)", re.IGNORECASE)
CHAPTER_PREFIX_FULL_REGEX = re.compile(r"^(?:#+\s*)?(Chapter|Cap[íi]tulo)\s*(\d+|[IVXLCDMivxlcdm]+)[\s.:-]*([^\n]*)", re.IGNORECASE)
NUMBERED_REGEX = re.compile(r"^(\d+|[IVXLCDMivxlcdm]+)$", re.IGNORECASE)
NUMBERED_WITH_HEADER_REGEX = re.compile(r"^(?:#+\s*)?(\d+|[IVXLCDMivxlcdm]+)$", re.IGNORECASE)
CONNECTORS_REGEX = re.compile(
    r"\b(?:and|or|of|the|to|in|for|with|a|an|on|at|by|your|my|our|their|its|her|his|y|o|de|del|el|la|los|las|un|una|en|para|con|por|su|sus)\b\s*$",
    re.IGNORECASE
)
MARKDOWN_HEADER_CLEAN_REGEX = re.compile(r'^#+\s*')
MARKDOWN_HEADER_END_CLEAN_REGEX = re.compile(r'\s*#+$')
ILLEGAL_CHARS_REGEX = re.compile(r'[\\/*?:"<>|]')
LINE_MATCH_REGEX = re.compile(r"^([^\r\n]*)(?:\r?\n|$)", re.MULTILINE)
END_OF_BOOK_REGEX = re.compile(r"^(index|bibliography|glosario|glossary|bibliografía|conclusión|epílogo)$", re.IGNORECASE)

class SplitterContext:
    """Manages the state and file operations of the split process to decouple it from page iteration."""
    def __init__(self, output_dir: Path, enhance_subsections: bool = False):
        self.output_dir = output_dir
        self.enhance_subsections = enhance_subsections
        self.current_chapter: Optional[str] = None
        self.buffer_content: list[str] = []
        self.is_writing: bool = False
        self.chapter_count: int = 0

    def start_new_chapter(self, title: str):
        self.flush()
        self.current_chapter = title
        self.chapter_count += 1
        self.buffer_content = [f"# {title}\n"]
        self.is_writing = True
        logger.info(f"Detected chapter: {title}")

    def append_content(self, text: str):
        if self.is_writing:
            self.buffer_content.append(text)

    def stop_writing(self):
        self.is_writing = False

    def flush(self):
        if self.current_chapter and self.buffer_content:
            safe_name = ILLEGAL_CHARS_REGEX.sub("", self.current_chapter).strip()
            safe_name = " ".join(safe_name.split())
            
            # Prevent empty names resulting in orphaned files named '.md'
            safe_name = safe_name if safe_name else f"Chapter_{self.chapter_count:02d}"
            
            # Check against Windows reserved names (case-insensitive) to prevent write failures
            if safe_name.upper() in WINDOWS_RESERVED_NAMES:
                safe_name = f"Chapter_{self.chapter_count:02d}_{safe_name}"
                
            # Truncate long file names to prevent OS errors (max 100 chars)
            if len(safe_name) > 100:
                safe_name = safe_name[:100].strip()
                
            file_path = self.output_dir / f"{safe_name}.md"
            if file_path.exists():
                counter = 1
                while True:
                    if counter > 100:
                        logger.error(f"Too many file naming collisions (>100) for '{safe_name}'. "
                                     "Stopping duplicate suffix search to prevent filesystem locking.")
                        break
                    candidate_path = self.output_dir / f"{safe_name}_{counter}.md"
                    if not candidate_path.exists():
                        file_path = candidate_path
                        break
                    counter += 1
            
            try:
                if self.enhance_subsections:
                    full_text = "".join(self.buffer_content)
                    processed_text = enhance_with_subsection_headers(full_text)
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(processed_text.strip())
                        f.write("\n")
                else:
                    # Memory-efficient streaming write (writes parts directly to avoid massive join duplication in RAM)
                    with open(file_path, "w", encoding="utf-8") as f:
                        first = True
                        for page in self.buffer_content:
                            cleaned_page = page.strip()
                            if cleaned_page:
                                if not first:
                                    f.write("\n\n")
                                f.write(cleaned_page)
                                first = False
                        f.write("\n")
            except PermissionError as e:
                logger.error(f"Permission denied writing chapter file '{file_path}': {e}")
            except FileNotFoundError as e:
                logger.error(f"Output path not found writing chapter file '{file_path}': {e}")
            except OSError as e:
                logger.error(f"Operating system error writing chapter file '{file_path}': {e}")
                
            self.buffer_content = []

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split a Markdown book file into separate chapter files.")
    # Dependency injection: using Path type directly to get initialized Path objects
    parser.add_argument("-f", "--file", required=True, type=Path, help="Path to the input Markdown file.")
    parser.add_argument("-o", "--output", required=True, type=Path, help="Path to the destination directory where chapters will be saved.")
    parser.add_argument("-s", "--strategy", choices=["prefix", "numbered", "titles", "auto"], default="auto",
                        help="Strategy to detect chapters: 'prefix' (Chapter X), 'numbered' (standalone number), 'titles' (list of titles), or 'auto' (detects prefix or numbered).")
    parser.add_argument("-t", "--titles", help="Comma-separated list of chapter titles (used with 'titles' strategy).")
    parser.add_argument("--titles-file", type=Path, help="Path to a text file containing chapter titles, one per line (used with 'titles' strategy).")
    parser.add_argument("--chunk-lines", type=int, default=2000, help="Number of lines per file when splitting by size as a fallback.")
    parser.add_argument("--chunk-size", type=int, default=8000, help="Maximum character size for contingency chunks.")
    parser.add_argument("--overlap-size", type=int, default=400, help="Number of overlap characters for contingency chunks.")
    parser.add_argument("-e", "--encoding", default="utf-8", help="Input file encoding (default: utf-8).")
    parser.add_argument("--enhance-subsections", action="store_true",
                        help="Automatically detect plain-text subsection titles and convert them to ## headings.")
    return parser.parse_args()

def normalize_title(title: str) -> str:
    cleaned = title.strip()
    if not cleaned:
        return ""
    
    cleaned = MARKDOWN_HEADER_CLEAN_REGEX.sub('', cleaned)
    cleaned = MARKDOWN_HEADER_END_CLEAN_REGEX.sub('', cleaned).strip()
    # Optimization: using native split with literal double spaces to isolate potential spaced-out letter blocks
    parts = [p.strip() for p in cleaned.split("  ") if p.strip()]
    normalized_parts = []
    
    for part in parts:
        subparts = part.split()
        if len(subparts) > 1:
            avg_len = sum(len(sp) for sp in subparts) / len(subparts)
            if avg_len < AVG_WORD_LEN_THRESHOLD:
                normalized_parts.append("".join(subparts))
            else:
                normalized_parts.append(" ".join(subparts))
        else:
            normalized_parts.append(part)
            
    result = " ".join(normalized_parts)
    # Optimization: use native string split() to clean any remaining multiple spaces
    return " ".join(result.split())

def get_first_non_empty_lines(page: str, max_lines: int = HEADER_SCAN_LINES_COUNT) -> list[str]:
    """Returns the first max_lines non-empty lines from the page sequentially (saves memory over full splitlines)."""
    lines: list[str] = []
    for line in page.splitlines():
        cleaned = line.strip()
        if cleaned:
            lines.append(cleaned)
            if len(lines) >= max_lines:
                break
    return lines

def detect_best_strategy(pages: list[str]) -> str:
    prefix_count = 0
    numbered_count = 0
    
    for page in pages:
        # Optimization: retrieve only the first SAMPLE_LINES_COUNT non-empty lines dynamically
        lines = get_first_non_empty_lines(page, SAMPLE_LINES_COUNT)
        if not lines:
            continue
        first_line = lines[0]
        
        clean_first_line = MARKDOWN_HEADER_CLEAN_REGEX.sub('', first_line).strip()
        
        if PREFIX_REGEX.match(first_line):
            prefix_count += 1
        elif NUMBERED_REGEX.match(clean_first_line):
            if len(lines) > 1 and len(lines[1]) < MAX_TITLE_LINE_LENGTH:
                numbered_count += 1
                
    logger.info(f"Strategy detection: found {prefix_count} pages with chapter prefix, {numbered_count} pages starting with standalone numbers.")
    
    if prefix_count >= CONFIDENCE_THRESHOLD:
        return "prefix"
    elif numbered_count >= CONFIDENCE_THRESHOLD:
        return "numbered"
    
    return "prefix"

def check_has_form_feed(file_path: Path, encoding: str = "utf-8") -> bool:
    """Check if the file contains Form Feed characters by reading sequentially in blocks up to configured bytes limit."""
    try:
        with open(file_path, "r", encoding=encoding) as f:
            bytes_read = 0
            
            while bytes_read < MAX_SCAN_WINDOW_BYTES:
                chunk = f.read(SCAN_CHUNK_SIZE_BYTES)
                if not chunk:
                    break
                if "\x0c" in chunk:
                    return True
                bytes_read += len(chunk)
        return False
    except UnicodeDecodeError as e:
        logger.error(f"Unicode decode error in check_has_form_feed: {e}")
        raise
    except OSError:
        return False

def build_split_pattern(strategy: str, target_titles: Optional[set[str]] = None) -> Optional[re.Pattern]:
    """Precompile the dynamic regex pattern for virtual page splitting."""
    delimiters = [
        r"^#+\s+(?:Chapter|Cap[íi]tulo)\s*(?:\d+|[IVXLCDMivxlcdm]+)",
        r"^#+\s+[^\n]+"
    ]
    
    if strategy in ("auto", "prefix"):
        delimiters.append(r"^(?:Chapter|Cap[íi]tulo)\s*(?:\d+|[IVXLCDMivxlcdm]+)")
    if strategy in ("auto", "numbered"):
        delimiters.append(r"^(\d+|[IVXLCDMivxlcdm]+)$")
        
    if strategy == "titles" and target_titles:
        escaped_titles = [re.escape(t) for t in target_titles]
        if escaped_titles:
            delimiters.append(r"^(?:" + "|".join(escaped_titles) + r")$")
            
    pattern_str = r"(?im)^(?:" + "|".join(delimiters) + r")"
    return re.compile(pattern_str)

def stream_book_pages(file_path: Path, use_form_feed: bool, split_pattern: Optional[re.Pattern] = None, encoding: str = "utf-8") -> Generator[str, None, None]:
    """Streams book pages or virtual chapters from disk using generator patterns to save RAM."""
    if use_form_feed:
        # Optimization: read in massive blocks and perform splitting in memory for extreme I/O speed
        remainder = ""
        try:
            with open(file_path, "r", encoding=encoding) as f:
                while True:
                    chunk = f.read(STREAM_IO_BLOCK_SIZE_BYTES)
                    if not chunk:
                        break
                    
                    data = remainder + chunk
                    parts = data.split("\x0c")
                    
                    for part in parts[:-1]:
                        if part.strip():
                            yield part
                    remainder = parts[-1]
                    
                if remainder and remainder.strip():
                    yield remainder
        except UnicodeDecodeError as e:
            logger.error(f"Unicode decode error in stream_book_pages (form feed): {e}")
            raise
        except OSError as e:
            logger.error(f"Error reading book stream by block: {e}")
    else:
        # Optimization: delegate line streaming to Python's native C-optimized BufferedReader iterator
        current_page_lines: list[str] = []
        try:
            with open(file_path, "r", encoding=encoding) as f:
                for line in f:
                    if split_pattern and split_pattern.match(line):
                        if current_page_lines:
                            yield "".join(current_page_lines)
                            current_page_lines = []
                    current_page_lines.append(line)
                    
                if current_page_lines:
                    yield "".join(current_page_lines)
        except UnicodeDecodeError as e:
            logger.error(f"Unicode decode error in stream_book_pages: {e}")
            raise
        except OSError as e:
            logger.error(f"Error reading book stream line by line: {e}")

def safe_tail(text: str, max_len: int) -> str:
    """
    Retorna un substring del final de `text` de longitud máxima aproximada `max_len`,
    alineado a un límite de palabra para no cortar a mitad de camino.
    """
    if len(text) <= max_len:
        return text
    
    limit = len(text) - max_len
    # Buscar el primer espacio o salto de línea a partir de limit en una ventana de 50 caracteres
    first_space = text.find(' ', limit, limit + 50)
    first_newline = text.find('\n', limit, limit + 50)
    
    cut = -1
    if first_space != -1 and first_newline != -1:
        cut = min(first_space, first_newline)
    elif first_space != -1:
        cut = first_space
    elif first_newline != -1:
        cut = first_newline
        
    if cut != -1:
        return text[cut + 1:]
    else:
        return text[limit:]

def _find_safe_cut(text: str, start: int, end: int) -> int:
    """Encuentra la última posición segura para cortar (espacio o punto) fuera de bloques de código y tablas."""
    backtick_count = text.count('```', 0, end)
    
    for cut in range(end, start - 1, -1):
        if cut == start:
            return start
            
        if text[cut:cut+3] == '```':
            backtick_count -= 1
            
        ch = text[cut] if cut < len(text) else ''
        if ch in (' ', '.'):
            if backtick_count % 2 == 0:  # fuera de bloque de código
                line_start = text.rfind('\n', 0, cut) + 1
                line = text[line_start:cut]
                if line.count('|') >= 2:
                    continue
                return cut
    return start

def _build_chunks_by_separator(elements: list[str], max_chars: int, overlap_chars: int, separator: str) -> list[str]:
    """Agrupa elementos (párrafos o líneas) con overlap acumulativo."""
    chunks = []
    current_chunk = []
    current_len = 0
    for elem in elements:
        elem_len = len(elem)
        if current_len + elem_len > max_chars and current_chunk:
            chunk_text = separator.join(current_chunk)
            chunks.append(chunk_text)
            overlap = safe_tail(chunk_text, overlap_chars)
            current_chunk = [overlap, elem]
            current_len = len(overlap) + len(separator) + elem_len
        else:
            current_chunk.append(elem)
            current_len += elem_len + len(separator)
    if current_chunk:
        chunks.append(separator.join(current_chunk))
    return chunks

def _build_chunks_by_lines_safe(lines: list[str], max_chars: int, overlap_chars: int) -> list[str]:
    """Agrupa líneas respetando bloques de código y tablas."""
    chunks = []
    current_chunk = []
    current_len = 0
    in_code_block = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            in_code_block = not in_code_block

        is_table = (not in_code_block and line.count('|') >= 2 and
                    (i+1 < len(lines) and lines[i+1].count('|') >= 2))

        if in_code_block or is_table:
            block_lines = [line]
            j = i + 1
            if is_table:
                while j < len(lines) and lines[j].count('|') >= 2:
                    block_lines.append(lines[j])
                    j += 1
            else:
                while j < len(lines) and not lines[j].strip().startswith("```"):
                    block_lines.append(lines[j])
                    j += 1
                if j < len(lines):
                    block_lines.append(lines[j])
                    j += 1
                in_code_block = False
            
            block_text = "\n".join(block_lines)
            block_len = len(block_text)
            if current_len + block_len > max_chars and current_chunk:
                chunk_text = "\n".join(current_chunk)
                chunks.append(chunk_text)
                overlap = safe_tail(chunk_text, overlap_chars)
                current_chunk = [overlap, block_text]
                current_len = len(overlap) + 1 + block_len
            else:
                current_chunk.append(block_text)
                current_len += block_len + 1
            i = j
            continue

        line_len = len(line)
        if current_len + line_len > max_chars and current_chunk:
            chunk_text = "\n".join(current_chunk)
            chunks.append(chunk_text)
            overlap = safe_tail(chunk_text, overlap_chars)
            current_chunk = [overlap, line]
            current_len = len(overlap) + 1 + line_len
        else:
            current_chunk.append(line)
            current_len += line_len + 1
        i += 1

    if current_chunk:
        chunks.append("\n".join(current_chunk))
    return chunks

def _build_chunks_by_char_safe(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Corta por caracteres con overlap acumulativo en un solo bucle."""
    chunks = []
    pos = 0
    text_len = len(text)
    overlap = ""
    while pos < text_len:
        effective_max = max_chars - len(overlap)
        if effective_max <= 0:
            overlap = safe_tail(overlap, max_chars // 2)
            effective_max = max_chars - len(overlap)

        if pos + effective_max >= text_len:
            chunk = overlap + text[pos:]
            chunks.append(chunk)
            break
            
        end = pos + effective_max
        cut = _find_safe_cut(text, pos, end)
        if cut <= pos:
            cut = end
            
        chunk = overlap + text[pos:cut]
        chunks.append(chunk)
        overlap = safe_tail(chunk, overlap_chars)
        pos = cut
    return chunks

def _write_contingency_chunks(chunks: list[str], output_dir: Path, base_name: str) -> int:
    """Escribe los fragmentos con prefijo de contexto (sin comentarios HTML)."""
    for i, chunk in enumerate(chunks, 1):
        prefix = f"[Continuación de {base_name}, fragmento {i}/{len(chunks)}]\n\n"
        safe_name = f"fragment_{i:03d}.md"
        file_path = output_dir / safe_name
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(prefix)
                f.write(chunk)
                if not chunk.endswith('\n'):
                    f.write('\n')
        except OSError as e:
            logger.error(f"Error writing contingency chunk {file_path}: {e}")
    logger.warning(f"Contingencia: se generaron {len(chunks)} fragmentos con solapamiento incorporado.")
    return len(chunks)

def split_by_contingency_streaming(file_path: Path, output_dir: Path,
                                   max_chars: int = 8000, overlap_chars: int = 400,
                                   encoding: str = "utf-8") -> int:
    """
    Cascada de contingencia con respeto a bloques de código y tablas.
    Nivel 1: párrafos
    Nivel 2: líneas completas (respetando bloques ``` y tablas)
    Nivel 3: caracteres seguros (con detección de límites de palabra fuera de bloques)
    """
    try:
        with open(file_path, "r", encoding=encoding) as f:
            text = f.read()
    except Exception as e:
        logger.error(f"Cannot read file for contingency: {e}")
        return 0

    if len(text) <= max_chars:
        return _write_contingency_chunks([text], output_dir, file_path.stem)

    paragraphs = re.split(r'\n\s*\n', text)
    if len(paragraphs) > 1:
        chunks = _build_chunks_by_separator(paragraphs, max_chars, overlap_chars, separator="\n\n")
        if len(chunks) > 1:
            return _write_contingency_chunks(chunks, output_dir, file_path.stem)

    lines = text.splitlines()
    if len(lines) > 1:
        chunks = _build_chunks_by_lines_safe(lines, max_chars, overlap_chars)
        if len(chunks) > 1:
            return _write_contingency_chunks(chunks, output_dir, file_path.stem)

    chunks = _build_chunks_by_char_safe(text, max_chars, overlap_chars)
    return _write_contingency_chunks(chunks, output_dir, file_path.stem)

def _extract_multiline_title(lines: list[str], start_idx: int) -> tuple[list[str], int]:
    """Helper private function to parse and extract multiline titles to enforce DRY."""
    title_parts: list[str] = []
    lines_added = 0
    idx = start_idx
    
    while idx < len(lines):
        next_line = lines[idx]
        prev_line = lines[idx-1]
        
        # Stop if it exceeds average line length, starts a new chapter prefix, or is a markdown header
        if len(next_line) >= MAX_TITLE_LINE_LENGTH or PREFIX_REGEX.match(next_line) or next_line.startswith("#"):
            break
            
        # Optimization: split only once and store in static blocks to avoid cpu-heavy redundant splits
        next_parts = next_line.split()
        prev_parts = prev_line.split()
        
        is_next_spaced = len(next_parts) > 1 and sum(len(sp) for sp in next_parts) / len(next_parts) < AVG_WORD_LEN_THRESHOLD
        is_prev_spaced = len(prev_parts) > 1 and sum(len(sp) for sp in prev_parts) / len(prev_parts) < AVG_WORD_LEN_THRESHOLD
        
        # Normalize prev_line to prevent single letters in spaced-out text from matching single-letter connectors (like 'y' in Spanish)
        prev_norm = normalize_title(prev_line) if is_prev_spaced else prev_line
        ends_with_connector = CONNECTORS_REGEX.search(prev_norm)
        is_uppercase_flow = prev_line.isupper() and next_line.isupper()
        is_spaced_flow = is_prev_spaced and is_next_spaced
        
        if is_uppercase_flow or ends_with_connector or is_spaced_flow:
            title_parts.append(next_line)
            lines_added += 1
            idx += 1
        else:
            break
            
    return title_parts, lines_added

def is_table_of_contents_page(lines: list[str]) -> bool:
    """Detects if the lines belong to a Table of Contents (TOC) page by checking for index patterns."""
    toc_indicators = 0
    # Match dots followed by numbers at the end of a line (classic index line)
    toc_pattern = re.compile(r'\.\s*\.\s*\.\s*\d+\s*$|\.\.\.\s*\d+\s*$')
    
    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue
        # Check for spaced out dots or consecutive dots
        if " . . ." in cleaned or ". . . ." in cleaned or "  .  ." in cleaned or toc_pattern.search(cleaned):
            toc_indicators += 1
            
    return toc_indicators >= 2

# Submodules to parse specific chapter formats
def check_titles_strategy(clean_first_line: str, target_titles: set[str], titles_list: list[str]) -> tuple[bool, str, Optional[str], Optional[str], int]:
    clean_first_line_lower = clean_first_line.lower()
    if clean_first_line_lower in target_titles:
        matched_title = clean_first_line
        for t in titles_list:
            if t.lower().strip() == clean_first_line_lower:
                matched_title = t.strip()
                break
        return True, "", None, matched_title, 1
    return False, "", None, None, 0

def check_prefix_strategy(first_line: str, lines: list[str]) -> tuple[bool, str, Optional[str], Optional[list[str]], int]:
    if is_table_of_contents_page(lines):
        return False, "Chapter", None, None, 0
        
    match = CHAPTER_PREFIX_FULL_REGEX.match(first_line)
    if match:
        prefix = "Chapter" if "chapter" in first_line.lower() else "Capítulo"
        num_raw = match.group(2)
        title_text = match.group(3).strip()
        lines_to_remove = 1
        
        if not title_text and len(lines) > 1:
            next_line = lines[1]
            if len(next_line) < MAX_CHAPTER_TITLE_LENGTH and not CHAPTER_PREFIX_FULL_REGEX.match(next_line):
                title_text = next_line
                lines_to_remove = 2
                
        title_parts = [title_text] if title_text else []
        extra_parts, lines_added = _extract_multiline_title(lines, lines_to_remove)
        title_parts.extend(extra_parts)
        lines_to_remove += lines_added
                
        return True, prefix, num_raw, title_parts, lines_to_remove
    return False, "Chapter", None, None, 0

def is_valid_title_heuristics(title: str) -> bool:
    """Evaluates if a candidate string looks like a chapter title or a regular text paragraph."""
    clean_title = title.strip()
    if not clean_title:
        return False
        
    # A chapter title shouldn't end with typical sentence-ending punctuation (colon excluded as it can separate titles/subtitles)
    if clean_title.endswith(('.', ',', ';', '...', '-')):
        return False
        
    # Normalize the title to handle spaced-out characters correctly before counting words
    normalized = normalize_title(clean_title)
    words = normalized.split()
    # If the string has more than 15 words, it is probabilistically a text paragraph, not a title
    if not words or len(words) > 15:
        return False
                
    return True

def check_numbered_strategy(first_line: str, lines: list[str]) -> tuple[bool, str, Optional[str], Optional[list[str]], int]:
    if is_table_of_contents_page(lines):
        return False, "Chapter", None, None, 0
        
    clean_num_val = MARKDOWN_HEADER_CLEAN_REGEX.sub('', first_line).strip()
    if NUMBERED_REGEX.match(clean_num_val):
        if len(lines) > 1:
            title_candidate = lines[1]
            if len(title_candidate) < MAX_CHAPTER_TITLE_LENGTH and is_valid_title_heuristics(title_candidate):
                lines_to_remove = 2
                title_parts = [title_candidate]
                
                extra_parts, lines_added = _extract_multiline_title(lines, lines_to_remove)
                title_parts.extend(extra_parts)
                lines_to_remove += lines_added
                        
                return True, "Chapter", clean_num_val, title_parts, lines_to_remove
    return False, "Chapter", None, None, 0

def find_slice_cut_index(page: str, lines_to_remove: int) -> int:
    """Finds the character index where the title ends using cross-platform universal line matching (O(1) memory)."""
    non_empty_seen = 0
    last_pos = 0
    
    # Use precompiled universal line regex (\r?\n) to correctly match Windows & Unix line endings
    line_iter = LINE_MATCH_REGEX.finditer(page)
    
    for match in line_iter:
        line_content = match.group(1)
        last_pos = match.end()
        if line_content.strip():
            non_empty_seen += 1
        if non_empty_seen == lines_to_remove:
            return last_pos
    return 0

def starts_with_sentence_start(text: str) -> bool:
    if not text:
        return False
    first = text[0]
    return first.isupper() or first.isdigit() or first in '"\'“‘(¿¡'

def is_subsection_title(lines: list[str], i: int) -> bool:
    line = lines[i]
    # Si la línea tiene sangría significativa, no es un título alineado al margen
    if line.startswith(' ') or line.startswith('\t'):
        return False
        
    line_clean = line.strip()
    if not line_clean:
        return False
        
    # La línea anterior DEBE estar vacía o ser un encabezado Markdown existente
    prev_line = lines[i-1] if i > 0 else ''
    prev_clean = prev_line.strip()
    if prev_clean and not prev_clean.startswith('#'):
        return False
        
    # El título debe empezar como inicio de oración (mayúscula, número, comillas)
    if not starts_with_sentence_start(line_clean):
        return False
        
    # Evitar comandos de consola ($), flags/parámetros (-), shebangs (!) o rutas (/)
    if line_clean[0] in '$-!/\\':
        return False
    # Evitar encabezados existentes, prefijos de capítulos o números standalone
    if line_clean.startswith('#') or PREFIX_REGEX.match(line_clean) or NUMBERED_REGEX.match(line_clean):
        return False
    # Evitar listas, citas o bullets
    if re.match(r'^\s*([\*\-\+>]|\d+[\.\)])\s+', line_clean):
        return False
    if len(line_clean) > MAX_CHAPTER_TITLE_LENGTH:
        return False
    if line_clean[-1] in '. ,;:':
        return False
    # Evitar títulos que terminen en conectores o artículos (indica wrapping huérfano)
    if CONNECTORS_REGEX.search(line_clean):
        return False
        
    # Buscar el contenido que sigue al título
    # Si la línea siguiente está vacía, miramos la que le sigue secuencialmente
    nxt_clean = ""
    idx = i + 1
    while idx < len(lines):
        candidate = lines[idx].strip()
        if candidate:
            nxt_clean = candidate
            break
        idx += 1
        
    # La línea siguiente no debe estar vacía y debe tener contenido real de párrafo (mínimo 15 caracteres)
    if not nxt_clean or len(nxt_clean) < 15:
        return False
        
    # La siguiente línea de contenido real también debe empezar como inicio de oración (evita continuaciones de wrap)
    if not starts_with_sentence_start(nxt_clean):
        return False
        
    words = line_clean.split()
    if len(words) > 15:
        return False
    # Evitar palabras excesivamente largas (como hashes SHA-1, paths, URLs)
    if any(len(w) > 20 for w in words):
        return False
    return True

def enhance_with_subsection_headers(text: str) -> str:
    lines = text.splitlines()
    output_lines = []
    in_code_block = False
    
    # 1. Identify preliminary candidates
    is_candidate = [False] * len(lines)
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
            
        if is_subsection_title(lines, i):
            is_candidate[i] = True

    # 2. Filter consecutive non-empty candidates (poems, lists, etc.)
    non_empty_indices = [i for i, line in enumerate(lines) if line.strip()]
    prev_ne = {}
    nxt_ne = {}
    for idx, line_idx in enumerate(non_empty_indices):
        prev_ne[line_idx] = non_empty_indices[idx-1] if idx > 0 else None
        nxt_ne[line_idx] = non_empty_indices[idx+1] if idx+1 < len(non_empty_indices) else None

    # 3. Write output applying isolation rule
    in_code_block = False
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            output_lines.append(line)
            continue
            
        if in_code_block:
            output_lines.append(line)
            continue
            
        if is_candidate[i]:
            p_idx = prev_ne.get(i)
            n_idx = nxt_ne.get(i)
            
            p_is_cand = is_candidate[p_idx] if p_idx is not None else False
            n_is_cand = is_candidate[n_idx] if n_idx is not None else False
            
            # A genuine subsection title must be isolated from other candidates
            if not p_is_cand and not n_is_cand:
                output_lines.append(f"## {line.strip()}")
                continue
                
        output_lines.append(line)
        
    return "\n".join(output_lines)

def split_book(input_path: Path, output_path: Path, strategy: str = "auto", titles_list: Optional[list[str]] = None, chunk_lines: int = 2000, encoding: str = "utf-8", enhance_subsections: bool = False, chunk_size: int = 8000, overlap_size: int = 400) -> bool:
    try:
        # Pre-compile the dynamic virtual page separator pattern and detect Form Feeds
        use_form_feed = check_has_form_feed(input_path, encoding=encoding)
        target_titles = set(t.lower().strip() for t in titles_list if t.strip()) if titles_list else None
        split_pattern = build_split_pattern(strategy, target_titles) if not use_form_feed else None
        
        # Initialize the memory-efficient streaming generator
        page_gen = stream_book_pages(input_path, use_form_feed, split_pattern, encoding=encoding)
        
        # Dynamic strategy pre-detection with early-stopping for statistical confidence (accelerates start)
        sample_pages: list[str] = []
        prefix_count = 0
        numbered_count = 0
        
        for _ in range(STRATEGY_SAMPLE_PAGES):
            try:
                page = next(page_gen)
                sample_pages.append(page)
                
                # Optimization: retrieve only the first SAMPLE_LINES_COUNT non-empty lines dynamically
                lines = get_first_non_empty_lines(page, SAMPLE_LINES_COUNT)
                if lines:
                    first_line = lines[0]
                    clean_first = MARKDOWN_HEADER_CLEAN_REGEX.sub('', first_line).strip()
                    clean_first = MARKDOWN_HEADER_END_CLEAN_REGEX.sub('', clean_first).strip()
                    if PREFIX_REGEX.match(first_line):
                        prefix_count += 1
                    elif NUMBERED_REGEX.match(clean_first):
                        if len(lines) > 1 and len(lines[1]) < MAX_TITLE_LINE_LENGTH:
                            numbered_count += 1
                            
                # Early stop if we achieve statistical confidence
                if prefix_count >= CONFIDENCE_THRESHOLD or numbered_count >= CONFIDENCE_THRESHOLD:
                    break
            except StopIteration:
                break
                
        logger.info(f"Sampled {len(sample_pages)} pages. Prefix hits: {prefix_count}, Numbered hits: {numbered_count}")
        
        if strategy == "auto":
            if prefix_count >= CONFIDENCE_THRESHOLD:
                strategy = "prefix"
            elif numbered_count >= CONFIDENCE_THRESHOLD:
                strategy = "numbered"
            else:
                strategy = "prefix" # fallback
            logger.info(f"Selected strategy: {strategy}")
            
        # Context state controller for chapter operations
        ctx = SplitterContext(output_path, enhance_subsections=enhance_subsections)
        
        # Pythonic, C-optimized lazy iteration of multiple iterables using itertools.chain
        for page in itertools.chain(sample_pages, page_gen):
            # Optimization: retrieve only the first HEADER_SCAN_LINES_COUNT non-empty lines sequentially
            lines = get_first_non_empty_lines(page, HEADER_SCAN_LINES_COUNT)
            if not lines:
                ctx.append_content(page)
                continue
                
            first_line = lines[0]
            clean_first_line = MARKDOWN_HEADER_CLEAN_REGEX.sub('', first_line).strip()
            clean_first_line = MARKDOWN_HEADER_END_CLEAN_REGEX.sub('', clean_first_line).strip()
            
            # Stop writing if we hit the end of the book
            if ctx.is_writing and END_OF_BOOK_REGEX.match(clean_first_line):
                ctx.stop_writing()
                continue
                
            detected = False
            prefix = "Chapter"
            num_raw = None
            title_text = None
            lines_to_remove = 0
            
            # 1. Detect Chapter based on strategy
            # Only treat generic '#' headers as new chapters if we failed to detect a confident strategy,
            # indicating we are dealing with a flat markdown document without clear prefix/numbered patterns.
            is_fallback_generic_markdown = (
                strategy == "prefix"
                and prefix_count < CONFIDENCE_THRESHOLD
                and numbered_count < CONFIDENCE_THRESHOLD
            )
            
            if first_line.startswith("#") and strategy != "titles" and is_fallback_generic_markdown:
                if not CHAPTER_PREFIX_FULL_REGEX.match(first_line):
                    detected = True
                    prefix = "Chapter"
                    num_raw = None
                    title_text = clean_first_line
                    lines_to_remove = 1
                    
            if not detected:
                if strategy == "titles" and target_titles and titles_list:
                    detected, prefix, num_raw, title_text, lines_to_remove = check_titles_strategy(
                        clean_first_line, target_titles, titles_list
                    )
                elif strategy == "prefix":
                    detected, prefix, num_raw, title_text, lines_to_remove = check_prefix_strategy(
                        first_line, lines
                    )
                elif strategy == "numbered":
                    detected, prefix, num_raw, title_text, lines_to_remove = check_numbered_strategy(
                        first_line, lines
                    )
                            
            # 2. Process chapter start
            if detected:
                # Resolve chapter number string
                if num_raw is None:
                    num_str = f"{ctx.chapter_count + 1:02d}"
                else:
                    try:
                        num = int(num_raw)
                        num_str = f"{num:02d}"
                    except ValueError:
                        if re.match(r"^[IVXLCDMivxlcdm]+$", num_raw):
                            num_str = num_raw.upper()
                        else:
                            num_str = num_raw
                
                # Clean and normalize title text
                if isinstance(title_text, list):
                    title_clean = " ".join(normalize_title(part) for part in title_text if part).strip()
                elif title_text:
                    title_clean = normalize_title(title_text)
                else:
                    title_clean = ""
                    
                if prefix:
                    detected_chapter_title = f"{prefix} {num_str} - {title_clean}" if title_clean else f"{prefix} {num_str}"
                else:
                    detected_chapter_title = f"{num_str} - {title_clean}" if title_clean else f"{num_str}"
                ctx.start_new_chapter(detected_chapter_title)
                
                # O(1) Memory slicing to bypass title lines without splitting the whole page in memory
                cut_idx = find_slice_cut_index(page, lines_to_remove)
                rest_of_page = page[cut_idx:].lstrip()
                
                if rest_of_page:
                    ctx.append_content(rest_of_page)
                continue
                
            ctx.append_content(page)
                
        # Flush remaining content
        ctx.flush()
            
        # 3. Fallback: if no chapters were written, split using the 3-level contingency cascade
        if ctx.chapter_count == 0:
            split_by_contingency_streaming(input_path, output_path, max_chars=chunk_size, overlap_chars=overlap_size, encoding=encoding)
        else:
            logger.info(f"Successfully finished. Total chapters written: {ctx.chapter_count}")
            
        return True
    except UnicodeDecodeError as e:
        logger.error(f"Unicode decoding failed for input file '{input_path}' with encoding '{encoding}'. "
                     f"Please verify the file encoding or specify it manually with --encoding. Details: {e}")
        return False

def main() -> None:
    """Isolated entry point responsible for argument resolution and environment preparation (early mkdir)."""
    args = parse_args()
    
    if not args.file.exists():
        logger.error(f"Input file '{args.file}' does not exist.")
        sys.exit(1)
        
    # Early environment preparation: create target directories at CLI interface layer
    try:
        args.output.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        logger.error(f"Permission denied creating output directory '{args.output}': {e}")
        sys.exit(1)
    except OSError as e:
        logger.error(f"Failed to create output directory '{args.output}': {e}")
        sys.exit(1)
        
    if not args.output.is_dir():
        logger.error(f"Output path '{args.output}' is not a directory.")
        sys.exit(1)
    
    titles_list = None
    if args.strategy == "titles" or args.titles or args.titles_file:
        if args.titles:
            titles_list = [t.strip() for t in args.titles.split(",") if t.strip()]
        elif args.titles_file:
            if not args.titles_file.exists():
                logger.error(f"Titles file '{args.titles_file}' does not exist.")
                sys.exit(1)
            try:
                # Use specified encoding for titles file as well
                with open(args.titles_file, "r", encoding=args.encoding) as f:
                    titles_list = [line.strip() for line in f if line.strip()]
            except UnicodeDecodeError as e:
                logger.error(f"Unicode decode error reading titles file '{args.titles_file}' with encoding '{args.encoding}': {e}")
                sys.exit(1)
            except OSError as e:
                logger.error(f"Failed to read titles file '{args.titles_file}': {e}")
                sys.exit(1)
        
        if not titles_list:
            logger.error("Strategy 'titles' selected, but no titles were provided via --titles or --titles-file.")
            sys.exit(1)
            
        if args.strategy == "auto":
            args.strategy = "titles"
            
    success = split_book(args.file, args.output, strategy=args.strategy, titles_list=titles_list, chunk_lines=args.chunk_lines, encoding=args.encoding, enhance_subsections=args.enhance_subsections, chunk_size=args.chunk_size, overlap_size=args.overlap_size)
    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main()
