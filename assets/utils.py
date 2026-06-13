import logging
import sys
import re
from pathlib import Path

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

def normalize_title(title: str) -> str:
    cleaned = title.strip()
    if not cleaned:
        return ""
    
    cleaned = MARKDOWN_HEADER_CLEAN_REGEX.sub('', cleaned)
    cleaned = MARKDOWN_HEADER_END_CLEAN_REGEX.sub('', cleaned).strip()
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
    return " ".join(result.split())

def get_first_non_empty_lines(page: str, max_lines: int = HEADER_SCAN_LINES_COUNT) -> list[str]:
    """Returns the first max_lines non-empty lines from the page sequentially."""
    lines: list[str] = []
    for line in page.splitlines():
        cleaned = line.strip()
        if cleaned:
            lines.append(cleaned)
            if len(lines) >= max_lines:
                break
    return lines

def check_has_form_feed(file_path: Path, encoding: str = "utf-8") -> bool:
    """Check if the file contains Form Feed characters."""
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
    except OSError:
        return False
