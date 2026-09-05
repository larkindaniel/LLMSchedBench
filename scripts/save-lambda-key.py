"""Run interactively to save a Lambda API key without putting it in shell history."""

import getpass
import os
from pathlib import Path

path = Path.home() / ".config" / "llmschedbench" / "lambda-api-key"
path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
key = getpass.getpass("Paste temporary Lambda API key (hidden): ").strip()
if not key or any(c.isspace() for c in key):
    raise SystemExit("Invalid key; nothing written")
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as handle:
    handle.write(key + "\n")
print(f"Saved privately to {path}. Revoke this key after the rental.")
