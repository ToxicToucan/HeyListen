"""Guest registers and flags become C locals only in the functions play runs (nx2/guest_locals.py keep_not_ran), and only
in the functions entered at their first block (keep_entered_below).

(Ported from Longshot deb08f36f's tests/test_locals_ran_only.py and tests/test_entry_below_locals.py, MagicBeanDev/Longshot
#1576, Veix and HumanBean, GPL-3.0-or-later. HeyListen's nx2/lift_fixes.py writes no entry goto, so the entered-below rule
is kept here as a conservative one, without Longshot's check of that goto.)

Locals cost compile time in every function they are given to and pay only where the code runs. nx2/ran_funcs.txt (Longshot's,
the same game build) names the 77,725 functions a 100-minute play session ran. Every other function keeps its registers and
flags in the context, as on a lift without locals.

Checked here, without the game:
  - the rules: a function not on the list joins no_local; one on it, and a PLT stub, do not; what was there stays;
    an entry above its first block joins no_local, an entry at it, a PLT stub and an empty body do not;
  - A64_LOCAL_FUNCS=all, or no list, gives every function its locals (no_local unchanged);
  - the committed list reads: f_ names, sorted, one a line, nothing else (addresses only: no game code);
  - the lift asks them only with HL_GUEST_LOCALS in the standalone lift, after the functions are known and before they
    are written;
  - the list is in the lift key (a .txt in nx2/, which make_game.py's lift_key hashes), and so are the switches.

python -I -B tests/test_locals_ran_only.py
"""
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
sys.path.insert(0, str(ROOT / "third_party" / "a64-kit" / "lift"))


class _Lifter:
    def __init__(self, funcs, plt=()):
        self.funcs = funcs if isinstance(funcs, dict) else {e: ({e: (("op",), 0)}, set()) for e in funcs}
        self.plt = {e: (0, 0) for e in plt}
        self.no_local = set()


def _body(*pcs):
    return ({pc: (("op",), 0) for pc in pcs}, set())


class RanOnly(unittest.TestCase):
    def setUp(self):
        try:
            import guest_locals as guest
        except ImportError as e:   # (the kit's Python packages are not there: the lift cannot be loaded)
            self.skipTest("nx2/guest_locals.py does not import here: %s" % e)
        self.guest = guest
        self.env = os.environ.pop("A64_LOCAL_FUNCS", None)

    def tearDown(self):
        os.environ.pop("A64_LOCAL_FUNCS", None)
        if self.env is not None:
            os.environ["A64_LOCAL_FUNCS"] = self.env

    def test_rule(self):
        lifter = _Lifter([0x1000, 0x2000, 0x3000, 0x4000], plt=(0x4000,))
        lifter.no_local.add(0x1000)   # (a spliced function, say: stays)
        self.assertEqual(self.guest.keep_not_ran(lifter, {0x1000, 0x2000}), 1)
        self.assertEqual(lifter.no_local, {0x1000, 0x3000})

    def test_all(self):
        os.environ["A64_LOCAL_FUNCS"] = "all"
        self.assertIsNone(self.guest.ran_functions())
        lifter = _Lifter([0x1000, 0x2000])
        self.assertIsNone(self.guest.keep_not_ran(lifter))
        self.assertEqual(lifter.no_local, set())

    def test_no_list(self):
        keep = self.guest.RAN
        self.guest.RAN = str(ROOT / "nx2" / "no_such_list.txt")
        try:
            self.assertIsNone(self.guest.ran_functions())
        finally:
            self.guest.RAN = keep

    def test_the_committed_list(self):
        text = (ROOT / "nx2" / "ran_funcs.txt").read_text(encoding="ascii")
        lines = text.split("\n")
        self.assertEqual(lines[-1], "", "ends with a newline")
        names = lines[:-1]
        self.assertTrue(all(re.fullmatch(r"f_8[0-9a-f]{7}", n) for n in names), "f_ names only, one a line")
        self.assertEqual(names, sorted(set(names)), "sorted, each once")
        self.assertGreater(len(names), 50000)
        ran = self.guest.ran_functions()
        self.assertEqual(len(ran), len(names))
        self.assertIn(int(names[0][2:], 16), ran)

    def test_entered_below(self):
        lifter = _Lifter({
            0x1000: _body(0x1000, 0x1004, 0x1008),          # (entered at its first block: gets locals)
            0x2010: _body(0x2000, 0x2004, 0x2010, 0x2014),  # (code below the entry)
            0x3008: _body(0x3000, 0x3008),                  # (the same)
            0x4004: _body(0x4000, 0x4004),                  # (a PLT stub is written without its body: not one)
            0x5000: ({}, set()),                            # (nothing walked: left alone)
        }, plt=(0x4004,))
        lifter.no_local.add(0x1000)   # (kept for another reason: stays)
        self.assertEqual(self.guest.keep_entered_below(lifter), 2)
        self.assertEqual(lifter.no_local, {0x1000, 0x2010, 0x3008})

    def test_the_lift_asks_before_it_writes(self):
        lift = (ROOT / "nx2" / "lift.py").read_text(encoding="utf-8")
        guard = lift.index("locals_on = guest_locals.enabled() and a.standalone")
        branch = lift.index("    if locals_on:\n", guard)
        for ask in ("guest_locals.keep_not_ran(L)", "guest_locals.keep_entered_below(L)", "guest_locals.keep_spliced(L)",
                    "guest_locals.keep_mentioned(L, guest_locals.patch_addresses())", "guest_locals.install(oot_patches.touches)"):
            at = lift.index(ask)
            self.assertLess(branch, at, "%s under the locals' own condition" % ask)
            self.assertLess(at - branch, 2500, ask)
            self.assertLess(lift.index("L.discover(max_insns"), at, "after the functions are known")
            self.assertLess(at, lift.index("chunks.write(L"), "before they are written")

    def test_in_the_lift_key(self):
        make_game = (ROOT / "nx2" / "make_game.py").read_text(encoding="utf-8")
        self.assertIn('f.endswith((".py", ".c", ".h", ".cpp", ".hpp", ".txt", ".json"))', make_game)
        self.assertIn('for k in ("HL_GUEST_LOCALS", "HL_FASTVEC") if k in os.environ', make_game)
        self.assertIn('k.startswith(("A64_", "OOT_", "RX_"))', make_game)   # (A64_LOCAL_*, A64_NO_V*)
        self.assertEqual(Path(self.guest.RAN).parent.resolve(), (ROOT / "nx2").resolve())


if __name__ == "__main__":
    unittest.main()
