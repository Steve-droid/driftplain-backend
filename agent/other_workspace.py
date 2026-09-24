"""Trusted exact-commit exporter and patch collector, outside the model/validation namespaces."""

import hashlib
import os
import re
import shutil
import stat
import tempfile
import time
from pathlib import Path

from agent.bounded_process import run_bounded
from agent.errors import AgentConfigError, CeilingExceeded
from app.task_contracts import Artifact, ChangedFile, Patch, relative_path


def git_env():
    return {
        "PATH": os.defpath + ":/opt/homebrew/bin",
        "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_ATTR_NOSYSTEM": "1",
    }


def git(args, limit=4194304, seconds=30):
    r = run_bounded(["git", *args], env=git_env(), max_bytes=limit, seconds=seconds)
    if r.reason:
        raise CeilingExceeded(
            "wall-clock" if "wall-clock" in r.reason else "input",
            "Git operation exceeded its bound",
        )
    if r.code:
        raise AgentConfigError("Exact commit export or patch capture failed")
    return r.output


def inspect_tree(path, max_bytes, max_file_bytes):
    files = {}
    total = 0

    def unreadable(_):
        raise AgentConfigError("Unreadable workspace directory")

    for directory, dirs, names in os.walk(path, followlinks=False, onerror=unreadable):
        for name in dirs + names:
            p = Path(directory) / name
            rel = p.relative_to(path).as_posix()
            try:
                relative_path(rel)
            except ValueError:
                raise AgentConfigError("Unsupported workspace path") from None
            info = p.lstat()
            if (
                name.lower() == ".git"
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
            ):
                raise AgentConfigError(
                    "Workspace contains forbidden metadata, link or special file"
                )
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
                if info.st_size > max_file_bytes or total > max_bytes:
                    raise CeilingExceeded("output", "Workspace byte ceiling exceeded")
                data = p.read_bytes()
                files[rel] = (
                    hashlib.sha256(data).hexdigest(),
                    bool(info.st_mode & 0o111),
                )
                if len(files) > 1000:
                    raise CeilingExceeded("output", "Workspace file ceiling exceeded")
    return files


class DisposableWorkspace:
    def __init__(self, source, commit, cfg, max_seconds=600):
        if not isinstance(commit, str) or not re.fullmatch(
            r"(?:[a-f0-9]{40}|[a-f0-9]{64})", commit
        ):
            raise AgentConfigError("An exact base commit is required")
        self.expires = time.monotonic() + max_seconds
        self.source = Path(source).resolve()
        self.commit = commit
        self.cfg = cfg

    def git(self, args, limit=4194304):
        remaining = self.expires - time.monotonic()
        if remaining <= 0:
            raise CeilingExceeded("wall-clock", "Workspace deadline expired")
        return git(args, limit, seconds=min(30, remaining))

    def __enter__(self):
        self.root = Path(tempfile.mkdtemp(prefix="driftplain-other-")).resolve()
        self.preserve = False
        self.path = self.root / "workspace"
        self.path.mkdir()
        self.meta = self.root / "index.git"
        try:
            # ls-tree/cat-file avoid archive export-ignore/substitution and checkout filters/hooks.
            actual = (
                self.git(
                    ["-C", str(self.source), "rev-parse", self.commit + "^{commit}"]
                )
                .decode()
                .strip()
            )
            if actual != self.commit:
                raise AgentConfigError("Base identity mismatch")
            rows = self.git(
                ["-C", str(self.source), "ls-tree", "-rz", self.commit]
            ).split(b"\0")
            total = 0
            count = 0
            for row in rows:
                if not row:
                    continue
                meta, name = row.split(b"\t", 1)
                mode, typ, oid = meta.decode().split()
                name = name.decode("utf-8")
                relative_path(name)
                if (
                    mode not in ("100644", "100755")
                    or typ != "blob"
                    or any(p.lower() == ".git" for p in name.split("/"))
                ):
                    raise AgentConfigError(
                        "Exact base contains a link, submodule or forbidden path"
                    )
                count += 1
                if count > 1000:
                    raise CeilingExceeded("input", "Base has too many files")
                data = self.git(
                    ["-C", str(self.source), "cat-file", "blob", oid],
                    self.cfg.inputs.max_file_bytes,
                )
                total += len(data)
                if total > self.cfg.inputs.max_bytes:
                    raise CeilingExceeded("input", "Base checkout exceeds byte ceiling")
                target = self.path / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(0o755 if mode == "100755" else 0o644)
            self.original = inspect_tree(
                self.path, self.cfg.inputs.max_bytes, self.cfg.inputs.max_file_bytes
            )
            self.git(["init", "--bare", "-q", str(self.meta)])
            (self.meta / "info" / "attributes").write_text(
                "* -filter -text -ident -working-tree-encoding\n"
            )
            self.base_tree = self.tree()
            return self
        except BaseException:
            shutil.rmtree(self.root)
            raise

    def tree(self):
        args = [
            "--git-dir",
            str(self.meta),
            "--work-tree",
            str(self.path),
            "-c",
            "core.bare=false",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.fileMode=true",
        ]
        self.git([*args, "add", "--all", "--force", "--", "."])
        return self.git([*args, "write-tree"]).decode().strip()

    def capture(self, out):
        current = inspect_tree(
            self.path,
            self.cfg.resources.max_output_bytes,
            self.cfg.resources.max_output_bytes,
        )
        changed = []
        for name in sorted(self.original.keys() | current.keys()):
            if self.original.get(name) == current.get(name):
                continue
            if not any(
                name == p or name.startswith(p + "/") for p in self.cfg.write_paths
            ):
                raise AgentConfigError("Changes outside permitted write paths")
            changed.append(
                ChangedFile(
                    path=name,
                    operation="added"
                    if name not in self.original
                    else "deleted"
                    if name not in current
                    else "modified",
                )
            )
        if not changed:
            return None, None
        if len(changed) > 100:
            raise CeilingExceeded("output", "Too many changed files")
        tree = self.tree()
        raw = self.git(
            [
                "--git-dir",
                str(self.meta),
                "diff-tree",
                "--no-commit-id",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                "--binary",
                "-p",
                self.base_tree,
                tree,
            ],
            self.cfg.resources.max_output_bytes,
        )
        digest = hashlib.sha256(raw).hexdigest()
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        target = out / "changes.patch"
        target.write_bytes(raw)
        artifact = Artifact(
            id="changes",
            kind="patch",
            path=target.name,
            sha256=digest,
            size_bytes=len(raw),
        )
        return Patch(sha256=digest, artifact_id="changes", files=changed), artifact

    def __exit__(self, *args):
        if not self.preserve:
            shutil.rmtree(self.root)
