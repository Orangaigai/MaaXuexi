"""Hash explicitly Git-tracked source files; never recurse through runtime data."""
import hashlib
import json
from pathlib import Path
import runpy
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def update():
    validator=runpy.run_path(str(ROOT/'scripts/Check-Repository.py'))['public_path']
    result=subprocess.run(['git','-C',str(ROOT),'ls-files','-z'],capture_output=True,check=True)
    names=result.stdout.decode('utf-8').strip('\0').split('\0')
    manifest={}
    for name in sorted(names):
        if name=='config/source-manifest.json':continue
        file=(ROOT/name).resolve()
        if not validator(name) or not file.is_relative_to(ROOT) or not file.is_file() or file.is_symlink():
            raise ValueError('Refusing unsafe, private or missing tracked source: '+name)
        manifest[name]=hashlib.sha256(file.read_bytes()).hexdigest()
    if not manifest:raise ValueError('No tracked source files')
    target=ROOT/'config/source-manifest.json'
    temporary=target.with_suffix('.tmp')
    temporary.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n','utf-8')
    temporary.replace(target)
    print(f'Updated {len(manifest)} source hashes. Stage config/source-manifest.json before committing.')


if __name__=='__main__':update()
