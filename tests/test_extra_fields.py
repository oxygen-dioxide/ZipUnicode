__author__ = "Duc Tin"

import sys
import os
import struct
import pytest
import zipfile
from pathlib import Path
from datetime import datetime
from zip_unicode.time_utils import parse_extra_fields
from zip_unicode.main import ZipHandler

# Ensure the project root is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def clean_up(path: Path):
    if path.is_dir():
        for f in path.iterdir():
            clean_up(f) if f.is_dir() else f.unlink()
        else:
            path.rmdir()
    else:
        path.unlink()


def test_parse_ntfs_extra():
    # Construct NTFS extra field (0x000a)
    # Reserved (4 bytes) + Tag1 (2 bytes) + Size1 (2 bytes) + Mtime (8) + Atime (8) + Ctime (8)
    
    # Timestamps are 100ns intervals since Jan 1 1601
    # Let's use a known date: 2020-01-01 00:00:00 UTC
    # Unix timestamp: 1577836800
    # FILETIME: (1577836800 + 11644473600) * 10000000 = 132223104000000000
    
    ctime_ft = 132223104000000000
    mtime_ft = ctime_ft + 10_000_000 # +1 second
    atime_ft = ctime_ft + 20_000_000 # +2 seconds
    
    ntfs_data = struct.pack('<HHQQQ', 
                            0x0001, # Tag1
                            24,     # Size1
                            mtime_ft, # Mtime
                            atime_ft, # Atime
                            ctime_ft  # Ctime
                           )
    
    extra_data = struct.pack('<HH4s', 0x000a, len(ntfs_data) + 4, b'\x00'*4) + ntfs_data
    
    timestamps = parse_extra_fields(extra_data)
    
    assert 'mtime' in timestamps
    assert 'atime' in timestamps
    assert 'ctime' in timestamps
    assert timestamps['mtime'] == pytest.approx(1577836801.0)
    assert timestamps['atime'] == pytest.approx(1577836802.0)
    assert timestamps['ctime'] == pytest.approx(1577836800.0)


def test_parse_extended_timestamp():
    # Construct Extended Timestamp extra field (0x5455)
    # Flags (1 byte) + Mtime (4) + Atime (4) + Ctime (4)
    
    ts_ctime = 1577836800 # 2020-01-01 00:00:00 UTC
    ts_mtime = ts_ctime + 1
    ts_atime = ts_ctime + 2
    
    # Flags: 1 (Mtime) | 2 (Atime) | 4 (Ctime) = 7
    ext_data = struct.pack('<BIII', 7, ts_ctime, ts_mtime, ts_atime)
    
    extra_data = struct.pack('<HH', 0x5455, len(ext_data)) + ext_data
    
    timestamps = parse_extra_fields(extra_data)
    
    assert 'ctime' in timestamps
    assert 'mtime' in timestamps
    assert 'atime' in timestamps
    assert timestamps['ctime'] == ts_ctime
    assert timestamps['mtime'] == ts_mtime
    assert timestamps['atime'] == ts_atime


def test_extraction_creation_time():
    """Verify that extracted files have correct timestamps (mtime, atime, ctime) matching the zip info"""
    
    # Use one of the existing test zip files
    zip_filename = '20200524_フラット.zip'
    zip_path = os.path.join(os.path.dirname(__file__), zip_filename)
    
    if not os.path.exists(zip_path):
        pytest.skip(f"Test zip file not found: {zip_path}")

    # Extract to a subfolder in tests directory
    extract_dir = Path(os.path.dirname(__file__)) / 'test_creation_time_extract'
    
    try:
        # Extract using ZipHandler
        zh = ZipHandler(zip_path, extract_path=str(extract_dir))
        zh.extract_all()
        
        # Check the extracted files against zip info
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for info in zf.infolist():
                decoded_name = None
                for dec, orig in zh.name_map.items():
                    if orig == info.filename:
                        decoded_name = dec
                        break
                
                if not decoded_name:
                    # Should not happen if ZipHandler works correctly
                    continue
                    
                target_path = extract_dir / decoded_name
                
                if not target_path.exists() or target_path.is_dir():
                    continue
                
                # Calculate expected timestamps
                dt = datetime(*info.date_time)
                expected_mtime = expected_atime = expected_ctime = dt.timestamp()
                
                if info.extra:
                    extra_ts = parse_extra_fields(info.extra)
                    if 'ctime' in extra_ts: expected_ctime = extra_ts['ctime']
                    if 'mtime' in extra_ts: expected_mtime = extra_ts['mtime']
                    if 'atime' in extra_ts: expected_atime = extra_ts['atime']
                
                # Get actual timestamps
                stat = target_path.stat()
                
                # Verify
                # Allow small delta (1.0s) for float precision/OS resolution
                assert abs(stat.st_mtime - expected_mtime) < 1.0, f"Mtime mismatch for {decoded_name}"
                assert abs(stat.st_atime - expected_atime) < 1.0, f"Atime mismatch for {decoded_name}"
                
                if os.name == 'nt':
                    assert abs(stat.st_ctime - expected_ctime) < 1.0, f"Ctime mismatch for {decoded_name}"

    finally:
        # Clean up
        if extract_dir.exists():
            clean_up(extract_dir)
