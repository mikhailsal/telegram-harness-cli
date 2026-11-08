#!/usr/bin/env python3
"""Run ShellCheck on tracked shell scripts in an immutable Git revision."""
import pathlib
import subprocess
import sys
import tempfile


def check(revision: str) -> None:
    listing = subprocess.check_output(["git", "ls-tree", "-rz", "--name-only", revision])
    paths = [path for path in listing.split(b"\0") if path.endswith(b".sh")]
    if not paths:
        return
    with tempfile.TemporaryDirectory(prefix="thc-shellcheck-") as directory:
        root = pathlib.Path(directory)
        for raw_path in paths:
            path = raw_path.decode("utf-8")
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.check_output(["git", "show", f"{revision}:{path}"]))
        subprocess.run(["shellcheck", "--", *[p.decode("utf-8") for p in paths]], cwd=root, check=True)


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "--pre-push":
        for update in sys.stdin:
            fields = update.split()
            if len(fields) == 4 and fields[1].strip("0"):
                check(fields[1])
    else:
        check(sys.argv[1] if len(sys.argv) == 2 else "HEAD")


if __name__ == "__main__":
    main()
