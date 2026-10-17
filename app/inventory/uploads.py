"""Validate ZIP envelopes before queueing local DICOM processing."""
import zipfile


def validate_zip(path,settings):
    try:
        with zipfile.ZipFile(path) as archive:
            entries=archive.infolist()
            if not any(not entry.is_dir() for entry in entries):
                raise ValueError('EMPTY_ZIP')
            if any(entry.flag_bits&1 for entry in entries):
                raise ValueError('ENCRYPTED_ZIP')
            if (len(entries)>settings.max_entries
                or sum(entry.file_size for entry in entries)>settings.max_expanded
                or any(entry.file_size/max(1,entry.compress_size)>settings.max_ratio for entry in entries)):
                raise ValueError('ARCHIVE_LIMIT')
    except (zipfile.BadZipFile,EOFError):
        raise ValueError('CORRUPT_ZIP') from None
