"""PyInstaller entry point — thin wrapper so relative imports stay simple."""

from yt2jellyfin.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
