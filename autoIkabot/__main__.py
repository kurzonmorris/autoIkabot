"""Allow running the package directly: python -m autoIkabot"""

import sys
import pathlib


def main():
    """Entry point for python -m autoIkabot and the console script."""
    # Ensure the repo root is on sys.path so main can be found
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from main import main as run_main
    run_main()


if __name__ == "__main__":
    main()
