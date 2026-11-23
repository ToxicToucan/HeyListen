# Copyright (C) 2026 Sacrappy

"""The program pick's validated-handle memo (HL_PROG_MEMO, gpu/vk/prog_memo.h): the header against a made-up world, and the wiring in
vk_shader.cpp (what the memo answers from, when it stays off, that the check mode works the reference pick out and uses it).
python -I -B tests/test_prog_memo.py [--cxx clang++]"""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_wiring():
    sh = (ROOT / "gpu/vk/vk_shader.cpp").read_text(encoding="utf-8")
    start = sh.index("const Program* program_for(const NvnState& s, ProgramKind kind")
    body = sh[start:sh.index("const Program* program_pick(const NvnState& s, ProgramKind kind", start)]
    for needle in (
        "const int pm = prog_memo_mode();",
        "if (pm) answer_handles_memo(s, *pk.p, now);",
        "const bool kept = pk.valid.has(now);",
        "bool ok = kept || still_valid_from(*pk.p, now);",
        # (the check mode: the reference pick is worked out on every memo answer, compared, and is the one used)
        "const bool reference = still_valid(s, *pk.p);",
        "prog_memo_compare(ok, reference, kept ? 0 : 1);",
        "ok = reference;",
        "if (!kept) pk.valid.add(now);",
        # (a new variant: nothing of the old one's vectors stands)
        "if (pk.p != picked) pk.valid.clear();",
        "pk.valid.clear();",
        # (the unswitched path stays as it was)
        "} else if (still_valid(s, *pk.p)) {",
    ):
        if needle not in body:
            raise SystemExit("vk_shader.cpp's program_for no longer has: " + needle)
    mode = sh[sh.index("int prog_memo_mode() {"):sh.index("void prog_memo_compare(")]
    for needle in ("HL_PROG_MEMO_CHECK", "HL_PROG_MEMO", "RX_VK_HANDLES_LIVE", "RX_VK_REGISTER_AT_ONCE"):
        if needle not in mode:
            raise SystemExit("prog_memo_mode no longer reads: " + needle)
    # (the one-pass validity is still_valid's compare, and answers stay a function of the slot and its version)
    one = sh[sh.index("bool still_valid_from("):]
    one = one[:one.index("\n}\n")]
    ref = sh[sh.index("bool still_valid(const NvnState& s, const Program& p) {"):]
    ref = ref[:ref.index("\n}\n")]
    cmp = "canonical_answer(p, a, answer_of(h, a.image)) != canonical_answer(p, a, a.type)"
    if cmp not in one or cmp not in ref:
        raise SystemExit("still_valid and still_valid_from no longer make the same compare")
    vec = sh[sh.index("void answer_handles_memo("):]
    vec = vec[:vec.index("\n}\n")]
    if "texture_slot_version(" not in vec or "slot_word(" not in vec:
        raise SystemExit("answer_handles_memo no longer holds the slot and its version")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="clang++")
    a = p.parse_args()
    check_wiring()
    with tempfile.TemporaryDirectory(prefix="prog-memo-") as temp:
        exe = Path(temp) / "prog_memo_test.exe"
        subprocess.run([a.cxx, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror", str(ROOT / "tests/prog_memo_test.cpp"), "-o", str(exe)],
                       check=True)
        out = subprocess.run([str(exe)], check=True, timeout=120, capture_output=True, text=True).stdout
        assert " 0 mismatches" in out, out
    print("PASS:", out.strip())


if __name__ == "__main__":
    main()
