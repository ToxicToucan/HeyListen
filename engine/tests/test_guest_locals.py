# Copyright (C) 2026 Sacrappy

"""nx2/guest_locals.py (A64_LOCAL_REGS, A64_LOCAL_FLAGS) and the plain / plain0 texts, game-free, no compiler:

  - the defaults (6 registers, flags on), A64_LOCAL_REGS=0 and A64_LOCAL_FLAGS=0, the kit's A64_PACKED_FLAGS;
  - mentioned_addresses() holds the addresses the text edits pin (oot_patches' heads and labels, native_replace's, the hl_gates
    hooks of host/hl_*.c), keep_mentioned() puts the function that holds one into no_local and plain0, keep_spliced() the
    functions with C spliced in, keep_entered_below() the ones that start below their entry, keep_hot() the hot list,
    keep_not_ran() the functions an A64_LOCAL_FUNCS list does not name;
  - plain0's inverses: nx2/cmpflags.undo and nx2/vecfp.undo give back the translator's text exactly (the edits' recorded
    hashes were made on it);
  - check_patched() stops a lift that wrote a function with an edit's mark and locals.

    python -I -B tests/test_guest_locals.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "nx2"))
sys.path.insert(0, os.path.join(ROOT, "third_party", "a64-kit", "lift"))
for _n in ("A64_LOCAL_REGS", "A64_LOCAL_FLAGS", "A64_PACKED_FLAGS", "A64_LOCAL_FUNCS", "A64_HOT_FUNCS"):
    os.environ.pop(_n, None)
import cmpflags  # noqa: E402
import guest_locals as gl  # noqa: E402
import vecfp  # noqa: E402


def fail(msg):
    raise SystemExit("guest_locals: FAIL: " + msg)


class Lifter:
    """what the keep_* functions read of a lifter"""

    def __init__(self, funcs, after=(), plt=()):
        self.funcs = {e: ({pc: None for pc in body}, {}) for e, body in funcs.items()}
        self.after = {pc: "x" for pc in after}
        self.plt = set(plt)
        self.no_local = set()
        self.plain0 = set()
        self.plain1 = set()

    def fname(self, e):
        return "f_%08x" % e


def check_switches():
    if (gl.local_regs(), gl.local_flags()) != (6, True):
        fail("defaults are %s" % ((gl.local_regs(), gl.local_flags()),))
    os.environ["A64_LOCAL_REGS"] = "0"
    os.environ["A64_LOCAL_FLAGS"] = "0"
    if (gl.local_regs(), gl.local_flags()) != (0, False):
        fail("A64_LOCAL_REGS=0 / A64_LOCAL_FLAGS=0 leave one on")
    os.environ["A64_LOCAL_REGS"] = "4"
    os.environ["A64_LOCAL_FLAGS"] = "1"
    os.environ["A64_PACKED_FLAGS"] = "1"
    if (gl.local_regs(), gl.local_flags()) != (4, False):
        fail("A64_PACKED_FLAGS does not turn the flags' locals off")
    for n in ("A64_LOCAL_REGS", "A64_LOCAL_FLAGS", "A64_PACKED_FLAGS"):
        del os.environ[n]


def check_sets():
    m = gl.mentioned_addresses()
    # (heads in oot_patches.py and native_replace.py, a label in oot_native_resolution.py's neighbours, a return address)
    for a in (0x80b1e140, 0x805aeea0, 0x806702e0, 0x80a9ca9c):
        if a not in m:
            fail("%#x is not among the %d addresses the edits name" % (a, len(m)))
    import hl_gates
    gates = set(int(a, 16) for a in hl_gates.wanted())
    if not gates or not gates <= m:
        fail("the hl_gates addresses are not all mentioned")
    # (LIFT2) the output size edits' functions, named in prose only (f_810a7b70's x0 + 724 was written with locals: the lift
    # stopped on OUT_EDITS [1]); a callee's name before its call's parenthesis is not one
    for a in (0x81222ce0, 0x810a7b70, 0x80ea2f30, 0x836ce9d0, 0x83b7b340, 0x80b72340):
        if a not in m:
            fail("the output size edits' function %#x is not among the addresses the edits name" % a)
    if gl._NAMED.findall("nnMain (f_81222ce0) puts; f_831cca50(c); f_810a7b70 sets; xf_80000000, f_8000") != ["81222ce0", "810a7b70"]:
        fail("_NAMED: %s" % gl._NAMED.findall("nnMain (f_81222ce0) puts; f_831cca50(c); f_810a7b70 sets"))
    funcs = {0x80000000: [0x80000000, 0x80000004, 0x80000008], 0x80000100: [0x80000100, 0x80000104],
             0x80000200: [0x800001f0, 0x80000200], 0x80000300: [0x80000300], 0x80000400: [0x80000400]}
    L = Lifter(funcs, after=[0x80000104], plt=[0x80000400])
    if gl.keep_spliced(L) != 1 or L.no_local != {0x80000100} or L.plain0 != {0x80000100}:
        fail("keep_spliced: %s %s" % (L.no_local, L.plain0))
    if gl.keep_entered_below(L) != 1 or 0x80000200 not in L.no_local or 0x80000400 in L.no_local:
        fail("keep_entered_below: %s" % sorted(L.no_local))
    if gl.keep_mentioned(L, {0x80000004, 0x80000300, 0x7fffff00}) != 2 or not {0x80000000, 0x80000300} <= L.plain0:
        fail("keep_mentioned: %s" % sorted(L.plain0))
    if 0x80000200 in L.plain0:
        fail("a function kept only for its entry has its passes off too")
    with tempfile.TemporaryDirectory() as tmp:
        hot = os.path.join(tmp, "hot.txt")
        with open(hot, "w") as f:
            f.write("f_80000000\nf_80000300 12\n")
        os.environ["A64_HOT_FUNCS"] = hot
        L2 = Lifter(funcs)
        if gl.keep_hot(L2) != 2 or L2.no_local != {0x80000000, 0x80000300} or L2.plain1 != L2.no_local:
            fail("keep_hot: %s" % sorted(L2.no_local))
        del os.environ["A64_HOT_FUNCS"]
        lst = os.path.join(tmp, "ran.txt")
        with open(lst, "w") as f:
            f.write("f_80000000 1\nf_80000300 2\n")
        L3 = Lifter(funcs, plt=[0x80000400])
        if gl.keep_not_ran(L3) is not None:
            fail("keep_not_ran with no list keeps functions")
        os.environ["A64_LOCAL_FUNCS"] = lst
        gl.keep_not_ran(L3)   # (the PLT stub is not counted: it has no body)
        if L3.no_local != {0x80000100, 0x80000200}:
            fail("keep_not_ran: %s" % sorted(L3.no_local))
        os.environ["A64_LOCAL_FUNCS"] = "all"
        if gl.keep_not_ran(Lifter(funcs)) is not None:
            fail("A64_LOCAL_FUNCS=all keeps functions")
        del os.environ["A64_LOCAL_FUNCS"]


def check_undo():
    sub64 = "{ uint64_t _fa=(c->x[1]),_fb=(c->x[2]),_fr=(c->x[1]-c->x[2]); c->z=(_fr==0); c->n=(int)((_fr>>63)&1); c->c=(_fa>=_fb); " + \
        cmpflags.OLD[64] + " }\n"
    sub32 = sub64.replace("uint64_t", "uint32_t").replace(cmpflags.OLD[64], cmpflags.OLD[32])
    for t in (sub64, sub32, "c->x[0]=1;\n"):
        if cmpflags.undo(cmpflags.rewrite(t)) != t:
            fail("cmpflags.undo(rewrite(t)) != t for %s" % t[:80])
    for orig in ("    { uint32_t _a[4]; memcpy(_a,c->vreg[1],16); }\n", "    { x }\n    { y }\n", "a;\n"):
        wrapped = vecfp.wrap(orig, "a64_vfx_div32v(c,c->vreg[1],c->vreg[2],c->vreg[3],4)")
        if vecfp.undo(wrapped) != orig:
            fail("vecfp.undo(wrap(t)) != t for %r: %r" % (orig, vecfp.undo(wrapped)))
    if vecfp.undo("c->x[0]=1;\n") != "c->x[0]=1;\n":
        fail("vecfp.undo changed an unwrapped text")


def check_patched():
    head = "void f_80000000(Ctx *__restrict A64_NOESC c) {"
    plain = head + "\n    c->x[8] = 1; /* (nx2/oot_patches.py: x) */\n}\n"
    local = head + "\n    uint64_t x8_ = c->x[8];\n    x8_ = 1; /* (nx2/oot_patches.py: x) */\n}\n"
    other = "void f_80000100(Ctx *__restrict A64_NOESC c) {\n    uint64_t x8_ = c->x[8];\n    x8_ = 2;\n}\n"
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "code_000.c"), "w", newline="\n") as f:
            f.write('#include "a64.h"\n' + plain + other)
        if gl.check_patched(tmp) != 1:
            fail("check_patched does not count a marked function with no locals")
        with open(os.path.join(tmp, "code_000.c"), "w", newline="\n") as f:
            f.write('#include "a64.h"\n' + local + other)
        try:
            gl.check_patched(tmp)
        except SystemExit as e:
            if "f_80000000" not in str(e) and "80000000" not in str(e):
                fail("check_patched stops without naming the function: %s" % e)
        else:
            fail("check_patched let a marked function with locals through")
        # (LIFT2) a gate's constructor line above the gated function's head belongs to it, not to the function before
        gate = ("__attribute__((constructor)) static void hl_gate_in_80000100(void) { hl_gate_present(0x80000100ULL); }   "
                "/* hl gate 80000100 */\n")
        gated = ("void f_80000100(Ctx *__restrict A64_NOESC c) {\n    { if (hl_gate_80000100(c)) return; }   /* hl gate 80000100 */\n"
                 "#if A64_BK\n}\n#endif\n}\n")
        with open(os.path.join(tmp, "code_000.c"), "w", newline="\n") as f:
            f.write('#include "a64.h"\n' + other + gate + gated)
        if gl.check_patched(tmp) != 1:
            fail("check_patched counted a gate's constructor line for the function above it")


def check_text_passes():
    """(LIFT2) the main texts are kept whenever a pass changes the text, the locals off too; all off, none"""
    names = ("A64_LOCAL_REGS", "A64_LOCAL_FLAGS", "A64_SUB_V", "A64_VECFP", "A64_VBYTES", "A64_VMOVES", "A64_NATIVE_FP")
    saved = {n: os.environ.get(n) for n in names}
    try:
        for n in names:
            os.environ[n] = "0"
        import a64_lift
        gl.install()
        if gl.text_passes():
            fail("text_passes() with every pass off")
        for n in names[2:]:
            os.environ[n] = "1"
            if not gl.text_passes():
                fail("text_passes() misses %s=1 with the locals off" % n)
            os.environ[n] = "0"
        if a64_lift.local_regs() != 0:
            fail("A64_LOCAL_REGS=0 not read by the kit")
    finally:
        for n, v in saved.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v


def check_vregs():
    """(ProjectReLink, pixl27) nx2/nativefp.py's A64_LOCAL_VREGS: the vector registers a function names as locals, written
    back and read again at the marks as the guest's ABI says (v0-v15 around calls, v16-v31 never there, all at an SVC)"""
    import a64_lift
    import nativefp
    os.environ["A64_LOCAL_REGS"] = "6"
    try:
        gl.install()
        mark = lambda k, i: "\x01%s%d\x01" % (k, i)   # noqa: E731
        text = ("void f_80000000(Ctx *__restrict A64_NOESC c) {\n"
                "    c->x[0] = c->x[1] + 1;\n"
                "    { memcpy(c->vreg[3],c->vreg[17],16); }\n"
                "    c->x[30] = 0x80000010ULL; " + mark("C", 2) + "f_80000100(c);" + mark("B", 2) + "\n"
                "    a64_vset64(c->vreg[3],c->x[0]);\n"
                "    " + mark("R", 4) + "return;\n"
                "    " + mark("A", 5) + "TAILCALL(f_80000200);\n}\n")
        v = lambda n: 1 << (nativefp.VSHIFT + n)   # noqa: E731
        before = [0, 1, 1 | v(3) | v(17), v(17), v(3) | v(17), v(3) | v(17)]
        dirty = (before, before)
        out = nativefp.localize_regs(text, True, dirty)
        lines = out.split("\n")
        if "uint64_t v3_[2] = { c->vreg[3][0], c->vreg[3][1] }, v17_[2] = { c->vreg[17][0], c->vreg[17][1] };" not in out:
            fail("vregs: no locals' declaration:\n" + out)
        if "memcpy(v3_,v17_,16)" not in out or "a64_vset64(v3_,x0_)" not in out or "c->vreg[3]," in out or "c->vreg[17]," in out:
            fail("vregs: the registers not renamed:\n" + out)
        call = [x for x in lines if "f_80000100(c)" in x][0]
        if "c->vreg[3][0] = v3_[0]" not in call.split("f_80000100")[0] or "c->vreg[17]" in call.split("f_80000100")[0]:
            fail("vregs: before a call v0-v15 are written back, v16-v31 not: %r" % call)
        after = call.split("f_80000100(c);")[1]
        if "v3_[0] = c->vreg[3][0]" not in after or "v17_" in after:
            fail("vregs: after a call v0-v15 are read again, v16-v31 not: %r" % call)
        ret = [x for x in lines if "return;" in x][0]
        if "c->vreg[3][0] = v3_[0]" not in ret or "v17" in ret:
            fail("vregs: at a return v3 written back, v17 not: %r" % ret)
        tail = [x for x in lines if "TAILCALL" in x][0]
        if "c->vreg[17][0] = v17_[0]" not in tail or "c->vreg[3][0] = v3_[0]" not in tail:
            fail("vregs: at a tail call everything written back: %r" % tail)
        if nativefp.reg_writes("{ memcpy(c->vreg[3],c->vreg[17],16); c->x[2]=1; }") != (1 << 2) | v(3) | v(17):
            fail("vregs: reg_writes")
        if a64_lift.localize_regs is nativefp.localize_regs and not os.environ.get("A64_LOCAL_VREGS") == "1":
            fail("vregs: installed with A64_LOCAL_VREGS unset")
    finally:
        os.environ.pop("A64_LOCAL_REGS", None)


def main():
    check_switches()
    check_sets()
    check_undo()
    check_patched()
    check_text_passes()
    check_vregs()
    print("guest_locals: pass")


if __name__ == "__main__":
    main()
