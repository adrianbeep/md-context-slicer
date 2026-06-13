import re
from pathlib import Path
from utils import logger

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
