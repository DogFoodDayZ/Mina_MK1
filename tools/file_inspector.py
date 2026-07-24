import base64
import csv
import mimetypes
import os
import re
import sqlite3
import struct
import wave
import zipfile
from typing import Any, Dict, Optional

try:
    from charset_normalizer import from_bytes as charset_from_bytes
except Exception:
    charset_from_bytes = None


TEXT_EXTENSIONS = {
    ".txt", ".log", ".ini", ".cfg", ".conf", ".json", ".xml", ".yaml", ".yml",
    ".csv", ".tsv", ".md", ".html", ".htm", ".css", ".js", ".ts", ".py", ".ps1",
    ".bat", ".cmd", ".lua", ".sql", ".uplugin", ".uproject", ".manifest", ".properties",
    ".toml", ".reg", ".url", ".gitignore", ".env", ".plist",
}

STRUCTURED_TEXT_EXTENSIONS = {
    ".json", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".properties", ".toml",
}

BINARY_EXTENSIONS = {
    ".exe", ".dll", ".pak", ".sig", ".bin", ".dat", ".db", ".dbtmp", ".sqlite", ".sqlite3",
    ".uasset", ".umap", ".ucas", ".utoc", ".bk2", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".bmp", ".ico", ".pdf", ".zip", ".7z", ".rar", ".gz", ".mp3", ".wav", ".ogg", ".mp4",
    ".avi", ".mkv", ".mov", ".ttf", ".otf", ".woff", ".woff2",
}


def _truncate_cell(value: Any, max_len: int = 80) -> str:
    txt = str(value if value is not None else "")
    txt = re.sub(r"\s+", " ", txt).strip()
    if len(txt) > max_len:
        return txt[:max_len].rstrip() + "..."
    return txt


def _truncate_text(text: str, max_chars: Optional[int]) -> Dict[str, Any]:
    out = str(text or "")
    truncated = False
    if max_chars is not None and max_chars >= 0 and len(out) > max_chars:
        out = out[:max_chars]
        truncated = True
    return {"content": out, "truncated": truncated}


def _file_extension(path: str) -> str:
    return os.path.splitext(str(path or ""))[1].lower()


def is_probably_text_path(path: str) -> bool:
    ext = _file_extension(path)
    if ext in TEXT_EXTENSIONS:
        return True
    if ext in BINARY_EXTENSIONS:
        return False
    mime, _ = mimetypes.guess_type(path)
    return bool(mime and (mime.startswith("text/") or mime in {"application/json", "application/xml"}))


def _looks_binary(raw: bytes, path: str) -> bool:
    ext = _file_extension(path)
    if ext in BINARY_EXTENSIONS:
        return True
    if not raw:
        return False
    if raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return False
    if b"\x00" in raw:
        return True
    suspicious = sum(1 for b in raw[:4096] if b < 9 or (13 < b < 32))
    sample = min(len(raw), 4096)
    return sample > 0 and (suspicious / float(sample)) > 0.30


def _decode_text_bytes(raw: bytes) -> Dict[str, Any]:
    if not raw:
        return {"ok": True, "text": "", "encoding": "utf-8", "error": None}

    direct_encodings = ["utf-8-sig"]
    for encoding in direct_encodings:
        try:
            text = raw.decode(encoding)
            return {"ok": True, "text": text, "encoding": encoding, "error": None}
        except Exception:
            pass

    has_utf16_bom = raw.startswith((b"\xff\xfe", b"\xfe\xff"))
    has_utf32_bom = raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"))
    nul_ratio = (raw[:4096].count(0) / float(min(len(raw), 4096))) if raw else 0.0
    likely_wide_text = has_utf16_bom or has_utf32_bom or nul_ratio > 0.20

    if likely_wide_text:
        wide_encodings = ["utf-32", "utf-16", "utf-16-le", "utf-16-be"]
        for encoding in wide_encodings:
            try:
                text = raw.decode(encoding)
                return {"ok": True, "text": text, "encoding": encoding, "error": None}
            except Exception:
                pass

    fallback_encodings = ["cp1252", "latin-1"]
    for encoding in fallback_encodings:
        try:
            text = raw.decode(encoding)
            return {"ok": True, "text": text, "encoding": encoding, "error": None}
        except Exception:
            pass

    if charset_from_bytes is not None:
        try:
            match = charset_from_bytes(raw).best()
            if match is not None:
                return {
                    "ok": True,
                    "text": str(match),
                    "encoding": str(getattr(match, "encoding", "unknown") or "unknown"),
                    "error": None,
                }
        except Exception:
            pass

    return {"ok": False, "text": "", "encoding": None, "error": "decode_failed"}


def _binary_summary(path: str, raw: bytes, size: int) -> str:
    ext = _file_extension(path)
    mime, _ = mimetypes.guess_type(path)
    mime_text = mime or "application/octet-stream"
    head_hex = raw[:24].hex(" ") if raw else ""
    head_b64 = base64.b64encode(raw[:24]).decode("ascii") if raw else ""

    labels = {
        ".exe": "Windows executable",
        ".dll": "Windows dynamic library",
        ".pak": "Packaged game asset archive",
        ".uasset": "Unreal Engine asset",
        ".umap": "Unreal Engine map asset",
        ".ucas": "Unreal Engine container data",
        ".utoc": "Unreal Engine table of contents",
        ".bk2": "Bink video asset",
        ".db": "Database or binary data file",
        ".sqlite": "SQLite database",
        ".sqlite3": "SQLite database",
        ".png": "Image file",
        ".jpg": "Image file",
        ".jpeg": "Image file",
        ".pdf": "PDF document",
        ".zip": "Archive file",
    }
    label = labels.get(ext, "Binary or non-plain-text file")

    parts = [
        f"Type: {label}",
        f"Path: {path}",
        f"Size: {size} bytes",
        f"MIME: {mime_text}",
    ]
    if head_hex:
        parts.append(f"Header bytes: {head_hex}")
    if head_b64:
        parts.append(f"Header base64: {head_b64}")
    parts.append("This file is not plain text, so I can classify it and inspect metadata/header bytes, but I cannot honestly render its internal meaning as readable prose without format-specific tooling.")
    return "\n".join(parts)


def _inspect_sqlite(path: str, max_chars: Optional[int]) -> Dict[str, Any]:
    try:
        conn = sqlite3.connect(path)
        try:
            cur = conn.cursor()
            rows = cur.execute(
                "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            lines = [f"SQLite database: {path}", f"Objects: {len(rows)}"]
            for name, obj_type in rows[:20]:
                try:
                    cols = cur.execute(f"PRAGMA table_info([{name}])").fetchall()
                except Exception:
                    cols = []
                try:
                    count = cur.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0]
                except Exception:
                    count = "unknown"
                col_names = ", ".join([str(c[1]) for c in cols[:8]])
                lines.append(f"- {obj_type} {name}: rows={count}; columns={col_names or 'unknown'}")
            if len(rows) > 20:
                lines.append(f"... and {len(rows) - 20} more objects")
            shrunk = _truncate_text("\n".join(lines), max_chars)
            return {
                "ok": True,
                "result": {
                    "content": shrunk["content"],
                    "encoding": None,
                    "is_text": False,
                    "kind": "sqlite",
                    "size": os.path.getsize(path),
                    "truncated": shrunk["truncated"],
                },
                "error": None,
            }
        finally:
            conn.close()
    except Exception as exc:
        return {"ok": False, "result": None, "error": str(exc)}


def inspect_sqlite_table(path: str, table_name: str, row_limit: int = 8, max_chars: Optional[int] = 4000) -> Dict[str, Any]:
    clean_table = str(table_name or "").strip()
    if not clean_table:
        return {"ok": False, "result": None, "error": "no_table_name_provided"}
    if not os.path.isfile(path):
        return {"ok": False, "result": None, "error": f"file_not_found: {path}"}

    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            exists = cur.execute(
                "SELECT name, type FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
                (clean_table,),
            ).fetchone()
            if not exists:
                return {"ok": False, "result": None, "error": f"sqlite_table_not_found: {clean_table}"}

            cols = cur.execute(f"PRAGMA table_info([{clean_table}])").fetchall()
            col_names = [str(c[1]) for c in cols]
            try:
                count = cur.execute(f"SELECT COUNT(*) FROM [{clean_table}]").fetchone()[0]
            except Exception:
                count = "unknown"

            query = f"SELECT * FROM [{clean_table}] LIMIT {max(1, min(int(row_limit), 50))}"
            rows = cur.execute(query).fetchall()

            lines = [
                f"SQLite table preview: {clean_table}",
                f"Path: {path}",
                f"Rows total: {count}",
                f"Columns: {', '.join(col_names) if col_names else 'unknown'}",
            ]
            if not rows:
                lines.append("Preview rows: table is empty.")
            else:
                lines.append("Preview rows:")
                for idx, row in enumerate(rows, start=1):
                    parts = [f"{col}={_truncate_cell(row[col])}" for col in col_names[:12]]
                    lines.append(f"- row {idx}: " + "; ".join(parts))

            shrunk = _truncate_text("\n".join(lines), max_chars)
            return {
                "ok": True,
                "result": {
                    "content": shrunk["content"],
                    "encoding": None,
                    "is_text": False,
                    "kind": "sqlite_table_preview",
                    "size": os.path.getsize(path),
                    "truncated": shrunk["truncated"],
                },
                "error": None,
            }
        finally:
            conn.close()
    except Exception as exc:
        return {"ok": False, "result": None, "error": str(exc)}


def _inspect_csv_tsv(path: str, raw: bytes, delimiter: str, max_chars: Optional[int]) -> Dict[str, Any]:
    decoded = _decode_text_bytes(raw)
    if not decoded.get("ok"):
        return {"ok": False, "result": None, "error": str(decoded.get("error") or "decode_failed")}

    text = str(decoded.get("text") or "")
    lines_src = text.splitlines()
    reader = csv.reader(lines_src, delimiter=delimiter)
    rows = list(reader)
    row_count = len(rows)
    headers = rows[0] if rows else []
    sample_rows = rows[1:6] if len(rows) > 1 else []
    widths = [len(r) for r in rows[:50]]
    consistent = (len(set(widths)) == 1) if widths else True

    label = "TSV" if delimiter == "\t" else "CSV"
    lines = [
        f"{label} file: {path}",
        f"Rows: {row_count}",
        f"Columns: {len(headers) if headers else 0}",
        f"Encoding: {decoded.get('encoding')}",
        f"Consistent column count: {consistent}",
    ]
    if headers:
        lines.append("Headers: " + ", ".join([_truncate_cell(h, 40) for h in headers[:20]]))
    if sample_rows:
        lines.append("Sample rows:")
        for idx, row in enumerate(sample_rows, start=1):
            cells = row[: min(len(headers) or len(row), 8)]
            rendered = ", ".join([_truncate_cell(cell, 40) for cell in cells])
            lines.append(f"- row {idx}: {rendered}")

    shrunk = _truncate_text("\n".join(lines), max_chars)
    return {
        "ok": True,
        "result": {
            "content": shrunk["content"],
            "encoding": decoded.get("encoding"),
            "is_text": True,
            "kind": "tabular_text",
            "size": os.path.getsize(path),
            "truncated": shrunk["truncated"],
        },
        "error": None,
    }


def _inspect_zip(path: str, max_chars: Optional[int]) -> Dict[str, Any]:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            total_uncompressed = sum(int(i.file_size or 0) for i in infos)
            total_compressed = sum(int(i.compress_size or 0) for i in infos)
            lines = [
                f"Archive: {path}",
                f"Entries: {len(infos)}",
                f"Compressed bytes: {total_compressed}",
                f"Uncompressed bytes: {total_uncompressed}",
            ]
            for info in infos[:30]:
                lines.append(f"- {info.filename} ({info.file_size} bytes)")
            if len(infos) > 30:
                lines.append(f"... and {len(infos) - 30} more entries")
            shrunk = _truncate_text("\n".join(lines), max_chars)
            return {
                "ok": True,
                "result": {
                    "content": shrunk["content"],
                    "encoding": None,
                    "is_text": False,
                    "kind": "archive",
                    "size": os.path.getsize(path),
                    "truncated": shrunk["truncated"],
                },
                "error": None,
            }
    except Exception as exc:
        return {"ok": False, "result": None, "error": str(exc)}


def _pdf_literal_fields(raw: bytes) -> Dict[str, str]:
    text = raw.decode("latin-1", errors="ignore")
    out: Dict[str, str] = {}
    for key in ["Title", "Author", "Subject", "Creator", "Producer"]:
        match = re.search(rf"/{key}\s*\((.*?)\)", text, re.DOTALL)
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip()
            if value:
                out[key.lower()] = value[:240]
    return out


def _inspect_pdf(path: str, raw: bytes, max_chars: Optional[int]) -> Dict[str, Any]:
    meta = _pdf_literal_fields(raw)
    text = raw.decode("latin-1", errors="ignore")
    page_count = len(re.findall(r"/Type\s*/Page\b", text))
    lines = [f"PDF document: {path}", f"Approximate pages: {page_count or 'unknown'}"]
    for key in ["title", "author", "subject", "creator", "producer"]:
        if meta.get(key):
            lines.append(f"{key.title()}: {meta[key]}")
    if len(lines) == 2:
        lines.append("No reliable embedded metadata found in the PDF header/body scan.")
    shrunk = _truncate_text("\n".join(lines), max_chars)
    return {
        "ok": True,
        "result": {
            "content": shrunk["content"],
            "encoding": None,
            "is_text": False,
            "kind": "pdf",
            "size": os.path.getsize(path),
            "truncated": shrunk["truncated"],
        },
        "error": None,
    }


def _inspect_png(raw: bytes) -> Optional[Dict[str, Any]]:
    if len(raw) >= 24 and raw.startswith(b"\x89PNG\r\n\x1a\n"):
        width = int.from_bytes(raw[16:20], "big")
        height = int.from_bytes(raw[20:24], "big")
        return {"format": "PNG", "width": width, "height": height}
    return None


def _inspect_gif(raw: bytes) -> Optional[Dict[str, Any]]:
    if len(raw) >= 10 and (raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a")):
        width = int.from_bytes(raw[6:8], "little")
        height = int.from_bytes(raw[8:10], "little")
        return {"format": "GIF", "width": width, "height": height}
    return None


def _inspect_bmp(raw: bytes) -> Optional[Dict[str, Any]]:
    if len(raw) >= 26 and raw.startswith(b"BM"):
        width = int.from_bytes(raw[18:22], "little", signed=True)
        height = int.from_bytes(raw[22:26], "little", signed=True)
        return {"format": "BMP", "width": abs(width), "height": abs(height)}
    return None


def _inspect_jpeg(raw: bytes) -> Optional[Dict[str, Any]]:
    if len(raw) < 4 or not raw.startswith(b"\xff\xd8"):
        return None
    index = 2
    while index + 9 < len(raw):
        if raw[index] != 0xFF:
            index += 1
            continue
        marker = raw[index + 1]
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            block_len = int.from_bytes(raw[index + 2:index + 4], "big")
            if index + 2 + block_len <= len(raw):
                height = int.from_bytes(raw[index + 5:index + 7], "big")
                width = int.from_bytes(raw[index + 7:index + 9], "big")
                return {"format": "JPEG", "width": width, "height": height}
            return None
        if marker in {0xD8, 0xD9}:
            index += 2
            continue
        block_len = int.from_bytes(raw[index + 2:index + 4], "big")
        if block_len < 2:
            break
        index += 2 + block_len
    return None


def _inspect_webp(raw: bytes) -> Optional[Dict[str, Any]]:
    if len(raw) >= 30 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        chunk = raw[12:16]
        if chunk == b"VP8X" and len(raw) >= 30:
            width = 1 + int.from_bytes(raw[24:27], "little")
            height = 1 + int.from_bytes(raw[27:30], "little")
            return {"format": "WEBP", "width": width, "height": height}
    return None


def _inspect_image(path: str, raw: bytes, max_chars: Optional[int]) -> Optional[Dict[str, Any]]:
    info = _inspect_png(raw) or _inspect_gif(raw) or _inspect_bmp(raw) or _inspect_jpeg(raw) or _inspect_webp(raw)
    if info is None:
        return None
    lines = [
        f"Image file: {path}",
        f"Format: {info['format']}",
        f"Dimensions: {info['width']} x {info['height']}",
        f"Size: {os.path.getsize(path)} bytes",
    ]
    shrunk = _truncate_text("\n".join(lines), max_chars)
    return {
        "ok": True,
        "result": {
            "content": shrunk["content"],
            "encoding": None,
            "is_text": False,
            "kind": "image",
            "size": os.path.getsize(path),
            "truncated": shrunk["truncated"],
        },
        "error": None,
    }


def _inspect_pe(path: str, raw: bytes, max_chars: Optional[int]) -> Optional[Dict[str, Any]]:
    if len(raw) < 0x40 or raw[:2] != b"MZ":
        return None
    pe_offset = int.from_bytes(raw[0x3C:0x40], "little")
    if pe_offset + 24 > len(raw) or raw[pe_offset:pe_offset + 4] != b"PE\x00\x00":
        return None
    machine = int.from_bytes(raw[pe_offset + 4:pe_offset + 6], "little")
    sections = int.from_bytes(raw[pe_offset + 6:pe_offset + 8], "little")
    characteristics = int.from_bytes(raw[pe_offset + 22:pe_offset + 24], "little")
    machine_map = {0x14C: "x86", 0x8664: "x64", 0x1C0: "ARM", 0xAA64: "ARM64"}
    lines = [
        f"PE file: {path}",
        f"Architecture: {machine_map.get(machine, hex(machine))}",
        f"Sections: {sections}",
        f"Characteristics: 0x{characteristics:04X}",
        f"Size: {os.path.getsize(path)} bytes",
    ]
    shrunk = _truncate_text("\n".join(lines), max_chars)
    return {
        "ok": True,
        "result": {
            "content": shrunk["content"],
            "encoding": None,
            "is_text": False,
            "kind": "pe_binary",
            "size": os.path.getsize(path),
            "truncated": shrunk["truncated"],
        },
        "error": None,
    }


def _inspect_wav(path: str, max_chars: Optional[int]) -> Optional[Dict[str, Any]]:
    try:
        with wave.open(path, "rb") as wav_file:
            frames = wav_file.getnframes()
            framerate = wav_file.getframerate()
            duration = (frames / float(framerate)) if framerate else 0.0
            lines = [
                f"WAV audio: {path}",
                f"Channels: {wav_file.getnchannels()}",
                f"Sample width: {wav_file.getsampwidth()} bytes",
                f"Sample rate: {framerate} Hz",
                f"Frames: {frames}",
                f"Duration seconds: {duration:.2f}",
                f"Size: {os.path.getsize(path)} bytes",
            ]
            shrunk = _truncate_text("\n".join(lines), max_chars)
            return {
                "ok": True,
                "result": {
                    "content": shrunk["content"],
                    "encoding": None,
                    "is_text": False,
                    "kind": "audio",
                    "size": os.path.getsize(path),
                    "truncated": shrunk["truncated"],
                },
                "error": None,
            }
    except Exception:
        return None


def _syncsafe_to_int(data: bytes) -> int:
    if len(data) != 4:
        return 0
    return ((data[0] & 0x7F) << 21) | ((data[1] & 0x7F) << 14) | ((data[2] & 0x7F) << 7) | (data[3] & 0x7F)


def _read_id3_string(data: bytes) -> str:
    if not data:
        return ""
    encoding_flag = data[0]
    payload = data[1:]
    encodings = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}
    enc = encodings.get(encoding_flag, "latin-1")
    try:
        return payload.decode(enc, errors="ignore").strip("\x00\ufeff ")
    except Exception:
        return payload.decode("latin-1", errors="ignore").strip("\x00 ")


def _inspect_mp3(path: str, raw: bytes, max_chars: Optional[int]) -> Optional[Dict[str, Any]]:
    if _file_extension(path) != ".mp3":
        return None
    lines = [f"MP3 audio: {path}", f"Size: {os.path.getsize(path)} bytes"]
    if raw.startswith(b"ID3") and len(raw) >= 10:
        version = f"2.{raw[3]}.{raw[4]}"
        tag_size = _syncsafe_to_int(raw[6:10])
        lines.append(f"ID3 version: {version}")
        lines.append(f"Tag bytes: {tag_size}")
        offset = 10
        seen = 0
        while offset + 10 <= min(len(raw), 10 + tag_size) and seen < 6:
            frame_id = raw[offset:offset + 4].decode("latin-1", errors="ignore").strip("\x00")
            if not frame_id:
                break
            frame_size = int.from_bytes(raw[offset + 4:offset + 8], "big")
            if frame_size <= 0 or offset + 10 + frame_size > len(raw):
                break
            frame_data = raw[offset + 10:offset + 10 + frame_size]
            if frame_id in {"TIT2", "TPE1", "TALB", "TYER", "TDRC", "COMM"}:
                value = _truncate_cell(_read_id3_string(frame_data), 120)
                if value:
                    lines.append(f"{frame_id}: {value}")
                    seen += 1
            offset += 10 + frame_size
    else:
        lines.append("No ID3 tag found in the inspected header region.")

    shrunk = _truncate_text("\n".join(lines), max_chars)
    return {
        "ok": True,
        "result": {
            "content": shrunk["content"],
            "encoding": None,
            "is_text": False,
            "kind": "audio_metadata",
            "size": os.path.getsize(path),
            "truncated": shrunk["truncated"],
        },
        "error": None,
    }


def _inspect_mp4(path: str, raw: bytes, max_chars: Optional[int]) -> Optional[Dict[str, Any]]:
    if _file_extension(path) not in {".mp4", ".mov", ".m4v"}:
        return None
    if len(raw) < 16:
        return None
    box_size = int.from_bytes(raw[0:4], "big")
    box_type = raw[4:8]
    if box_type != b"ftyp":
        return None
    major_brand = raw[8:12].decode("latin-1", errors="ignore").strip()
    minor_version = int.from_bytes(raw[12:16], "big")
    compatible = []
    for idx in range(16, min(len(raw), box_size), 4):
        brand = raw[idx:idx + 4]
        if len(brand) == 4:
            compatible.append(brand.decode("latin-1", errors="ignore").strip())
    lines = [
        f"MP4 container: {path}",
        f"Size: {os.path.getsize(path)} bytes",
        f"Major brand: {major_brand or 'unknown'}",
        f"Minor version: {minor_version}",
    ]
    if compatible:
        lines.append("Compatible brands: " + ", ".join([b for b in compatible[:8] if b]))
    shrunk = _truncate_text("\n".join(lines), max_chars)
    return {
        "ok": True,
        "result": {
            "content": shrunk["content"],
            "encoding": None,
            "is_text": False,
            "kind": "video_metadata",
            "size": os.path.getsize(path),
            "truncated": shrunk["truncated"],
        },
        "error": None,
    }


def inspect_file(path: str, max_chars: Optional[int] = None) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {"ok": False, "result": None, "error": f"file_not_found: {path}"}

    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except Exception as exc:
        return {"ok": False, "result": None, "error": str(exc)}

    size = len(raw)
    ext = _file_extension(path)

    if ext in {".sqlite", ".sqlite3", ".db"}:
        sqlite_result = _inspect_sqlite(path, max_chars)
        if sqlite_result.get("ok"):
            return sqlite_result

    if ext in {".csv", ".tsv"}:
        table_result = _inspect_csv_tsv(path, raw, "\t" if ext == ".tsv" else ",", max_chars)
        if table_result.get("ok"):
            return table_result

    if ext in {".zip"}:
        zip_result = _inspect_zip(path, max_chars)
        if zip_result.get("ok"):
            return zip_result

    if ext in {".pdf"}:
        return _inspect_pdf(path, raw, max_chars)

    image_result = _inspect_image(path, raw, max_chars)
    if image_result is not None:
        return image_result

    if ext in {".exe", ".dll"}:
        pe_result = _inspect_pe(path, raw, max_chars)
        if pe_result is not None:
            return pe_result

    if ext in {".wav"}:
        wav_result = _inspect_wav(path, max_chars)
        if wav_result is not None:
            return wav_result

    if ext in {".mp3"}:
        mp3_result = _inspect_mp3(path, raw, max_chars)
        if mp3_result is not None:
            return mp3_result

    if ext in {".mp4", ".mov", ".m4v"}:
        mp4_result = _inspect_mp4(path, raw, max_chars)
        if mp4_result is not None:
            return mp4_result

    if not _looks_binary(raw, path) or is_probably_text_path(path):
        decoded = _decode_text_bytes(raw)
        if decoded.get("ok"):
            text = str(decoded.get("text") or "")
            shrunk = _truncate_text(text, max_chars)
            return {
                "ok": True,
                "result": {
                    "content": shrunk["content"],
                    "encoding": decoded.get("encoding"),
                    "is_text": True,
                    "kind": "structured_text" if ext in STRUCTURED_TEXT_EXTENSIONS else "text",
                    "size": size,
                    "truncated": shrunk["truncated"],
                },
                "error": None,
            }

    summary = _binary_summary(path, raw, size)
    return {
        "ok": True,
        "result": {
            "content": summary,
            "encoding": None,
            "is_text": False,
            "kind": "binary",
            "size": size,
            "truncated": False,
        },
        "error": None,
    }