# Copyright (C) 2026 Sacrappy
"""The lift's file split and the text edits that read it (synthetic text only).

- the same input gives the same files, whatever the hash seed (a set or dict order reaching the text would show);
- an output size edit finds its place in any file, a part (code_NNN_M.c) as much as a whole chunk, and a place that is
  missing stops the lift with the function's name and the files searched;
- the written C is checked against the functions the lift made (a part another process removed is caught at once);
- the gen folder's lock: a second writer ends with a message naming the first, a dead holder's lock is taken over.

python -I -B tests/test_lift_split_stable.py
"""
import collections
import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import kit  # noqa: E402,F401  (a64-kit on the path)
import a64_lift  # noqa: E402
import chunks  # noqa: E402
import genlock  # noqa: E402
import oot_patches  # noqa: E402

a64_lift.a64emit.translate = lambda keys: ([], [])   # (no instructions to translate: the native translator is not built)
a64_lift.CHUNK_BYTES = 1500000   # (small chunks: the giant functions below make parts)


class Lift:
    """What chunks.write and Lifter.write use of a lifter: the functions, emitted as plain C"""
    write = a64_lift.Lifter.write
    _write_table = a64_lift.Lifter._write_table
    fname = a64_lift.Lifter.fname

    def __init__(self, addrs):
        self.funcs = {a: ({}, None) for a in addrs}
        self.hooks = {}
        self.stats, self.unhandled, self.ijump = collections.Counter(), collections.Counter(), collections.Counter()

    def emit_function(self, e, texts):
        n = 40 + (e * 7919) % 900 + (100000 if (e >> 4) % 700 == 350 else 0)   # (a few giant functions among the small ones)
        return ("void f_%08x(Ctx *__restrict A64_NOESC c) {\n" % e + "c->x[1] = 1;\n" * n + "}\n",
                {"f_%08x" % (e + 0x10), "f_%08x" % (e + 0x20), "f_%08x" % (e + 0x30)})


ADDRS = [0x80000000 + 16 * i for i in range(3000)]
HOT = ADDRS[::97]

DRIVER = """
import contextlib, hashlib, io, os, sys
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
import kit, a64_lift, chunks
sys.path.insert(0, sys.argv[3])
import test_lift_split_stable as T
work = sys.argv[4]
gen, img = os.path.join(work, "gen"), os.path.join(work, "image")
os.makedirs(img, exist_ok=True)
os.environ["A64_HOT_CHUNK"] = "1"
hot = os.path.join(work, "hot.txt")
open(hot, "w").write("".join("f_%08x\\n" % a for a in T.HOT))
os.environ["A64_HOT_FUNCS"] = hot
with contextlib.redirect_stdout(io.StringIO()):
    chunks.write(T.Lift(T.ADDRS), gen, img, rechunk=True, jobs=1)
h = hashlib.sha256()
for f in sorted(os.listdir(gen)):
    if f.endswith(".c"):
        h.update(f.encode()); h.update(open(os.path.join(gen, f), "rb").read())
print(h.hexdigest())
"""


def check(what, ok):
    if not ok:
        raise SystemExit("FAIL: " + what)
    print("ok:", what)


def lift_hash(seed, budget):
    with tempfile.TemporaryDirectory(prefix="hookshot-lift-split-") as d:
        env = dict(os.environ, PYTHONHASHSEED=str(seed))
        script = Path(d) / "drive.py"
        script.write_text(DRIVER)
        work = Path(d) / "w"
        work.mkdir()
        r = subprocess.run([sys.executable, "-B", str(script), str(ROOT / "nx2"), str(ROOT / "third_party" / "a64-kit" / "lift"),
                            str(Path(__file__).resolve().parent), str(work)], env=env, capture_output=True, text=True)
        if r.returncode:
            raise SystemExit("FAIL: the driver stopped: " + r.stderr[-800:])
        return r.stdout.strip().splitlines()[-1]


def test_same_split_any_hash_seed():
    hashes = {lift_hash(seed, None) for seed in (0, 1, 2, 12345)}
    check("the lift's files are the same under four hash seeds", len(hashes) == 1)


def test_check_whole():
    with tempfile.TemporaryDirectory(prefix="hookshot-lift-split-") as d:
        gen, img = os.path.join(d, "gen"), os.path.join(d, "image")
        os.makedirs(img)
        os.environ["A64_HOT_CHUNK"] = "1"
        with contextlib.redirect_stdout(io.StringIO()):
            chunks.write(Lift(ADDRS), gen, img, rechunk=True, jobs=1)
        parts = [f for f in os.listdir(gen) if chunks.PART.fullmatch(f)]
        check("the synthetic lift has split parts to lose", bool(parts))
        chunks.check_whole(gen, len(ADDRS))
        os.remove(os.path.join(gen, parts[0]))   # (what a second lift's cleanup does to this one's parts)
        try:
            chunks.check_whole(gen, len(ADDRS))
        except SystemExit as e:
            check("a lost part stops the lift, saying how many definitions are there",
                  "function definitions" in str(e) and str(len(ADDRS)) in str(e))
        else:
            check("a lost part stops the lift", False)


def test_edits_find_their_text_in_parts():
    old = [e[0] for e in oot_patches.OUT_EDITS]
    with tempfile.TemporaryDirectory(prefix="hookshot-lift-split-") as d:
        def lay(places):
            for f in os.listdir(d):
                os.remove(os.path.join(d, f))
            for name, idx in places.items():
                body = "".join("void f_%08x(Ctx *__restrict A64_NOESC c) {\n%s}\n" % (0x80100000 + 16 * i, old[i]) for i in idx)
                Path(d, name).write_text('#include "a64.h"\n' + body, newline="")
        # (edits in a whole chunk, in a second part and in a third: wherever the split puts them)
        lay({"code_001.c": [0, 1], "code_002_1.c": [2, 3, 4], "code_003_2.c": [5, 6, 7], "code_004.c": []})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            oot_patches.apply_output_size(d, required=True)
        check("every output size edit is made once, in whole chunks and parts alike",
              all(n in out.getvalue() for n in ("code_001.c", "code_002_1.c", "code_003_2.c")))
        text = "".join(Path(d, f).read_text() for f in os.listdir(d))
        check("each new text stands once", all(text.count(e[1]) == 1 for e in oot_patches.OUT_EDITS))
        with contextlib.redirect_stdout(io.StringIO()):
            oot_patches.apply_output_size(d, required=True)   # (a second run finds them made, once each)
        lay({"code_001.c": [0, 1], "code_002.c": [2, 3, 4], "code_003.c": [5, 6]})   # (the part with edit 7 is gone)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                oot_patches.apply_output_size(d, required=True)
        except SystemExit as e:
            msg = str(e)
            check("a missing place names its function and the files searched",
                  "f_80b72340" in msg and "searched 3 code_*.c files" in msg)
        else:
            check("a missing place stops the lift", False)


def test_gen_lock():
    with tempfile.TemporaryDirectory(prefix="hookshot-lift-split-") as d:
        gen = os.path.join(d, "sa", "gen")
        code = ("import sys; sys.path.insert(0, %r); import genlock\n"
                "with genlock.hold(%r, 'second'): pass\n" % (str(ROOT / "nx2"), gen))
        with genlock.hold(gen, "first"):
            check("the lock file is there", os.path.exists(genlock.lock_path(gen)))
            r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
            out = r.stderr + r.stdout
            check("a second live writer is refused, told to wait, not to stop anything",
                  r.returncode != 0 and "wait for it to end" in out and "stop" not in out.lower().replace("stopped", ""))
        r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
        check("the lock is given back", r.returncode == 0)
        # (a file a dead process left, with a pid in it that is alive: mine, here, and that of a stranger)
        for pid in (os.getpid(), os.getppid(), 4):
            Path(genlock.lock_path(gen)).write_text("%d lift.py since 00:00:00" % pid)
            r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
            check("a stale lock file naming live pid %d is taken over silently" % pid, r.returncode == 0 and not r.stderr)


def test_scripts_start_isolated():
    """The build runs these as `python -I nx2/<script> <gen>`: -I leaves the script's folder off the path, so each must put it
    back before it imports its neighbours (hl_gates.py did not: "No module named 'genlock'" stopped every build)"""
    with tempfile.TemporaryDirectory(prefix="hookshot-lift-split-") as d:
        gen = os.path.join(d, "sa", "gen")
        os.makedirs(gen)
        for script in ("hl_gates.py", "split_hot.py", "oot_patches.py"):
            r = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "nx2" / script), gen], capture_output=True, text=True,
                               cwd=str(ROOT))
            check("%s starts under python -I" % script, "ModuleNotFoundError" not in r.stderr and "ImportError" not in r.stderr)


def main():
    test_scripts_start_isolated()
    test_gen_lock()
    test_edits_find_their_text_in_parts()
    test_check_whole()
    test_same_split_any_hash_seed()


if __name__ == "__main__":
    main()
