"""Conservative source-tree scan. Prints locations/categories, never matched values."""
import ipaddress
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'API credential': re.compile(r'\bsk-[A-Za-z0-9_-]{16,}'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'machine path': re.compile(r'/Users/|/home/[a-z][a-z0-9_-]+/'),
    'AWS access key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'GitHub token': re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}'),
    'credential URL': re.compile(r'https?://[^\s/:]+:[^\s/@]+@'),
}
IP = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
PRIVATE = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
issues = []
files = 0
for path in sorted(ROOT.rglob('*')):
    relative = path.relative_to(ROOT)
    if any(p in ('.git', '__pycache__') for p in relative.parts) or not path.is_file():
        continue
    files += 1
    if path.name == '.env' or relative.parts[0] == 'secrets' or str(relative) == 'config/config.json':
        issues.append((str(relative), 0, 'runtime credential/config file'))
    try:
        content = path.read_text()
    except UnicodeDecodeError:
        issues.append((str(relative), 0, 'unexpected binary')); continue
    for number, line in enumerate(content.splitlines(), 1):
        # Patterns and private network ranges in this scanner are definitions, not leaked data.
        if path == Path(__file__).resolve(): continue
        for label, pattern in PATTERNS.items():
            if pattern.search(line): issues.append((str(relative), number, label))
        for match in IP.findall(line):
            try: address = ipaddress.ip_address(match)
            except ValueError: continue
            if any(address in network for network in PRIVATE):
                issues.append((str(relative), number, 'private IPv4'))
if issues:
    for path, line, label in issues: print(f'{path}:{line}: {label}')
    sys.exit(1)
print(f'PASS: {files} publication files scanned; no matched secrets or internal data.')
