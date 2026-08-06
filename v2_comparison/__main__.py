"""python -m v2_comparison dispatches to record_capture help."""

from v2_comparison.record_capture import build_parser


def main() -> None:
    build_parser().print_help()


if __name__ == "__main__":
    main()
