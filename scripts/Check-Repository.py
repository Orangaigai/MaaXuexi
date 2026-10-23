"""Read-only checks for a source checkout or source ZIP. No paid/device calls."""
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote,urlsplit

ROOT=Path(__file__).resolve().parents[1]
REQUIRED=['README.md','LICENSE','CONTRIBUTING.md','SECURITY.md','ACKNOWLEDGEMENTS.md','CHANGELOG.md',
          'docs/README.md','docs/installation.md','docs/configuration.md','docs/usage.md',
          'docs/troubleshooting.md','docs/development.md','docs/architecture.md',
          'scripts/Install-MaaXuexi.ps1','.github/workflows/ci.yml']


def public_path(relative):
    path=Path(relative)
    return (not path.is_absolute() and '..' not in path.parts
        and not any(part in ('.venv','logs','runtime','debug','__pycache__','.git') for part in path.parts)
        and path.suffix.lower() not in ('.dpapi','.sqlite3','.log','.lnk','.pyc','.pem','.pfx','.p12')
        and (not relative.startswith('config/') or relative=='config/source-manifest.json'
             or relative.endswith('.example.json')))


def check(root=ROOT):
    root=Path(root).resolve();errors=[]
    try:
        manifest=json.loads((root/'config/source-manifest.json').read_text('utf-8-sig'))
        if not isinstance(manifest,dict) or not manifest:raise ValueError()
    except (OSError,ValueError):return ['Missing or invalid source manifest']
    names=set(manifest)|{'config/source-manifest.json'}
    for name in REQUIRED:
        if name not in names or not (root/name).is_file():errors.append('Missing required source: '+name)
    for name,expected in manifest.items():
        file=(root/name).resolve()
        if not public_path(name) or not file.is_relative_to(root):errors.append('Private or unsafe source path: '+name);continue
        if not file.is_file():errors.append('Missing source: '+name);continue
        if file.stat().st_size>=100*1024*1024:errors.append('File exceeds GitHub regular-file limit: '+name)
        if hashlib.sha256(file.read_bytes()).hexdigest()!=expected:errors.append('Source hash mismatch: '+name)
    for name in names:
        if not name.endswith('.md') or not (root/name).is_file():continue
        source=(root/name).read_text('utf-8-sig')
        source=re.sub(r'```.*?```','',source,flags=re.S)
        for target in re.findall(r'!?\[[^\]]*\]\(([^\s)]+)(?:\s+"[^"]*")?\)',source):
            target=target.strip('<>');url=urlsplit(target)
            if url.scheme or target.startswith('#') or target.startswith('//'):continue
            file=((root/name).parent/unquote(url.path)).resolve()
            if not file.is_relative_to(root) or file.relative_to(root).as_posix() not in names:
                errors.append('Unpublished local link: '+name+' -> '+target)
    for name in ('emulator','local-task-plan','comment-policy'):
        path=root/f'config/{name}.example.json'
        try:json.loads(path.read_text('utf-8-sig'))
        except (OSError,ValueError):errors.append('Missing or invalid example: '+path.name)
    return errors


if __name__=='__main__':
    errors=check()
    for error in errors:print(error)
    print('Repository checks passed.' if not errors else f'{len(errors)} repository check(s) failed.')
    sys.exit(bool(errors))
