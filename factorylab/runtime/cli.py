"""Command-line entry point. Filled in by the runtime workstream."""

import sys


def main(argv: list[str] | None = None) -> int:
    """Return 0 after printing a placeholder; the runtime workstream replaces this."""
    print("factorylab: runtime not yet built", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
