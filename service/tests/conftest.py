import os
import sys
from pathlib import Path

# Tests exercise pure logic (scheduling, routing, parsing, validation) and never
# touch the network or the database, so a placeholder DSN is enough to let
# Settings construct.
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/postgres")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
