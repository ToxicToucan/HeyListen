"""HeyListen's patches, gates and hooks keep the context under HL_GUEST_LOCALS (nx2/guest_locals.py, nx2/oot_patches.py
touches), and both ported passes are off unless asked for.

(The rules are Longshot deb08f36f's, MagicBeanDev/Longshot #1576, Veix and HumanBean, GPL-3.0-or-later; this test is
HeyListen's, for its own patch modules.)

Checked here, without the game:
  - off by default: importing nx2/guest_locals.py and nx2/fastvec.py changes none of the kit's functions, enabled() is
    false with HL_GUEST_LOCALS / HL_FASTVEC unset, and nx2/lift.py installs them only when enabled;
  - patch_addresses(): the heads the patch modules edit (oot_patches.py, oot_native_resolution.py, native_replace.py) and
    every function host/hl_*.c gates or hooks; keep_mentioned puts the function holding such an address in no_local;
  - touches(): a function whose text one of oot_patches.apply's edits would find is touched, also when the edit's anchors
    are not all there (on the safe side), and an ordinary function is not;
  - the localize_regs wrapper: a kept function (no_local) and a touched one get no locals, registers or flags; an
    ordinary one gets both;
  - guard_patches' check: a function with locals that an edit changed stops the lift; the lines between functions (where
    hl_gates.py puts its presence lines) do not count.

python -I -B tests/test_guest_locals_patches.py
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
sys.path.insert(0, str(ROOT / "third_party" / "a64-kit" / "lift"))

try:
    import a64_lift
    KIT = {n: getattr(a64_lift, n) for n in ("localize_regs", "op_text", "hot_text", "fpx_header", "local_regs", "local_flags")}
    KIT["emit_function"] = a64_lift.Lifter.emit_function
    import fastvec
    import guest_locals
    import oot_patches
    IMPORT_ERROR = None
except ImportError as e:   # (the kit's Python packages are not there: the lift cannot be loaded)
    IMPORT_ERROR = e

HEAD = "void f_%08x(Ctx *__restrict A64_NOESC c) {\n"


def fn(addr, body):
    return HEAD % addr + body + "}"


class _Lifter:
    def __init__(self, funcs):
        self.funcs = {e: ({pc: (("op",), 0) for pc in pcs}, set()) for e, pcs in funcs.items()}
        self.plt = {}
        self.no_local = set()


class GuestLocalsPatches(unittest.TestCase):
    def setUp(self):
        if IMPORT_ERROR is not None:
            self.skipTest("nx2/guest_locals.py does not import here: %s" % IMPORT_ERROR)
        self.env = {k: os.environ.pop(k, None) for k in ("HL_GUEST_LOCALS", "HL_FASTVEC", "A64_LOCAL_REGS",
                                                         "A64_LOCAL_FLAGS", "A64_PACKED_FLAGS")}

    def tearDown(self):
        for k, v in self.env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_a_off_by_default(self):
        """(first: no test before it installs anything) importing them leaves the kit's functions its own"""
        for name in ("localize_regs", "op_text", "hot_text", "fpx_header", "local_regs", "local_flags"):
            self.assertIs(getattr(a64_lift, name), KIT[name], name)
        self.assertIs(a64_lift.Lifter.emit_function, KIT["emit_function"])
        self.assertFalse(guest_locals.enabled())
        self.assertFalse(fastvec.enabled())
        self.assertEqual(KIT["local_regs"](), 0, "the kit's default: no locals")
        lift = (ROOT / "nx2" / "lift.py").read_text(encoding="utf-8")
        self.assertIn("    if fastvec.enabled():\n        fastvec.install()", lift)
        self.assertIn("locals_on = guest_locals.enabled() and a.standalone", lift)
        self.assertIn("    if locals_on:\n        import oot_patches\n        guest_locals.install(oot_patches.touches)", lift)
        self.assertEqual(lift.count("guest_locals.install("), 1)
        self.assertEqual(lift.count("        guest_locals.install_plain()"), 1, "install_plain under locals_on too")
        self.assertEqual(lift.count("fastvec.install()"), 1)

    def test_patch_addresses(self):
        found = guest_locals.patch_addresses()
        for head in (oot_patches.VIEW_HEAD, oot_patches.LIFE_HEAD, oot_patches.MOVE_HEAD, oot_patches.WARP_HEAD,
                     oot_patches.CAM_HEAD, oot_patches.TEXT_HEAD, oot_patches.JO_RUN_HEAD, oot_patches.JO_PUSH_HEAD):
            self.assertIn(int(head[7:15], 16), found, head)
        import native_replace
        import oot_native_resolution
        for head in [r["head"] for r in native_replace.REPLACEMENTS] + [oot_native_resolution.CONFIG_HEAD,
                                                                         oot_native_resolution.SCENE_HEAD]:
            self.assertIn(int(head[7:15], 16), found, head)
        hooks = guest_locals.hl_hook_addresses()
        self.assertTrue(hooks <= found)
        import hl_gates
        self.assertEqual(hooks, {int(a, 16) for a in hl_gates.wanted()} | {int(a, 16) for a in hl_gates.wanted_steps()} |
                         {int(a, 16) for a in hl_gates.wanted_movies()} | {int(a, 16) for a in hl_gates.wanted_lifts()})

    def test_keep_mentioned(self):
        lifter = _Lifter({0x80001000: (0x80001000, 0x80001004), 0x80002000: (0x80002000, 0x80002004, 0x80002008),
                          0x80003000: (0x80003000,)})
        self.assertEqual(guest_locals.keep_mentioned(lifter, {0x80002008, 0x80003000, 0x80009000}), 2)
        self.assertEqual(lifter.no_local, {0x80002000, 0x80003000})

    def test_touches(self):
        t = oot_patches.touches
        self.assertFalse(t(fn(0x80001000, "    c->x[0] = 1ULL;\n    return;\n")))
        self.assertTrue(t(fn(0x80644c80, "    return;\n")), "the mouse camera's head")
        self.assertTrue(t(fn(0x804e48c0, "L_804e5068:; A64_POLL(c);\n    return;\n")), "the free view's anchor, polled")
        self.assertTrue(t(fn(0x804e48c0, "    return;\n")), "its head without the anchor: on the safe side")
        self.assertTrue(t(fn(0x80820000, "    " + oot_patches.OLD + "\n    return;\n")), "dynres without its label: raises")
        self.assertTrue(t(fn(0x80ea2f30, "    " + oot_patches.OUT_EDITS[2][0] + "    return;\n")), "an output-size place")
        self.assertTrue(t(fn(0x806702e0, "    return;\n")), "oot_native_resolution's config")
        self.assertTrue(t(fn(0x805aeea0, "    return;\n")), "native_replace's function")

    def test_localize_wrapper(self):
        os.environ["HL_GUEST_LOCALS"] = "1"
        touched = {"yes": False}
        guest_locals.install(lambda text: touched["yes"])
        text = fn(0x80001000, "    c->x[1] = c->x[2] + 1;\n    if (c->z) { return; }\n    c->x[1] += c->x[2];\n    return;\n")
        dirty = ([0] * 8, [0] * 8)
        out = a64_lift.localize_regs(text, True, dirty)
        self.assertIn("uint64_t x1_ = c->x[1], x2_ = c->x[2];", out)
        self.assertIn("uint8_t fn_ = c->n", out)
        touched["yes"] = True
        out = a64_lift.localize_regs(text, True, dirty)
        self.assertNotRegex(out, guest_locals.LOCAL_DECL, "touched: no locals")
        self.assertIn("if (c->z)", out)
        touched["yes"] = False
        guest_locals._keep[0] = True
        try:
            out = a64_lift.localize_regs(text, False, None)
        finally:
            guest_locals._keep[0] = False
        self.assertNotRegex(out, guest_locals.LOCAL_DECL, "no_local: no flags as locals either")

    def test_guard(self):
        local = fn(0x80001000, "    uint64_t x1_ = c->x[1];\n    x1_ += 1;\n    c->x[1] = x1_; return;\n")
        plain = fn(0x80002000, "    c->x[1] += 1;\n    return;\n")
        s = '#include "a64.h"\n' + local + "\n" + plain + "\n"
        before = guest_locals.local_bodies(s)
        self.assertEqual(list(before), ["80001000"])
        presence = "__attribute__((constructor)) static void hl_gate_in_80002000(void) { }   /* hl gate 80002000 */\n"
        edited = s.replace(HEAD % 0x80002000, presence + HEAD % 0x80002000 + "    hook(c);\n")
        self.assertEqual(guest_locals.check_unchanged(before, guest_locals.local_bodies(edited)), 1)
        with self.assertRaises(SystemExit):
            guest_locals.check_unchanged(before, guest_locals.local_bodies(s.replace("x1_ += 1;", "x1_ += 1; hook(c);")))


if __name__ == "__main__":
    unittest.main()
