#!/usr/bin/env python3
"""Prepare this standalone repository for HACS. Does not publish anything."""
import argparse
import json
import re
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('owner', help='GitHub user or organization owning the repository')
parser.add_argument('repository', help='Repository name, e.g. hophop-casa')
parser.add_argument('--maintainer', required=True, help='Individual GitHub username, without @')
args = parser.parse_args()
for field in ('owner', 'maintainer'):
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', getattr(args, field)):
        parser.error(f'{field} must be a GitHub username/organization name, without URLs or @')
if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', args.repository) or args.repository in ('.', '..'):
    parser.error('repository must be a GitHub repository name, not a URL')
root = Path(__file__).resolve().parents[1]
path = root / 'custom_components/hophop_casa/manifest.json'
manifest = json.loads(path.read_text())
url = f'https://github.com/{args.owner}/{args.repository}'
manifest.update(documentation=url+'#readme', issue_tracker=url+'/issues', codeowners=['@'+args.maintainer])
ordered = {key: manifest[key] for key in (
    'domain', 'name', *sorted(key for key in manifest if key not in ('domain', 'name'))
)}
path.write_text(json.dumps(ordered, indent=2)+'\n')
readme = root / 'README.md'
text = readme.read_text().replace('GITHUB_OWNER/REPOSITORY', args.owner+'/'+args.repository)
readme.write_text(text)
print('Metadata ready for '+url)
print('Version: '+manifest['version'])
print('Server .env: CLIENT_GITHUB_REPOSITORY='+args.owner+'/'+args.repository)
print('Publish this directory as the repository root, then run GitHub Actions.')
