__author__ = "Duc Tin"

import os
import time
import zipfile
import logging
import struct
from datetime import datetime
from pathlib import Path

logger = logging.getLogger('zip_unicode')

def parse_extra_fields(extra_bytes: bytes) -> dict:
    """Parse zip extra fields to extract timestamps (mtime, atime, ctime)
        The stored time in the extra fields is in UTC.
    """
    timestamps = {}

    offset = 0
    while offset < len(extra_bytes):
        if offset + 4 > len(extra_bytes):
            break
        
        header_id, data_size = struct.unpack_from('<HH', extra_bytes, offset)
        offset += 4
        
        if offset + data_size > len(extra_bytes):
            break
            
        data = extra_bytes[offset:offset + data_size]
        offset += data_size

        # NTFS (0x000a)
        if header_id == 0x000a:
            # Reserved (4 bytes) + Tag1 (2 bytes) + Size1 (2 bytes)
            if len(data) >= 32: # Minimal size for Mtime, Atime, Ctime
                # Skip reserved (4 bytes)
                tag1_offset = 4
                while tag1_offset < len(data):
                    if tag1_offset + 4 > len(data):
                        break
                    tag1, size1 = struct.unpack_from('<HH', data, tag1_offset)
                    tag1_offset += 4
                    
                    if tag1 == 0x0001: # NTFS Attribute Tag 1
                        if size1 >= 24 and tag1_offset + 24 <= len(data):
                            mtime, atime, ctime = struct.unpack_from('<QQQ', data, tag1_offset)
                            # Windows FILETIME is 100-nanosecond intervals since Jan 1, 1601 (UTC)
                            # Unix epoch is Jan 1, 1970
                            # Difference is 11644473600 seconds
                            EPOCH_AS_FILETIME = 116444736000000000
                            timestamps['ctime'] = (ctime - EPOCH_AS_FILETIME) / 10_000_000
                            timestamps['mtime'] = (mtime - EPOCH_AS_FILETIME) / 10_000_000
                            timestamps['atime'] = (atime - EPOCH_AS_FILETIME) / 10_000_000
                        break
                    tag1_offset += size1

        # Extended Timestamp (0x5455)
        elif header_id == 0x5455:
            if len(data) >= 1:
                flags = data[0]
                current_offset = 1
                
                # Ctime
                if flags & 4 and current_offset + 4 <= len(data):
                    timestamps['ctime'] = struct.unpack_from('<I', data, current_offset)[0]
                    current_offset += 4

                # Mtime
                if flags & 1 and current_offset + 4 <= len(data):
                    timestamps['mtime'] = struct.unpack_from('<I', data, current_offset)[0]
                    current_offset += 4
                
                # Atime
                if flags & 2 and current_offset + 4 <= len(data):
                    timestamps['atime'] = struct.unpack_from('<I', data, current_offset)[0]
                    current_offset += 4
                
        else:
            logger.warning(f"Unknown extra field header ID: {header_id}")

    return timestamps


def set_creation_time_windows(path: Path, timestamp: float):
    """Set the creation time of a file on Windows"""
    import ctypes
    from ctypes import wintypes

    # Windows FILETIME starts from Jan 1, 1601.
    # 116444736000000000 is the number of 100ns intervals between 1601 and 1970.
    EPOCH_AS_FILETIME = 116444736000000000
    HUNDREDS_OF_NANOSECONDS = 10000000

    ctime = int(timestamp * HUNDREDS_OF_NANOSECONDS + EPOCH_AS_FILETIME)

    lpCreationTime = wintypes.FILETIME(ctime & 0xFFFFFFFF, ctime >> 32)
    lpLastAccessTime = wintypes.FILETIME(0, 0)
    lpLastWriteTime = wintypes.FILETIME(0, 0)

    handle = ctypes.windll.kernel32.CreateFileW(
        str(path),
        256,  # GENERIC_WRITE
        0,    # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
        None,
        3,    # OPEN_EXISTING
        128,  # FILE_ATTRIBUTE_NORMAL
        None
    )

    if handle == -1:
        # Failed to open file
        return

    try:
        ctypes.windll.kernel32.SetFileTime(
            handle,
            ctypes.byref(lpCreationTime),
            None, # lpLastAccessTime
            None  # lpLastWriteTime
        )
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def set_file_time(path: Path, zip_info: zipfile.ZipInfo):
    """Set the modification time of a file or directory to match the ZipInfo"""
    try:
        # Default to legacy zip timestamp
        dt = datetime(*zip_info.date_time)
        ctime = mtime = atime = dt.timestamp()
        
        # Try to parse extra fields for better precision
        if zip_info.extra:
            extra_ts = parse_extra_fields(zip_info.extra)
            if 'mtime' in extra_ts:
                mtime = extra_ts['mtime']
            if 'atime' in extra_ts:
                atime = extra_ts['atime']
            if 'ctime' in extra_ts:
                ctime = extra_ts['ctime']
        
        os.utime(path, (atime, mtime))

        # On Windows, set creation time to ctime (or mtime fallback)
        if os.name == 'nt':
            set_creation_time_windows(path, ctime)

    except Exception as e:
        logger.warning(f"Could not set time for {path}: {e}")
