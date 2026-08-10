"""Download pack helpers — presence-scoped bulk pull over the same WebDAV path MC uses.

Slice 1: list a folder as a pack (exclude previews by default), stream one file,
and stream a ZIP_STORED archive without buffering whole multi-GB members.
"""
from __future__ import annotations

import struct
import time
import zlib
from typing import AsyncIterator, Iterable, List, Optional, Sequence, Tuple


# Default exclude: previews and junk. Captioned + masters stay in.
DEFAULT_EXCLUDE_SUBSTR = ("preview",)
DEFAULT_EXCLUDE_NAMES = {".ds_store", "thumbs.db", ".ds_store"}


def pack_name_excluded(
    name: str,
    *,
    exclude_preview: bool = True,
    extra_substrings: Sequence[str] = (),
) -> bool:
    """True if this basename should be left out of a download pack."""
    n = (name or "").strip()
    if not n or n in (".", ".."):
        return True
    lower = n.lower()
    if lower in DEFAULT_EXCLUDE_NAMES:
        return True
    if exclude_preview and "preview" in lower:
        return True
    for sub in extra_substrings:
        s = (sub or "").strip().lower()
        if s and s in lower:
            return True
    return False


def filter_pack_items(
    items: Iterable[dict],
    *,
    exclude_preview: bool = True,
    query: str = "",
    files_only: bool = True,
) -> List[dict]:
    """Filter list_files-style items into a pack list."""
    q = (query or "").strip().lower()
    out: List[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if files_only and it.get("is_dir"):
            continue
        name = it.get("name") or ""
        if pack_name_excluded(name, exclude_preview=exclude_preview):
            continue
        if q and q not in name.lower() and q not in (it.get("path") or "").lower():
            continue
        out.append(it)
    return out


def _dos_time(ts: Optional[float] = None) -> Tuple[int, int]:
    t = time.localtime(ts if ts is not None else time.time())
    dos_time = (t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2)
    dos_date = ((t.tm_year - 1980) << 9) | (t.tm_mon << 5) | t.tm_mday
    return dos_time, dos_date


async def iter_zip_stored(
    members: Sequence[Tuple[str, AsyncIterator[bytes]]],
) -> AsyncIterator[bytes]:
    """Yield a ZIP archive (store only) for async byte members.

    Uses the data-descriptor form so each member can stream without knowing
    CRC/size up front. Suitable for already-compressed media (mp4).
    """
    central: List[bytes] = []
    offset = 0
    count = 0

    for arcname, body_iter in members:
        name = (arcname or "file").replace("\\", "/").lstrip("/")
        # Zip path traversal guard
        parts = [p for p in name.split("/") if p not in ("", ".", "..")]
        if not parts:
            continue
        name = "/".join(parts)
        name_b = name.encode("utf-8")
        dos_time, dos_date = _dos_time()

        # bit 3: data descriptor follows; general purpose bit 11: UTF-8
        flags = 0x08 | 0x800
        local = struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50,  # local file header signature
            20,  # version needed
            flags,
            0,  # method store
            dos_time,
            dos_date,
            0,  # crc (descriptor)
            0,  # comp size
            0,  # uncomp size
            len(name_b),
            0,  # extra len
        ) + name_b

        yield local
        local_offset = offset
        offset += len(local)

        crc = 0
        size = 0
        async for chunk in body_iter:
            if not chunk:
                continue
            crc = zlib.crc32(chunk, crc) & 0xFFFFFFFF
            size += len(chunk)
            offset += len(chunk)
            yield chunk

        # data descriptor (with signature). 32-bit sizes; multi-GB single
        # members still fit under 4GiB-1 for typical short packs. Oversize
        # files should use single-file download, not zip.
        if size >= 0xFFFFFFFF:
            raise ValueError(
                f"Zip member too large for non-zip64 pack: {name} ({size} bytes)"
            )
        desc = struct.pack("<IIII", 0x08074B50, crc, size, size)
        yield desc
        offset += len(desc)

        # central directory header (sizes known now)
        central.append(
            struct.pack(
                "<IHHHHHHIIIHHHHHII",
                0x02014B50,
                20,  # version made by
                20,  # version needed
                flags,
                0,  # store
                dos_time,
                dos_date,
                crc,
                size,
                size,
                len(name_b),
                0,  # extra
                0,  # comment
                0,  # disk start
                0,  # int attrs
                0,  # ext attrs
                local_offset,
            )
            + name_b
        )
        count += 1

    if count == 0:
        # Empty zip still valid
        end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0, 0, 0, 0, 0)
        yield end
        return

    central_blob = b"".join(central)
    central_offset = offset
    yield central_blob
    offset += len(central_blob)

    end = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        count,
        count,
        len(central_blob),
        central_offset,
        0,
    )
    yield end


def format_size_label(n: int) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"
