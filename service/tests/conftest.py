import os
import sys
from pathlib import Path

# Unit tests never touch the network or the database, so placeholders are
# enough to let Settings construct. (Integration tests set real values.)
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
