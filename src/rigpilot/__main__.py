"""Enable ``python -m rigpilot`` on Windows and other supported platforms."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
