"""PyInstaller entry script for the desktop engine. Kept separate from the
package so the frozen executable starts the same way as
`python -m cognitrix.desktop`."""
import sys

from cognitrix.desktop import main

if __name__ == '__main__':
    sys.exit(main())
