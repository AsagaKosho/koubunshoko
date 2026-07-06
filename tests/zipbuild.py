"""テスト用ZIP生成ヘルパ。

標準の zipfile はASCII以外のエントリ名を必ずUTF-8フラグ付きで書き込むため、
e-Gov 実物と同じ「UTF-8フラグなし・cp932バイト列のエントリ名」を再現できない。
ここではZIPフォーマットを直接組み立てて、エントリ名のバイト列とフラグを
完全に制御する（cp437→cp932名修復のテストケースを作るため）。
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

_DOS_DATE = ((2026 - 1980) << 9) | (7 << 5) | 6  # 2026-07-06
_DOS_TIME = (12 << 11) | (0 << 5) | 0


def write_zip(
    dest: Path,
    entries: list[tuple[str, bytes]],
    encoding: str = "cp932",
    utf8_flag: bool = False,
) -> Path:
    """entries を無圧縮(stored)で dest に書き込む。

    encoding='cp932', utf8_flag=False で e-Gov 実物と同じ
    「cp437として読まれてしまう日本語エントリ名」を再現する。
    """
    blob = bytearray()
    central = bytearray()
    for name, data in entries:
        name_bytes = name.encode(encoding)
        crc = zlib.crc32(data) & 0xFFFFFFFF
        flags = 0x800 if utf8_flag else 0
        offset = len(blob)
        blob += struct.pack(
            "<4sHHHHHIIIHH",
            b"PK\x03\x04", 20, flags, 0, _DOS_TIME, _DOS_DATE,
            crc, len(data), len(data), len(name_bytes), 0,
        )
        blob += name_bytes + data
        central += struct.pack(
            "<4sHHHHHHIIIHHHHHII",
            b"PK\x01\x02", 20, 20, flags, 0, _DOS_TIME, _DOS_DATE,
            crc, len(data), len(data), len(name_bytes),
            0, 0, 0, 0, 0, offset,
        )
        central += name_bytes
    eocd = struct.pack(
        "<4sHHHHIIH",
        b"PK\x05\x06", 0, 0, len(entries), len(entries),
        len(central), len(blob), 0,
    )
    dest.write_bytes(bytes(blob) + bytes(central) + eocd)
    return dest


def zip_from_dir(
    src_dir: Path,
    dest: Path,
    inner_dir: str | None = None,
    encoding: str = "cp932",
    utf8_flag: bool = False,
) -> Path:
    """フォルダ一式をZIP化する。inner_dir を指定すると1階層包む（到達番号フォルダ構造）。"""
    entries: list[tuple[str, bytes]] = []
    for p in sorted(src_dir.rglob("*")):
        if not p.is_file():
            continue
        arcname = p.relative_to(src_dir).as_posix()
        if inner_dir:
            arcname = f"{inner_dir}/{arcname}"
        entries.append((arcname, p.read_bytes()))
    return write_zip(dest, entries, encoding=encoding, utf8_flag=utf8_flag)
