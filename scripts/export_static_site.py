import argparse
from pathlib import Path

from app.static_export import build_site


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the current database as a static HTML site (no server needed to view it)."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("site"),
        help="Output directory (default: ./site). Existing contents are replaced.",
    )
    args = parser.parse_args()

    build_site(args.output)
    print(f"Static site written to {args.output.resolve()}")


if __name__ == "__main__":
    main()
