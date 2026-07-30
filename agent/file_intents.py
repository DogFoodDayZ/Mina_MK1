import json
import os
import re
from typing import Any, Dict, Optional, Tuple


def detect_file_directory_intent(core: Any, text: str, raw_text: str, t: str) -> Optional[Tuple[Optional[str], Dict[str, Any]]]:
    def extract_directory_path(raw: str) -> str:
        quoted = re.findall(r'["\']([^"\']+)["\']', raw)
        for q in quoted:
            candidate = q.strip()
            if candidate and not re.search(r'\.[A-Za-z0-9]{1,8}$', candidate):
                return candidate

        patterns = [
            r'\b(?:in|at|to)\s+([A-Za-z]:\\[^\s,;]+)',
            r'\b(?:in|at|to)\s+([.]{1,2}[\\/][^\s,;]+)',
            r'\b(?:in|at|to)\s+([^\s,;]+[\\/][^\s,;]+)',
            r'\b(?:named|called)\s+([^\s,;]+)',
            r'\b(?:folder|directory|dir)\s+([^\s,;]+)',
        ]

        for pattern in patterns:
            m = re.search(pattern, raw, re.IGNORECASE)
            if m:
                candidate = m.group(1).strip().strip('"\'')
                if candidate and not re.search(r'\.[A-Za-z0-9]{1,8}$', candidate):
                    return candidate

        return ""

    def extract_file_write_path(raw: str) -> str:
        quoted = re.findall(r'["\']([^"\']+)["\']', raw)
        for q in quoted:
            candidate = q.strip()
            if re.search(r'\.[A-Za-z0-9]{1,8}$', candidate):
                return candidate.rstrip('.,;:')

        patterns = [
            r'\bfile\s+([^\s,;:]+\.[A-Za-z0-9]{1,8})',
            r'\b(?:at|to|in)\s+([A-Za-z]:\\[^\s,;]+)',
            r'\b(?:at|to|in)\s+([.]{1,2}[\\/][^\s,;]+)',
            r'\b(?:at|to|in)\s+([^\s,;]+\.[A-Za-z0-9]{1,8})',
            r'\b(?:named|called)\s+([^\s,;]+\.[A-Za-z0-9]{1,8})',
        ]

        for pattern in patterns:
            m = re.search(pattern, raw, re.IGNORECASE)
            if m:
                return m.group(1).strip().strip('"\'').rstrip('.,;:')

        return ""

    def extract_file_write_content(raw: str, lowered: str) -> str:
        fence = re.search(r'```(?:[A-Za-z0-9_+-]+)?\n([\s\S]*?)```', raw)
        if fence:
            return fence.group(1).strip("\n")

        quoted = re.findall(r'["\']([^"\']+)["\']', raw)
        if quoted:
            for q in reversed(quoted):
                if not re.search(r'\.[A-Za-z0-9]{1,8}$', q.strip()):
                    return q.strip()

        content_patterns = [
            r'\bwith\s+content\s*:\s*([\s\S]+)$',
            r'\bwith\s+content\s+([\s\S]+)$',
            r'\bthat\s+(?:says|prints|contains)\s+([\s\S]+)$',
            r'\bprint(?:s)?\s+(["\'][^"\']+["\'])',
        ]

        for pattern in content_patterns:
            m = re.search(pattern, raw, re.IGNORECASE)
            if m:
                return m.group(1).strip().strip('"\'')

        if "hello world" in lowered:
            return 'print("hello world")'

        return ""

    scaffold_intent = (
        re.search(r'\b(create|make|build|scaffold|setup)\b', t) is not None
        and (
            re.search(r'\b(folder|folders|directory|directories|dir)\b', t) is not None
            and (
                re.search(r'\b(file|files|script|scripts|module|modules)\b', t) is not None
                or " with " in t
                or " inside " in t
            )
        )
    )

    if not scaffold_intent:
        scaffold_intent = (
            "project scaffold" in t
            or (
                "create project" in t
                and re.search(r'\b(src|tests?)\b', t) is not None
                and re.search(r'\b(file|files|directory|directories|folder|folders)\b', t) is not None
            )
        )

    if scaffold_intent:
        return "__scaffold__", {"request": raw_text}

    directory_create_intent = (
        re.search(r'\b(create|make|add|new)\b', t) is not None
        and re.search(r'\b(folder|directory|dir)\b', t) is not None
    ) or any(x in t for x in [
        "mkdir ", "make folder", "create folder", "create directory", "new folder", "new directory",
    ])

    if directory_create_intent:
        path = extract_directory_path(raw_text)
        if path:
            return "dir_create", {"path": path}

    if core._is_recursive_directory_read_request(raw_text, t):
        path = core._extract_explicit_windows_path(raw_text)
        if path:
            return "__inspect_directory_tree__", {"path": path, "mode": core._extract_directory_inspection_filter(raw_text)}

    sqlite_table_match = re.search(
        r'\b(?:read|show|preview|inspect|describe)\s+(?:the\s+)?table\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:from|in|inside)\s+(.+)$',
        raw_text,
        re.IGNORECASE,
    )
    if sqlite_table_match:
        table_name = str(sqlite_table_match.group(1) or "").strip()
        db_path = core._extract_explicit_windows_path(str(sqlite_table_match.group(2) or ""))
        if table_name and db_path:
            return "__inspect_sqlite_table__", {"path": db_path, "table": table_name, "row_limit": 8}

    if any(x in t for x in ["read file", "open file", "show file"]):
        m = re.search(r'["\']([^"\']+)["\']', text)
        if not m:
            m = re.search(r'[“‘]([^”’]+)[”’]', text)
        if not m:
            m = re.search(r'file\s+([^\s]+)', text, re.IGNORECASE)
        path = m.group(1).strip() if m else ""
        return "file_read", {"path": path}

    if any(x in t for x in [
        "delete file", "remove file", "delete script", "remove script", "delete the file", "remove the file", "delete this file", "remove this file",
    ]):
        path = ""
        quoted = re.findall(r'["\']([^"\']+)["\']', text)
        for q in quoted:
            if re.match(r'^[A-Za-z]:\\', q):
                path = q.strip()
                break
        if not path:
            m = re.search(r'\bat\s+([A-Za-z]:\\[^\s,]+)', text, re.IGNORECASE)
            if not m:
                m = re.search(r'\bfrom\s+([A-Za-z]:\\[^\s,]+)', text, re.IGNORECASE)
            if not m:
                m = re.search(r'([A-Za-z]:\\[^\s,]+\.(?:py|txt|md|json|csv))', text, re.IGNORECASE)
            if m:
                path = m.group(1).strip()
        return "file_delete", {"path": path}

    if any(x in t for x in [
        "move file", "move script", "move this file", "move the file", "rename file", "rename script", "rename this file", "rename the file", "rename to", "move to",
    ]) or (("move" in t or "rename" in t) and re.search(r'[A-Za-z]:\\', text) and " to " in t):
        src = ""
        dst = ""
        quoted = re.findall(r'["\']([^"\']+)["\']', text)
        quoted_paths = [q.strip() for q in quoted if re.match(r'^[A-Za-z]:\\', q)]
        if len(quoted_paths) >= 2:
            src, dst = quoted_paths[0], quoted_paths[1]
        elif len(quoted_paths) == 1:
            src = quoted_paths[0]
        if not src:
            m = re.search(r'\b(?:move|rename)\s+(?:the\s+)?file\s+([A-Za-z]:\\[^\s,]+)', text, re.IGNORECASE)
            if not m:
                m = re.search(r'\bfrom\s+([A-Za-z]:\\[^\s,]+)', text, re.IGNORECASE)
            if m:
                src = m.group(1).strip()
        if not dst:
            m = re.search(r'\bto\s+([A-Za-z]:\\[^\s,]+)', text, re.IGNORECASE)
            if not m:
                m = re.search(r'\brename\s+[A-Za-z]:\\[^\s,]+\s+to\s+([^\s,]+)', text, re.IGNORECASE)
            if m:
                dst = m.group(1).strip()
        # Guard against false positives from generic phrasing such as
        # "create and save a program to rename files..." where no concrete
        # source/destination paths are present.
        if not src or not dst:
            return None
        if src and dst and not os.path.isabs(dst):
            dst = os.path.join(os.path.dirname(src), dst)
        return "file_move", {"src": src, "dst": dst}

    if any(x in t for x in [
        "append file", "append to file", "add to file", "add content to file", "append text to", "append this", "append the following",
    ]) or ("append" in t and re.search(r'[A-Za-z]:\\', text) and any(x in t for x in ["to", "with", "content", "text", "line"])):
        path = ""
        content = ""
        quoted = re.findall(r'["\']([^"\']+)["\']', text)
        for q in quoted:
            if re.match(r'^[A-Za-z]:\\', q):
                path = q.strip()
        if not path:
            m = re.search(r'\bfile\s+([^\s,;:]+\.[A-Za-z0-9]{1,8})', text, re.IGNORECASE)
            if not m:
                m = re.search(r'\bat\s+([A-Za-z]:\\[^\s,]+)', text, re.IGNORECASE)
            if not m:
                m = re.search(r'\bto\s+([A-Za-z]:\\[^\s,]+)', text, re.IGNORECASE)
            if not m:
                m = re.search(r'([A-Za-z]:\\[^\s,]+\.[A-Za-z0-9]+)', text, re.IGNORECASE)
            if not m:
                m = re.search(r'\b(?:at|to)\s+([^\s,;]+\.[A-Za-z0-9]{1,8})', text, re.IGNORECASE)
            if not m:
                m = re.search(r'([^\s,;]+\.[A-Za-z0-9]{1,8})', text, re.IGNORECASE)
            if m:
                path = m.group(1).strip().rstrip('.,;:')
        if not content:
            m = re.search(r'\bwith\s+content\s*:\s*([\s\S]+)$', text, re.IGNORECASE)
            if not m:
                m = re.search(r'(?:append|add).*?\bto\s+[^\s]+\s+(.+)', text, re.IGNORECASE)
            if m:
                content = m.group(1).strip()
            elif quoted:
                content = quoted[-1].strip()
        return "file_append", {"path": path, "content": content}

    file_write_intent = any(x in t for x in [
        "create file", "write file", "write to file", "write this to file", "save file", "make file", "create a python script", "create a script", "write a python script", "save a python script", "make a python script", "create script", "make script", "write script", "save script",
    ]) or (("at " in t or "to " in t) and any(x in t for x in ["print", "prints", "code", "content", "with content"])) or (
        re.search(r'\b(create|write|save|make|generate|build)\b', t) is not None
        and (re.search(r'\b(file|script|module)\b', t) is not None or re.search(r'\.[A-Za-z0-9]{1,8}\b', raw_text) is not None)
    )
    if file_write_intent:
        path = extract_file_write_path(raw_text)
        if not path:
            return None, {}
        if not re.search(r'\.[A-Za-z0-9]{1,8}$', path) and (
            "project scaffold" in t
            or re.search(r'\b(make|create)\b.*\b(project|structure)\b', t) is not None
            or re.search(r'\b(src|tests?)\b', t) is not None
        ):
            return "__scaffold__", {"request": raw_text}
        content = extract_file_write_content(raw_text, t)
        if path.lower().endswith('.py') and content:
            if not content.strip().startswith('print') and ('print' in t or 'prints' in t or 'python script' in t):
                content = f'print({json.dumps(content)})'
        return "file_write", {"path": path, "content": content}

    dir_list_triggers = [
        "list directory", "list files", "show files", "show directory", "directory contents", "folder contents", "what files", "what's in this folder", "contents of", "list the contents", "list contents", "show contents", "what is in", "toy box", "toybox",
    ]
    if any(x in t for x in dir_list_triggers) or re.search(r'\blist\b\s+[a-zA-Z]:\\', t) or re.search(r'\bcontents\b.*[a-zA-Z]:\\', t):
        workspace_root = core.workspace_root
        path = workspace_root
        m = re.search(r'in\s+([a-zA-Z]:\\?[^\n\r]*)', text, re.IGNORECASE)
        if not m:
            m = re.search(r'on\s+([a-zA-Z]:\\?[^\n\r]*)', text, re.IGNORECASE)
        if not m:
            m = re.search(r'of\s+([a-zA-Z]:\\?[^\n\r]*)', text, re.IGNORECASE)
        if not m:
            m = re.search(r'(?:list\s+files\s+)?([a-zA-Z]:\\[^\n\r]*)', text, re.IGNORECASE)
        if not m:
            m = re.search(r'(?:list\s+)?([a-zA-Z]:\\?)(?:\s|$)', text, re.IGNORECASE)
        if m:
            path = m.group(1).strip()
        elif any(x in t for x in ["toy box", "toybox"]):
            path = os.path.join(workspace_root, "toys")
        return "dir_list", {"path": path}

    return None
