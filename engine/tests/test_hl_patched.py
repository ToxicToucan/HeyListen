# Copyright (C) 2026 Sacrappy

"""The native engine and exeFS (code) mods, game-free: host/hl_patched.c on a made-up patch list and made-up answers (tests/hl_patched_test.c);
and the builder's side, on a made-up image: which functions a patch touches (nx2/exefs_mods.py patched_functions), the list's text,
and that nx2/hl_gates.py leaves an entry gate and the step hooks out of a patched function (and puts them in again when the mod goes).

python -I -B tests/test_hl_patched.py [--cc gcc]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import exefs_mods  # noqa: E402
import hl_gates  # noqa: E402


def check(what, ok):
    if not ok:
        raise SystemExit("FAIL: " + what)


def builder_side(temp):
    image = Path(temp) / "image"
    image.mkdir()
    (image / "image.json").write_text(json.dumps({"modules": [{"name": "main", "base": 0x80020000}, {"name": "sdk", "base": 0x88000000}],
                                                  "hooks": [{"addr": 0x80021000, "sym": "nn_os_thing", "c": "imp_x"}]}))
    # (function table: address -> [instruction count, module])
    funcs = {"80020000": [16, "main"], "80020100": [8, "main"], "80021000": [4, "main"], "88000000": [4, "sdk"]}
    (image / "functions.json").write_text(json.dumps(funcs))
    said = []
    text, patched = exefs_mods.patched_functions(
        {"main": [(0x20, 0x24, "A"), (0x104, 0x108, "B"), (0x1000, 0x1004, "C"), (0x90, 0x94, "gap")], "sdk": [(0x8, 0xc, "S")]},
        str(image), say=said.append)
    check("a patch in the first function is found (%s)" % patched, patched.get("80020000") == "A")
    check("a patch inside the second function", patched.get("80020100") == "B")
    check("a patch between functions touches none", all(k != "80020090" for k in patched) and len(patched) == 4)
    check("the sdk module has its own base", patched.get("88000000") == "S")
    check("a range is in guest addresses after relocation", "range 0x80020020 0x80020024 main A" in text)
    check("the function's extent is the lift's count of instructions", "function 0x80020000 0x80020040 A" in text)
    check("a function the OS layer answers is said", any("nn_os_thing" in s for s in said))
    return patched


def gates_side(temp, patched):
    host = Path(temp) / "host"
    host.mkdir()
    (host / "hl_x.c").write_text('HL_GATE(80020000, "%s") { return 1; }\nHL_GATE(80020100, "%s") { return 1; }\n' % ("0" * 16, "0" * 16))
    gen = Path(temp) / "gen"
    gen.mkdir()
    code = ('#include "a64.h"\nvoid f_80020000(Ctx *__restrict A64_NOESC c) {\n    c->x[0] = 1;\nreturn;\n}\n'
            'void f_80020100(Ctx *__restrict A64_NOESC c) {\n    c->x[0] = 2;\nreturn;\n}\n')
    h1 = hl_gates.text_hash(code, "80020000")
    h2 = hl_gates.text_hash(code, "80020100")
    (host / "hl_x.c").write_text('HL_GATE(80020000, "%s") { return 1; }\nHL_GATE(80020100, "%s") { return 1; }\n' % (h1, h2))
    (gen / "code_0.c").write_bytes(code.encode())
    check("both gates go in without a patch", hl_gates.apply(str(gen), str(host), quiet=True) == 2)
    check("the gate text is in the file", (gen / "code_0.c").read_bytes().decode().count("/* hl gate ") >= 2)
    n = hl_gates.apply(str(gen), str(host), quiet=True, skip={"80020000": "A"})
    text = (gen / "code_0.c").read_bytes().decode()
    check("the patched function loses its gate, the other keeps it", n == 1 and "hl gate 80020000" not in text and "hl gate 80020100" in text)
    n = hl_gates.apply(str(gen), str(host), quiet=True)
    check("the mod gone: the gate comes back", n == 2 and "hl gate 80020000" in (gen / "code_0.c").read_bytes().decode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="hookshot-hlpatched-") as temp:
        patched = builder_side(temp)
        gates_side(temp, patched)
        exe = Path(temp) / "hl_patched_test.exe"
        subprocess.run([args.cc, "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-I" + str(ROOT / "host"), str(ROOT / "tests/hl_patched_test.c"),
                        str(ROOT / "host/hl_patched.c"), "-o", str(exe)], check=True)
        code = subprocess.run([str(exe)], timeout=60).returncode
    if code:
        return code
    print("ok: the builder lists the functions a patch touches, hl_gates leaves their gates out, the host leaves their answers to the lifted code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
