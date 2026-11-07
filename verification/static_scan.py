"""Static analysis script for security review."""

import os
import re
import ast

findings = []

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "user-service"))

def check_file(filepath):
    relpath = os.path.relpath(filepath, PROJECT_ROOT)
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
        lines = content.splitlines()

    # 1. Search for print()
    for idx, line in enumerate(lines, 1):
        if re.search(r"\bprint\s*\(", line):
            findings.append({
                "type": "PRINT_STATEMENT",
                "file": relpath,
                "line": idx,
                "content": line.strip()
            })

    # 2. Search for hardcoded secrets or private keys
    if "BEGIN RSA PRIVATE KEY" in content or "BEGIN PRIVATE KEY" in content:
        if not filepath.endswith(".pyc"):
            findings.append({
                "type": "PRIVATE_KEY_IN_FILE",
                "file": relpath,
                "line": 1,
                "content": "Potential hardcoded private key header"
            })

    for idx, line in enumerate(lines, 1):
        if re.search(r'(?i)(password|secret|key)\s*=\s*["\'][^"\']{4,}["\']', line):
            if "test" not in relpath.lower() and ".env" not in relpath:
                findings.append({
                    "type": "HARDCODED_SECRET_OR_DEFAULT",
                    "file": relpath,
                    "line": idx,
                    "content": line.strip()
                })

    # 3. Search for float() or Float in models/schemas
    for idx, line in enumerate(lines, 1):
        if re.search(r'\bfloat\b|\bFloat\b', line):
            if "sqlalchemy" in line or "pydantic" in line or "models" in relpath:
                findings.append({
                    "type": "FLOAT_USAGE",
                    "file": relpath,
                    "line": idx,
                    "content": line.strip()
                })

    # 4. Search for raw SQL execution with format or f-strings
    for idx, line in enumerate(lines, 1):
        if re.search(r'(execute|text)\s*\(\s*f["\']', line) or re.search(r'(execute|text)\s*\(.*%\s*', line):
            findings.append({
                "type": "RAW_SQL_STRING_FORMATTING",
                "file": relpath,
                "line": idx,
                "content": line.strip()
            })

    # 5. Search for logging of request body
    for idx, line in enumerate(lines, 1):
        if re.search(r'logger\.\w+\(.*(body|payload|password|token)', line, re.IGNORECASE):
            findings.append({
                "type": "POTENTIAL_SENSITIVE_LOGGING",
                "file": relpath,
                "line": idx,
                "content": line.strip()
            })

for root, dirs, files in os.walk(PROJECT_ROOT):
    # skip .git, __pycache__, .pytest_cache
    if any(p in root for p in [".git", "__pycache__", ".pytest_cache"]):
        continue
    for f in files:
        if f.endswith((".py", ".yml", ".yaml", ".ini", "Dockerfile")):
            check_file(os.path.join(root, f))

print(f"Total findings: {len(findings)}")
for f in findings:
    print(f"[{f['type']}] {f['file']}:{f['line']} -> {f['content']}")
