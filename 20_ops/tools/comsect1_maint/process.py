# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""Helpers every maintainer tool shares (Python standard library only).

Repository discovery, run ids, child processes without a window and with a
timeout that kills the whole process tree, link and reparse refusal, the
exact-run quarantine through `99_trash`, digests and LF UTF-8 writes.
"""

import datetime
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys

PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))
TOOLS_ROOT = os.path.dirname(PACKAGE_ROOT)
REPO_ROOT = os.path.abspath(os.path.join(TOOLS_ROOT, "..", ".."))
# The one in-repository home of declared dependency checkouts (Git submodules), repository-relative
# segments: a consumed checkout inside the repository lies below it, never elsewhere.
DEPS_FOLDER = ("10_pkg", "deps")

RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
REPARSE_FLAG = 0x400
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


class ProcessError(Exception):
    """A child process could not start (`kind` "start") or exceeded its timeout (`kind` "timeout")."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def new_run_id(prefix="run", precise=True):
    """`<prefix>-<UTC stamp>-<pid>`; `precise` adds microseconds to the stamp."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ" if precise else "%Y%m%dT%H%M%SZ")
    return "%s-%s-%d" % (prefix, stamp, os.getpid())


def window_flags():
    """`creationflags` of a child that never opens a window (0 off Windows)."""
    return CREATE_NO_WINDOW if os.name == "nt" else 0


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def write_json(path, value, sort_keys=False):
    write_text(path, json.dumps(value, indent=2, sort_keys=sort_keys) + "\n")


def load_sibling(directory, file_name, module_name):
    """Load `<directory>/<file_name>` as module `module_name` (registered in sys.modules)."""
    spec = importlib.util.spec_from_file_location(module_name, os.path.join(directory, file_name))
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- processes

def kill_tree(process):
    """Kill a child and every process it started."""
    if os.name == "nt":
        taskkill = os.path.join(os.environ.get("SystemRoot", "C:/Windows"), "System32", "taskkill.exe")
        try:
            subprocess.run([taskkill, "/F", "/T", "/PID", str(process.pid)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
                           creationflags=CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def run_process(name, command, timeout, cwd, log_path=None, env=None):
    """Run one process without a window, stdin closed; return (exit code, stdout text, stderr text).

    Text is decoded as UTF-8. On timeout the whole tree is killed and ProcessError("timeout") raised;
    a process that cannot start raises ProcessError("start"). `log_path` receives the exit and output."""
    options = {}
    if os.name == "nt":
        options["creationflags"] = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    try:
        process = subprocess.Popen([str(part) for part in command], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(cwd), env=env, **options)
    except OSError as error:
        raise ProcessError("start", "%s could not start: %s" % (name, error))
    try:
        out_bytes, err_bytes = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        if log_path is not None:
            write_text(log_path, "timeout after %d s; process tree killed\n" % timeout)
        raise ProcessError("timeout", "%s exceeded %d s; process tree killed" % (name, timeout))
    out = out_bytes.decode("utf-8", errors="replace")
    err = err_bytes.decode("utf-8", errors="replace")
    if log_path is not None:
        write_text(log_path, "exit=%d\n--- stdout\n%s\n--- stderr\n%s\n" % (process.returncode, out, err))
    return process.returncode, out, err


def default_jobs():
    """The worker count of a parallel build step: the logical processors of the host."""
    return max(1, os.cpu_count() or 1)


def ordered_map(function, items, jobs):
    """`[function(item) for item in items]` on at most `jobs` worker threads (for child processes).

    Results keep the order of `items` whatever the completion order. After the first failure no further
    item starts; the running ones finish, and the failure of the earliest failed item is raised."""
    items = list(items)
    if jobs is None:
        jobs = default_jobs()
    if jobs < 1:
        raise ValueError("jobs must be at least 1")
    if jobs == 1 or len(items) <= 1:
        return [function(item) for item in items]
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(jobs, len(items))) as pool:
        futures = [pool.submit(function, item) for item in items]
        failure = None
        results = []
        for future in futures:
            if failure is not None:
                future.cancel()
            try:
                results.append(future.result())
            except concurrent.futures.CancelledError:
                results.append(None)
            except BaseException as error:  # noqa: B902 - re-raised after the pool drains
                if failure is None:
                    failure = error
                    for pending in futures:
                        pending.cancel()
                results.append(None)
    if failure is not None:
        raise failure
    return results


# ---------------------------------------------------------------- links and quarantine

def is_link(path):
    """True for a symbolic link, a junction or any reparse point; False for an absent path."""
    if not os.path.lexists(path):
        return False
    info = os.lstat(path)
    return (stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & REPARSE_FLAG)
            or (hasattr(os.path, "isjunction") and os.path.isjunction(path)))


def checked_path(path, root=REPO_ROOT):
    """The absolute `path` after refusing a location outside `root` and any linked existing ancestor."""
    root = os.path.abspath(root)
    candidate = os.path.abspath(path)
    try:
        common = os.path.commonpath([root, candidate])
    except ValueError as error:
        raise RuntimeError("path is outside the repository: %s" % candidate) from error
    if os.path.normcase(common) != os.path.normcase(root):
        raise RuntimeError("path is outside the repository: %s" % candidate)
    cursor = candidate
    while True:
        if is_link(cursor):
            raise RuntimeError("refusing linked or reparse-point path: %s" % cursor)
        parent = os.path.dirname(cursor)
        if parent == cursor:
            break
        cursor = parent
    return candidate


def check_tree(path):
    """A content inventory of `path` [(relative, kind, sha256)]; refuses links and unstable entries."""
    inventory = []

    def fail(error):
        raise error

    for directory, names, files in os.walk(path, followlinks=False, onerror=fail):
        for name in sorted(names + files):
            child = os.path.join(directory, name)
            if is_link(child):
                raise RuntimeError("refusing linked or reparse-point child: %s" % child)
            info = os.lstat(child)
            relative = os.path.relpath(child, path)
            if stat.S_ISDIR(info.st_mode):
                inventory.append((relative, "directory", None))
            elif stat.S_ISREG(info.st_mode):
                digest = hashlib.sha256()
                with open(child, "rb") as handle:
                    opened = os.fstat(handle.fileno())
                    if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                        raise RuntimeError("entry changed before reading: %s" % child)
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
                    completed = os.fstat(handle.fileno())
                current = os.lstat(child)
                if (is_link(child) or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                        != (completed.st_dev, completed.st_ino, completed.st_size, completed.st_mtime_ns)
                        or (completed.st_dev, completed.st_ino) != (current.st_dev, current.st_ino)):
                    raise RuntimeError("entry changed during reading: %s" % child)
                inventory.append((relative, "file", digest.hexdigest()))
            else:
                raise RuntimeError("refusing non-regular entry: %s" % child)
    return sorted(inventory)


def quarantine(run_dir, expected, child_name, root=REPO_ROOT, announce=None, prune_levels=2):
    """Move the exact run directory `run_dir` through `<root>/99_trash/<child_name>` and delete only that child.

    `expected` is the one path `run_dir` must equal. The run and the quarantine child are checked for
    links before and after, the content inventory and the directory identity must survive the move,
    and only then is the child deleted. Empty parents of the run (up to `prune_levels`) are removed.
    `announce` receives a one-line preview before the move."""
    run_dir = checked_path(run_dir, root)
    if os.path.normcase(run_dir) != os.path.normcase(os.path.abspath(expected)):
        raise RuntimeError("cleanup refused: not the exact run directory")
    trash_root = checked_path(os.path.join(root, "99_trash"), root)
    child = os.path.join(trash_root, child_name)
    checked_path(child, root)
    if not os.path.isdir(run_dir) or os.path.lexists(child):
        raise RuntimeError("cleanup refused: missing run or existing quarantine child")
    inventory = check_tree(run_dir)
    os.makedirs(trash_root, exist_ok=True)
    checked_path(trash_root, root)
    before = os.stat(run_dir)
    if announce is not None:
        announce("cleanup: preview source=%s entries=%d quarantine=%s" % (run_dir, len(inventory), child))
    os.rename(run_dir, child)
    if os.path.lexists(run_dir) or not os.path.isdir(child):
        raise RuntimeError("quarantine failed verification")
    checked_path(child, root)
    after = os.stat(child)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise RuntimeError("quarantine identity differs from the run")
    if check_tree(child) != inventory:
        raise RuntimeError("quarantine content differs from the run")
    shutil.rmtree(child)
    level = run_dir
    for _ in range(prune_levels):
        level = os.path.dirname(level)
        try:
            checked_path(level, root)
            os.rmdir(level)
        except (OSError, RuntimeError):
            break
