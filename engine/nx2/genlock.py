# Copyright (C) 2026 Sacrappy
"""One writer at a time in the lifted C's folder (work/sa/gen).

Every step that rewrites code_*.c in place (the lift's write, its text edits and the chunk split; split_hot.py; hl_gates.py;
oot_patches.py run by hand) holds <gen>.lock while it works. A second one, a lift started by hand beside a build or one a
stopped wrapper left running, ends with a message naming the holder, instead of both writing: each deletes the other's split
parts and patches the other's half-written files, and the edits then fail at random ("the output size places were found
[0, 1, 1, ...] times": the second part of a chunk was gone). The lock is the operating system's, on the open file (msvcrt.locking / fcntl.flock), held by the handle for the life of the
process: a crash or a power cut drops it, and a file left behind, with any pid in it, is taken over silently. The file's text
(pid, what, since) only feeds the refusal's message.
"""
import contextlib
import os
import sys
import time

_held = {}   # (path -> the open handle: closing it, or the process ending, gives the lock back)
LOCK_AT = 4096   # (the byte locked, past the text: Windows refuses other processes a read of a locked byte)


def lock_path(gen):
    return os.path.normpath(gen) + ".lock"


def read_holder(path):
    try:
        fd = os.open(path, os.O_RDONLY)   # (no buffer: a buffered read asks for a block that spans the locked byte, and
        try:                              #  Windows then refuses the whole read, so the refusal named no holder)
            text = os.read(fd, 200).decode("utf-8", "replace")
        finally:
            os.close(fd)
        pid, _, rest = text.strip().partition(" ")
        return int(pid), rest
    except (OSError, ValueError):
        return 0, ""


def _lock(fd):
    """True when the OS lock is ours, False when another process holds it"""
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, LOCK_AT, 0)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _take(gen, what):
    path = lock_path(gen)
    if path in _held:
        return path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    if not _lock(fd):
        pid, rest = read_holder(path)
        os.close(fd)
        raise SystemExit("genlock: another build or lift is writing %s (%s): wait for it to end, then run this again. Two writers "
                         "on the lifted C make the edits miss their text at random" % (os.path.dirname(path), rest or "?"))
    os.ftruncate(fd, 0)   # (the lock is ours: whatever a dead process left in the file goes)
    os.lseek(fd, 0, 0)
    os.write(fd, ("%d %s since %s" % (os.getpid(), what, time.strftime("%H:%M:%S"))).encode("utf-8"))
    _held[path] = fd
    return path


def take(gen, what):
    """Takes <gen>.lock for the rest of the process; SystemExit when another process holds it"""
    return _take(gen, what)


def _give(path):
    fd = _held.pop(path, None)
    if fd is not None:
        os.close(fd)   # (the OS lock goes with the handle; the file stays, empty of meaning)


@contextlib.contextmanager
def hold(gen, what):
    """Holds <gen>.lock for the with block; SystemExit when another process holds it"""
    path = _take(gen, what)
    try:
        yield
    finally:
        _give(path)
