"""Упаковка CSV-сабмита с корневыми Python-модулями по регламенту."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def build_submission(
    output: Path, consultant: Path | None = None, modules: list[Path] | None = None
) -> bool:
    backend = Path(__file__).resolve().parent
    files = {
        "solution.py": backend / "solution.py",
        "consultant.py": backend / "app/consultant.py",
        "rag_search.py": backend.parent / "rag/rag_search.py",
        "dialog_memory.py": backend / "dialog_memory.py",
        "requirements.txt": backend / "requirements-submission.txt",
    }
    if consultant is not None:
        if consultant.suffix != ".py":
            raise ValueError("Consultant implementation must be a .py file")
        files["consultant_impl.py"] = consultant
    else:
        files["consultant_impl.py"] = backend / "consultant_impl.py"
    for module in modules or []:
        if module.suffix != ".py" or module.name in files:
            raise ValueError("Additional modules must have unique .py filenames")
        files[module.name] = module
    for source in files.values():
        if not source.is_file():
            raise FileNotFoundError(f"Missing module: {source.name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, source in files.items():
            archive.write(source, name)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).parent / "dist/submission.zip"
    )
    parser.add_argument(
        "--consultant", type=Path, help="Implementation exporting create_consultant()"
    )
    parser.add_argument(
        "--module", type=Path, action="append", help="Additional root Python module"
    )
    args = parser.parse_args()
    build_submission(args.output, args.consultant, args.module)
    print(f"Created {args.output}")


if __name__ == "__main__":
    main()
