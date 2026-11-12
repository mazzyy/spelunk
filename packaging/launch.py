"""PyInstaller entry point for Spelunk.app."""
import sys

from spelunk.desktop import main

if __name__ == "__main__":
    sys.exit(main())
