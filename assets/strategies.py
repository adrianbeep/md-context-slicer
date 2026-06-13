import re
import itertools
from pathlib import Path
from typing import Optional
from collections.abc import Generator
from utils import (
    logger, PREFIX_REGEX, CHAPTER_PREFIX_FULL_REGEX, NUMBERED_REGEX, 
    CONNECTORS_REGEX, MARKDOWN_HEADER_CLEAN_REGEX, MARKDOWN_HEADER_END_CLEAN_REGEX,
    STRATEGY_SAMPLE_PAGES, CONFIDENCE_THRESHOLD, SAMPLE_LINES_COUNT,
    MAX_TITLE_LINE_LENGTH, MAX_CHAPTER_TITLE_LENGTH, STREAM_IO_BLOCK_SIZE_BYTES,
    AVG_WORD_LEN_THRESHOLD, normalize_title, get_first_non_empty_lines
)

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

def _extract_multiline_title(lines: list[str], start_idx: int) -> tuple[list[str], int]:
    """Helper private function to parse and extract multiline titles to enforce DRY."""
    title_parts: list[str] = []
    lines_added = 0
    idx = start_idx
    
    while idx < len(lines):
        next_line = lines[idx]
        prev_line = lines[idx-1]
        
        if len(next_line) >= MAX_TITLE_LINE_LENGTH or PREFIX_REGEX.match(next_line) or next_line.startswith("#"):
            break
            
        next_parts = next_line.split()
        prev_parts = prev_line.split()
        
        is_next_spaced = len(next_parts) > 1 and sum(len(sp) for sp in next_parts) / len(next_parts) < AVG_WORD_LEN_THRESHOLD
        is_prev_spaced = len(prev_parts) > 1 and sum(len(sp) for sp in prev_parts) / len(prev_parts) < AVG_WORD_LEN_THRESHOLD
        
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
    toc_pattern = re.compile(r'\.\s*\.\s*\.\s*\d+\s*$|\.\dots\s*\d+\s*$')
    
    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue
        if " . . ." in cleaned or ". . . ." in cleaned or "  .  ." in cleaned or toc_pattern.search(cleaned):
            toc_indicators += 1
            
    return toc_indicators >= 2

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
        
    if clean_title.endswith(('.', ',', ';', '...', '-')):
        return False
        
    normalized = normalize_title(clean_title)
    words = normalized.split()
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
