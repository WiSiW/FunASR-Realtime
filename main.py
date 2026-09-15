"""Backward-compatible CLI entry point.

Run ``python main.py --help`` for microphone recognition without the web UI.
"""

from backend.app.cli import main


if __name__ == "__main__":
    main()
