"""Path validation — keep every filesystem operation inside the workspace."""

from pathlib import Path


class PathError(ValueError):
    """Raised when a request points somewhere it has no business pointing."""


def safe_workspace(workspace_path: str) -> Path:
    """Resolve a workspace path, rejecting anything that isn't a real directory."""
    if not workspace_path or not workspace_path.strip():
        raise PathError("workspace_path is required")

    try:
        root = Path(workspace_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as e:
        raise PathError(f"workspace_path does not exist: {workspace_path}") from e

    if not root.is_dir():
        raise PathError(f"workspace_path is not a directory: {workspace_path}")
    return root


def within(root: Path, candidate: str) -> Path:
    """Resolve `candidate` and confirm it sits inside `root`.

    Blocks `../` traversal and absolute paths pointing elsewhere on disk.
    """
    try:
        target = Path(candidate).expanduser().resolve()
    except (OSError, RuntimeError) as e:
        raise PathError(f"invalid path: {candidate}") from e

    if target != root and root not in target.parents:
        raise PathError(f"path escapes the workspace: {candidate}")
    return target


if __name__ == "__main__":
    import tempfile

    tmp = Path(tempfile.mkdtemp()).resolve()
    (tmp / "ok.py").write_text("x = 1")
    print("workspace:", safe_workspace(str(tmp)))
    print("inside   :", within(tmp, str(tmp / "ok.py")))
    for bad in [str(tmp / ".." / "etc" / "passwd"), "C:/Windows/System32/config"]:
        try:
            within(tmp, bad)
            print("LEAK     :", bad)
        except PathError as e:
            print("blocked  :", e)
