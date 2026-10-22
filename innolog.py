"""
Read Inno Setup uninstall logs (unins000.dat) to learn exactly which files and
folders an installer created. Many plugin vendors use Inno Setup, so these
logs give precise data folders (presets, samples, config) instead of guesses.

File layout (all little-endian):
  header (448 bytes): ID[64], AppId[128], AppName[128], Version, NumRecs,
                      EndOffset, Flags, Reserved[27], CRC
  blocks up to EndOffset: Size, NotSize (= ~Size), CRC32, then Size bytes
  records (concatenated block payloads): Typ (u16), ExtraData (i32),
                      DataSize (u32), then DataSize bytes of strings
  string: length byte (<= 0xFC), or 0xFD + i16, or 0xFE + i32; a negative
          length means UTF-16 of |length| bytes; 0xFF ends the list
"""

import struct
import zlib
from dataclasses import dataclass, field

_HEADER_SIZE = 448
_ID = b'Inno Setup Uninstall Log'

# Record types (Inno Setup's Undo.pas)
_START_INSTALL = 0x10
_DELETE_DIR = 0x81      # a directory the installer created
_DELETE_FILE = 0x82     # a file the installer installed


@dataclass
class InnoLog:
    app_name: str
    source: str = ''                          # where this .dat file was read from
    app_dir: str = ''                         # the install's {app} folder
    dirs: list = field(default_factory=list)  # created directories
    files: list = field(default_factory=list) # installed files


def read_log(path: str) -> InnoLog:
    """Parse an unins*.dat file. Raises ValueError if it isn't a valid log."""
    with open(path, 'rb') as f:
        d = f.read()
    if len(d) < _HEADER_SIZE or not d.startswith(_ID):
        raise ValueError('not an Inno Setup uninstall log')

    app_name = d[192:320].split(b'\0')[0].decode('latin-1')
    _version, num_recs, end, _flags = struct.unpack_from('<iiII', d, 320)
    end = min(end, len(d))

    data = bytearray()
    pos = _HEADER_SIZE
    while pos + 12 <= end:
        size, not_size, crc = struct.unpack_from('<IIi', d, pos)
        block = d[pos + 12:pos + 12 + size]
        if not_size != (~size & 0xFFFFFFFF) or len(block) != size \
                or zlib.crc32(block) != crc & 0xFFFFFFFF:
            raise ValueError('corrupt block')
        data += block
        pos += 12 + size

    log = InnoLog(app_name=app_name, source=path)
    o = 0
    for _ in range(num_recs):
        if o + 10 > len(data):
            break
        typ, _extra, size = struct.unpack_from('<HiI', data, o)
        o += 10
        if typ in (_START_INSTALL, _DELETE_DIR, _DELETE_FILE):
            strings = _read_strings(bytes(data[o:o + size]))
            if typ == _START_INSTALL and len(strings) >= 3:
                log.app_dir = strings[2]      # computer, user, {app}, timestamp
            elif typ == _DELETE_DIR and strings:
                log.dirs.append(strings[0])
            elif typ == _DELETE_FILE and strings:
                log.files.append(strings[0])
        o += size
    return log


def _read_strings(p: bytes) -> list:
    out = []
    i = 0
    while i < len(p) and p[i] != 0xFF:
        b = p[i]
        i += 1
        if b <= 0xFC:
            n = b
        elif b == 0xFD:
            n = struct.unpack_from('<h', p, i)[0]
            i += 2
        else:
            n = struct.unpack_from('<i', p, i)[0]
            i += 4
        raw = p[i:i + abs(n)]
        i += abs(n)
        out.append(raw.decode('utf-16-le', 'replace') if n < 0
                   else raw.decode('latin-1'))
    return out
