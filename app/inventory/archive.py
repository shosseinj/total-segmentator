"""Extract archives without executing files; enforce limits while streaming."""
import shutil, stat, zipfile
from pathlib import Path, PurePosixPath

def extract_zip(path,destination,max_expanded=30*1024**3,max_entries=100000,max_ratio=1000):
    destination=Path(destination)
    if destination.exists():raise ValueError('EXTRACTION_DIRECTORY_EXISTS')
    files=[];written=0
    try:
        with zipfile.ZipFile(path) as bundle:
            entries=bundle.infolist()
            if len(entries)>max_entries or sum(e.file_size for e in entries)>max_expanded:raise ValueError('ARCHIVE_LIMIT')
            seen=set()
            for e in entries:
                name=e.filename;p=PurePosixPath(name);mode=e.external_attr>>16
                if (p.is_absolute() or '\\' in name or ':' in name or '\x00' in name
                    or any(part in ('..','.') for part in name.split('/')) or stat.S_ISLNK(mode)
                    or mode and stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFDIR)
                    or name in seen):raise ValueError('UNSAFE_ARCHIVE')
                seen.add(name)
                if e.flag_bits&1:raise ValueError('ENCRYPTED_ZIP')
                if e.file_size/max(1,e.compress_size)>max_ratio:raise ValueError('ARCHIVE_LIMIT')
                if e.is_dir():continue
                target=destination/p;target.parent.mkdir(parents=True,exist_ok=True)
                n=0
                with bundle.open(e) as source,target.open('xb') as out:
                    for block in iter(lambda:source.read(1024*1024),b''):
                        n+=len(block);written+=len(block)
                        if n>e.file_size or written>max_expanded:raise ValueError('ARCHIVE_LIMIT')
                        out.write(block)
                if n!=e.file_size:raise ValueError('CORRUPT_ZIP')
                files.append(target)
        return files
    except (zipfile.BadZipFile,EOFError):
        shutil.rmtree(destination,ignore_errors=True)
        raise ValueError('CORRUPT_ZIP') from None
    except BaseException:
        shutil.rmtree(destination,ignore_errors=True)
        raise
