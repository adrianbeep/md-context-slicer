from pathlib import Path
from typing import Optional
from utils import logger, WINDOWS_RESERVED_NAMES, ILLEGAL_CHARS_REGEX
from enhancer import enhance_with_subsection_headers

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
            
            safe_name = safe_name if safe_name else f"Chapter_{self.chapter_count:02d}"
            
            if safe_name.upper() in WINDOWS_RESERVED_NAMES:
                safe_name = f"Chapter_{self.chapter_count:02d}_{safe_name}"
                
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
