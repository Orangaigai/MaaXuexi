"""Conservative retention for generated logs and trace images only."""
from pathlib import Path
import re
import time


def prune_logs(root='logs',*,keep_trace=None,now=None,max_age_days=14,max_bytes=256*1024*1024):
    root=Path(root).resolve();now=time.time() if now is None else now
    if not root.is_dir():return dict(deleted_files=0,freed_bytes=0)
    groups=[]
    def safe(path):
        return not path.is_symlink() and not path.is_junction() and path.resolve().is_relative_to(root)
    for path in root.iterdir():
        if (safe(path) and path.is_file() and path.suffix=='.log'
                and re.fullmatch(r'(?:events-\d{8}|daily-[\d-]+|launcher-[\d-]+|[\d-]+\.(?:stdout|stderr))\.log',path.name)):
            groups.append([path])
    traces=root/'trace'
    if traces.is_dir() and safe(traces):
        for folder in traces.iterdir():
            if not safe(folder) or not folder.is_dir() or not re.fullmatch(r'[a-f0-9]{12}',folder.name):continue
            if keep_trace and folder.name==keep_trace:continue
            files=list(folder.iterdir())
            if files and all(safe(p) and p.is_file() and p.suffix in ('.png','.json') for p in files):groups.append(files)
    groups.sort(key=lambda files:max(p.stat().st_mtime for p in files))
    sizes={p:p.stat().st_size for files in groups for p in files}
    total=sum(sizes.values());deleted=freed=0
    for files in groups:
        modified=max(p.stat().st_mtime for p in files)
        if modified>now-3600:continue
        if modified>=now-max_age_days*86400 and total<=max_bytes:continue
        for path in files:
            try:
                if not safe(path):continue
                size=sizes[path];path.unlink();total-=size;freed+=size;deleted+=1
            except OSError:continue
        folder=files[0].parent
        if folder.parent==traces and safe(folder):
            try:folder.rmdir()
            except OSError:pass
    return dict(deleted_files=deleted,freed_bytes=freed)
