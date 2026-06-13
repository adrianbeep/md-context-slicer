import re
from utils import MAX_CHAPTER_TITLE_LENGTH, PREFIX_REGEX, NUMBERED_REGEX, CONNECTORS_REGEX

def starts_with_sentence_start(text: str) -> bool:
    if not text:
        return False
    first = text[0]
    return first.isupper() or first.isdigit() or first in '"\'“‘(¿¡'

def is_subsection_title(lines: list[str], i: int) -> bool:
    line = lines[i]
    if line.startswith(' ') or line.startswith('\t'):
        return False
        
    line_clean = line.strip()
    if not line_clean:
        return False
        
    prev_line = lines[i-1] if i > 0 else ''
    prev_clean = prev_line.strip()
    if prev_clean and not prev_clean.startswith('#'):
        return False
        
    if not starts_with_sentence_start(line_clean):
        return False
        
    if line_clean[0] in '$-!/\\':
        return False
    if line_clean.startswith('#') or PREFIX_REGEX.match(line_clean) or NUMBERED_REGEX.match(line_clean):
        return False
    if re.match(r'^\s*([\*\-\+>]|\d+[\.\)])\s+', line_clean):
        return False
    if len(line_clean) > MAX_CHAPTER_TITLE_LENGTH:
        return False
    if line_clean[-1] in '. ,;:':
        return False
    if CONNECTORS_REGEX.search(line_clean):
        return False
        
    nxt_clean = ""
    idx = i + 1
    while idx < len(lines):
        candidate = lines[idx].strip()
        if candidate:
            nxt_clean = candidate
            break
        idx += 1
        
    if not nxt_clean or len(nxt_clean) < 15:
        return False
        
    if not starts_with_sentence_start(nxt_clean):
        return False
        
    words = line_clean.split()
    if len(words) > 15:
        return False
    if any(len(w) > 20 for w in words):
        return False
    return True

def enhance_with_subsection_headers(text: str) -> str:
    lines = text.splitlines()
    output_lines = []
    in_code_block = False
    
    is_candidate = [False] * len(lines)
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
            
        if is_subsection_title(lines, i):
            is_candidate[i] = True

    non_empty_indices = [i for i, line in enumerate(lines) if line.strip()]
    prev_ne = {}
    nxt_ne = {}
    for idx, line_idx in enumerate(non_empty_indices):
        prev_ne[line_idx] = non_empty_indices[idx-1] if idx > 0 else None
        nxt_ne[line_idx] = non_empty_indices[idx+1] if idx+1 < len(non_empty_indices) else None

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
            
            if not p_is_cand and not n_is_cand:
                output_lines.append(f"## {line.strip()}")
                continue
                
        output_lines.append(line)
        
    return "\n".join(output_lines)
