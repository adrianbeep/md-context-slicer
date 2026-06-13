#!/usr/bin/env python3
import os
import sys
import re
import argparse
import itertools
from pathlib import Path
from typing import Optional

# Ensure the assets directory is in sys.path for direct script execution
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import (
    logger, check_has_form_feed, get_first_non_empty_lines, normalize_title,
    PREFIX_REGEX, NUMBERED_REGEX, CHAPTER_PREFIX_FULL_REGEX, LINE_MATCH_REGEX,
    MARKDOWN_HEADER_CLEAN_REGEX, MARKDOWN_HEADER_END_CLEAN_REGEX,
    END_OF_BOOK_REGEX, STRATEGY_SAMPLE_PAGES, CONFIDENCE_THRESHOLD,
    SAMPLE_LINES_COUNT, MAX_TITLE_LINE_LENGTH, HEADER_SCAN_LINES_COUNT
)
from context import SplitterContext
from strategies import (
    build_split_pattern, stream_book_pages, check_titles_strategy,
    check_prefix_strategy, check_numbered_strategy
)
from contingency import split_by_contingency_streaming

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split a Markdown book file into separate chapter files.")
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

def find_slice_cut_index(page: str, lines_to_remove: int) -> int:
    """Finds the character index where the title ends using cross-platform universal line matching (O(1) memory)."""
    non_empty_seen = 0
    last_pos = 0
    
    line_iter = LINE_MATCH_REGEX.finditer(page)
    
    for match in line_iter:
        line_content = match.group(1)
        last_pos = match.end()
        if line_content.strip():
            non_empty_seen += 1
        if non_empty_seen == lines_to_remove:
            return last_pos
    return 0

def split_book(input_path: Path, output_path: Path, strategy: str = "auto", titles_list: Optional[list[str]] = None, chunk_lines: int = 2000, encoding: str = "utf-8", enhance_subsections: bool = False, chunk_size: int = 8000, overlap_size: int = 400) -> bool:
    try:
        use_form_feed = check_has_form_feed(input_path, encoding=encoding)
        target_titles = set(t.lower().strip() for t in titles_list if t.strip()) if titles_list else None
        split_pattern = build_split_pattern(strategy, target_titles) if not use_form_feed else None
        
        page_gen = stream_book_pages(input_path, use_form_feed, split_pattern, encoding=encoding)
        
        sample_pages: list[str] = []
        prefix_count = 0
        numbered_count = 0
        
        for _ in range(STRATEGY_SAMPLE_PAGES):
            try:
                page = next(page_gen)
                sample_pages.append(page)
                
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
                strategy = "prefix"
            logger.info(f"Selected strategy: {strategy}")
            
        ctx = SplitterContext(output_path, enhance_subsections=enhance_subsections)
        
        for page in itertools.chain(sample_pages, page_gen):
            lines = get_first_non_empty_lines(page, HEADER_SCAN_LINES_COUNT)
            if not lines:
                ctx.append_content(page)
                continue
                
            first_line = lines[0]
            clean_first_line = MARKDOWN_HEADER_CLEAN_REGEX.sub('', first_line).strip()
            clean_first_line = MARKDOWN_HEADER_END_CLEAN_REGEX.sub('', clean_first_line).strip()
            
            if ctx.is_writing and END_OF_BOOK_REGEX.match(clean_first_line):
                ctx.stop_writing()
                continue
                
            detected = False
            prefix = "Chapter"
            num_raw = None
            title_text = None
            lines_to_remove = 0
            
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
                            
            if detected:
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
                
                cut_idx = find_slice_cut_index(page, lines_to_remove)
                rest_of_page = page[cut_idx:].lstrip()
                
                if rest_of_page:
                    ctx.append_content(rest_of_page)
                continue
                
            ctx.append_content(page)
                
        ctx.flush()
            
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
    args = parse_args()
    
    if not args.file.exists():
        logger.error(f"Input file '{args.file}' does not exist.")
        sys.exit(1)
        
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
