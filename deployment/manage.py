"""Source/installed bootstrap; runtime configuration is selected explicitly."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "app" if (HERE / "app/bobowatch").is_dir() else HERE.parent))
from bobowatch.manage import main  # noqa: E402

if __name__ == "__main__":
    main()
