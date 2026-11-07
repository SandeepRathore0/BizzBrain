"""Check router layering and scattered role checks."""

import os
import re

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "user-service"))
API_DIR = os.path.join(PROJECT_ROOT, "app", "api")

print("Checking API directory:", API_DIR)
for root, dirs, files in os.walk(API_DIR):
    for f in files:
        if f.endswith(".py"):
            path = os.path.join(root, f)
            with open(path, "r", encoding="utf-8") as file:
                lines = file.readlines()
            for idx, line in enumerate(lines, 1):
                if "repository" in line.lower() or "repositories" in line.lower():
                    print(f"[ROUTER_VIOLATION] Repo import in router {f}:{idx}: {line.strip()}")
                if "select(" in line or "execute(" in line:
                    print(f"[ROUTER_VIOLATION] Direct query in router {f}:{idx}: {line.strip()}")

# Check for scattered role checks (e.g., if role == 'OWNER' or role == 'ADMIN') outside app/core/permissions.py
for root, dirs, files in os.walk(os.path.join(PROJECT_ROOT, "app")):
    for f in files:
        if f.endswith(".py"):
            path = os.path.join(root, f)
            relpath = os.path.relpath(path, PROJECT_ROOT)
            if "permissions.py" in relpath:
                continue
            with open(path, "r", encoding="utf-8") as file:
                lines = file.readlines()
            for idx, line in enumerate(lines, 1):
                if re.search(r'role\s*==\s*["\'](OWNER|ADMIN|EMPLOYEE)["\']', line):
                    print(f"[SCATTERED_ROLE_CHECK] {relpath}:{idx}: {line.strip()}")
