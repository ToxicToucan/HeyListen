"""(longshot #1576, Veix; ported by hand into HeyListen) Guest registers and flags as C locals: the kit's A64_LOCAL_REGS /
A64_LOCAL_FLAGS, on by default and made to fit HeyListen's text edits. A function's most used guest registers (6) and the four
flags live in C locals, written back to the context only where something else may read them (the kit's context marks), where
the lifted code otherwise loads and stores the context around every instruction. A64_LOCAL_REGS=0 and A64_LOCAL_FLAGS=0 turn
each off (lift time, so in the lift key); both off, the lift's C is the lift without this change.

The kit's files are not edited (third_party/: CONTRIBUTING.md). install(), called from nx2/lift.py, and the keep_* functions
lift.py asks before the functions are written change these from outside:

  - the defaults: a64_lift.local_regs / local_flags answer 6 registers and flags on with the variables unset (the kit took any
    A64_LOCAL_FLAGS value, "0" too, as on).
  - a function that keeps its registers in the context (Lifter.no_local) keeps its flags there too (localize_regs is wrapped for
    that, emit_function tells it which function is being written): the kit keeps the registers of such a function but makes its
    flags locals all the same.
  - which functions keep the context (the others get locals):
      keep_spliced: C spliced after one of their instructions (Lifter.after: widescreen, the field of view, DLSS jitter, occlusion);
      keep_entered_below: code below their entry (nx2/lift_fixes.py jumps to the entry from the head, over the locals' loads);
      keep_mentioned: the functions HeyListen's text edits find their places in (nx2/oot_patches.py, native_replace.py,
        oot_native_resolution.py, and the hl_gates.py hooks host/hl_*.c ask for): they match the lifted text (c->x[8], c->n ...) and
        the host code they call reads the context. Every function that holds a guest address one of those files names;
      keep_hot: the hot functions (A64_HOT_FUNCS): a64_block.py lifts their float runs as blocks by the shapes of the context
        text (TICK-063), and the PGO profile is recorded on their control flow: they keep the context, and no `if` of nx2/vecfp.py
        and no rewrite of nx2/fastvec.py (A64_SUB_V only changes an expression: it stays);
      keep_not_ran: when A64_LOCAL_FUNCS names a file (one function a line, `f_<address>`), the functions it does not list.
        Longshot ships a list of the 77,725 functions a play session ran; HeyListen ships none (nothing of the game in the
        repository): unset, or `all`, every other function gets locals.
  - every function in no_local is written with the instructions' texts as without the flags as locals (the kit decides once for the
    whole lift how a scalar compare reads the flags; a patch finds its place by the context text), and the mentioned ones, whose text
    the hl_gates hashes and the other edits were recorded against, with the lift's other passes off too (A64_SUB_V, A64_VECFP,
    A64_VBYTES, A64_VMOVES, and nx2/nativefp.py's A64_NATIVE_FP): their text is the lift's as before this change, to the byte.
    (LIFT2) This holds whenever one of those passes is on, the locals off too (text_passes(); nx2/lift.py asks the keep_*
    functions then): with the locals off and the other passes on, the gated functions were written with the passes' text and
    every hl_gates gate and hook missed its recorded hash.
  - check_patched() looks at the written files afterwards and stops the lift if a function carries a text edit's mark and has locals.
"""
import bisect
import glob
import os
import re

import a64_lift

REGS = 6   # (Longshot measured 2026-11-15: 4 and 8 are slower than 6; 8 spills on x86)
HERE = os.path.dirname(os.path.abspath(__file__))


def local_regs():
    """A64_LOCAL_REGS: how many of a function's guest registers become locals (0: none; unset: REGS)"""
    v = os.environ.get("A64_LOCAL_REGS", "")
    return int(v) if v.isdigit() else REGS


def local_flags():
    """the four flags as locals, unless A64_LOCAL_FLAGS=0, or the kit's A64_PACKED_FLAGS experiment is set (any value, as the
    kit's pack_flags reads it): it folds a flag-setting instruction's four stores into a64_set_nzcv(c, ...), which writes the
    context where localize_regs does not see it, so a later B.EQ would read a stale local Z"""
    if os.environ.get("A64_PACKED_FLAGS"):
        return False
    return os.environ.get("A64_LOCAL_FLAGS", "1")[:1] != "0"


def keep_spliced(lifter):
    """the functions with C spliced after an instruction keep the context: into lifter.no_local and lifter.plain0. Returns how many"""
    if not lifter.after:
        return 0
    spliced = {e for e, (body, _) in lifter.funcs.items() if any(pc in body for pc in lifter.after)}
    lifter.no_local |= spliced
    lifter.plain0 |= spliced
    return len(spliced)


def keep_entered_below(lifter):
    """the functions whose code starts below their entry keep the context: into lifter.no_local. Returns how many.
    nx2/lift_fixes.py writes `goto L_<entry>;` right after such a function's head, which is where localize_regs declares the
    locals and loads them from the context: the jump would pass over the loads. A PLT stub is written without its body and is not one"""
    below = {e for e, (body, _) in lifter.funcs.items() if e not in lifter.plt and body and min(body) != e}
    lifter.no_local |= below
    return len(below)


def keep_hot(lifter):
    """the hot functions (A64_HOT_FUNCS) keep the context: into lifter.no_local, and into lifter.plain1 (written without
    nx2/vecfp.py's `if` and nx2/fastvec.py's rewrite: the PGO profile was recorded on their control flow, which a wrapped
    instruction or a removed byte loop changes, and clang drops a profile whose function hash differs)"""
    names = a64_lift.hot_functions()
    hot = {e for e in lifter.funcs if lifter.fname(e) in names}
    lifter.no_local |= hot
    lifter.plain1 |= hot - lifter.plain0
    return len(hot)


def ran_functions():
    """the functions that may get locals (A64_LOCAL_FUNCS=<file>, one `f_<address>` a line): None (every function) when it is
    unset, empty or `all`"""
    v = os.environ.get("A64_LOCAL_FUNCS", "").strip()
    if v.lower() in ("", "all") or not os.path.isfile(v):
        return None
    with open(v, encoding="ascii") as f:
        return {int(w[2:], 16) for line in f for w in line.split()[:1] if w.startswith("f_")}


def keep_not_ran(lifter, ran=None):
    """the functions the list does not name keep the context: into lifter.no_local. Returns how many, or None when every function
    may have locals"""
    ran = ran_functions() if ran is None else ran
    if ran is None:
        return None
    cold = {e for e in lifter.funcs if e not in ran and e not in lifter.plt}
    lifter.no_local |= cold
    return len(cold)


# (a function by its own head, a label, a hex address; not a function merely called: "f_80522050(c)" names the callee,
# and the call's place is its return address, which is named too)
_ADDRESS = re.compile(r"(?:void f_|L_|0x)(8[0-9a-fA-F]{7})(?![0-9a-fA-F])")
# (LIFT2) a function named in prose, "f_810a7b70 sets up ..." or "nnMain (f_81222ce0)": the output size edits
# (oot_patches.OUT_EDITS) name their functions only so, and two of their eight places hold no address the pattern above sees
# (f_810a7b70's x0 + 724 and nnMain's 2560 x 1440): the lift gave those functions locals and the edits found nothing. A name
# right before "(" is a call, whose callee keeps its locals. ProjectReLink (pixl27) keeps every f_ name of these files.
_NAMED = re.compile(r"(?<![0-9A-Za-z_])f_(8[0-9a-fA-F]{7})(?![0-9a-fA-F(])")
EDIT_FILES = ("oot_patches.py", "native_replace.py", "oot_native_resolution.py")


def text_passes():
    """a pass that changes the lifted text is on (the locals, A64_SUB_V, A64_VECFP, A64_VBYTES / A64_VMOVES, A64_NATIVE_FP): the
    functions the text edits and the gates read are then written as main writes them (keep_mentioned's plain0), and the hot
    ones without vecfp's `if` (keep_hot). (LIFT1's pieces 1-4 alone, with the locals off, wrote the gated functions with the
    other passes' text: every hl_gates gate and hook missed its recorded hash)"""
    import cmpflags
    import fastvec
    import nativefp
    import vecfp
    return bool(a64_lift.local_regs() or a64_lift.local_flags() or cmpflags.on() or vecfp.groups() or
                fastvec.pass_on("VBYTES") or fastvec.pass_on("VMOVES") or nativefp.native_fp())


def mentioned_addresses():
    """Every guest code address HeyListen's text edits name: the heads, labels and hex addresses of nx2/oot_patches.py,
    native_replace.py and oot_native_resolution.py, the functions they name in prose (f_<address> not followed by a call's
    parenthesis), and the addresses host/hl_*.c asks hl_gates.py to gate or hook (gates, step hooks, movie hooks, lift hooks).
    An edit pins its place by such an address, so a function that holds one is a function an edit may change, whatever else
    it wants to see before it acts: the lift leaves each as the edits expect it. An address named for another reason costs
    one function its locals"""
    found = set()
    for name in EDIT_FILES:
        with open(os.path.join(HERE, name), encoding="utf-8") as f:
            s = f.read()
        found |= {int(a, 16) for a in _ADDRESS.findall(s)}
        found |= {int(a, 16) for a in _NAMED.findall(s)}
    import hl_gates
    for table in (hl_gates.wanted(), hl_gates.wanted_steps(), hl_gates.wanted_movies(), hl_gates.wanted_lifts()):
        found |= {int(a, 16) for a in table}
    return found


def keep_mentioned(lifter, addresses):
    """the functions that hold one of `addresses` (an entry, or an instruction's address) keep the context: into lifter.no_local
    and lifter.plain0. Returns how many"""
    entries = sorted(lifter.funcs)
    found = set()
    for a in addresses:
        i = bisect.bisect_right(entries, a) - 1
        e = entries[i] if i >= 0 else None
        if e is not None and (e == a or a in lifter.funcs[e][0]):
            found.add(e)
        else:   # (not in the function that starts before it: code shared with another, or no code at all)
            found.update(x for x, (body, _) in lifter.funcs.items() if a in body)
    lifter.no_local |= found
    lifter.plain0 |= found
    return len(found)


# (what an edit's inserted line carries: the text edits of nx2/oot_patches.py, native_replace.py, oot_native_resolution.py, the
# hl_gates.py hooks, and the output size edits)
PATCH_MARKS = ("(nx2/oot_patches.py", "(nx2/native_replace.py", "(nx2/oot_native_resolution.py", "/* hl gate ", "/* hl step ",
               "/* hl movie ", "/* hl lift ", "(nx2/oot_wide.py")
LOCAL_DECL = re.compile(r"^    uint(?:64_t x\d+_ = c->x\[|8_t fn_ = c->n)", re.M)   # (localize_regs' declarations)
HEAD = re.compile(r"^void f_([0-9a-f]+)\(Ctx \*__restrict A64_NOESC c\) \{$", re.M)


def check_patched(gen):
    """the written C: no function with an edit's mark has locals (the edit's host code would read a stale context). Stops the lift
    naming them; returns how many marked functions it looked at"""
    bad, seen = [], 0
    for path in sorted(glob.glob(os.path.join(glob.escape(gen), "code_*.c"))):
        with open(path, encoding="utf-8", newline="") as f:
            s = f.read()
        if not any(m in s for m in PATCH_MARKS):
            continue
        heads = list(HEAD.finditer(s))
        for k, m in enumerate(heads):
            fn = s[m.start():heads[k + 1].start() if k + 1 < len(heads) else len(s)]
            # (LIFT2) up to its closing brace: what stands between it and the next head belongs to the next function (hl_gates
            # puts a gate's constructor line right above the gated function's head, which counted for the one before it)
            end = fn.rfind("\n}\n")
            if end >= 0:
                fn = fn[:end + 3]
            if any(mk in fn for mk in PATCH_MARKS):
                seen += 1
                if LOCAL_DECL.search(fn):
                    bad.append(m.group(1))
    if bad:
        raise SystemExit("locals: %d edited functions were given locals (%s): the edit's text was not found by the lift's "
                         "keep_mentioned when the function was written" % (len(bad), " ".join(bad[:12])))
    return seen


_keep = [False]   # (the function being emitted keeps its context: emit_function below sets it for localize_regs)
_installed = False


class _Both(str):
    """an instruction's C as the lift writes it (flags as locals); .raw: the translator's text it was made from (after the lift's
    translate passes). .plain, .plain1 and .plain0 are the same instruction written with the flags in the context; .plain1 with
    nx2/vecfp.py's fast path taken off again (a hot function: PGO), .plain0 with nx2/cmpflags.py's too, as the lift wrote it
    before them; each made the first time a function asks, kept"""
    __slots__ = ("raw", "plain", "plain0", "plain1")

    def __new__(cls, text, raw):
        o = str.__new__(cls, text)
        o.raw, o.plain, o.plain0, o.plain1 = raw, None, None, None
        return o


class _View:
    """a function's view of the instructions' texts in one mode (emit_function reads texts[key] only)"""

    def __init__(self, texts, get):
        self.texts = texts
        self.get = get

    def __getitem__(self, key):
        text, flags = self.texts[key]
        return (self.get(text) if isinstance(text, _Both) else text), flags


def install():
    """the defaults, and localize_regs, op_text and Lifter.emit_function wrapped. Once"""
    global _installed
    if _installed:
        return
    _installed = True
    import cmpflags
    import fastvec
    import vecfp
    a64_lift.local_regs = local_regs
    a64_lift.local_flags = local_flags
    base_localize = a64_lift.localize_regs
    base_op_text = a64_lift.op_text
    base_emit = a64_lift.Lifter.emit_function

    def kept(text):
        """the function with its registers and flags in the context"""
        a64_lift.local_flags = lambda: False
        try:
            return base_localize(text, False, None)
        finally:
            a64_lift.local_flags = local_flags

    def localize_regs(text, local, dirty):
        return kept(text) if _keep[0] else base_localize(text, local, dirty)

    def op_text(t, fpx):
        r = base_op_text(t, fpx)
        if not (local_flags() or text_passes()):
            return r
        return _Both(r, t)

    def written(raw):
        keep = a64_lift.local_flags
        a64_lift.local_flags = lambda: False
        try:
            return base_op_text(raw, set())   # (the FPX shapes it needs were noted at the first writing, from the same text)
        finally:
            a64_lift.local_flags = keep

    def plain_of(text):
        if text.plain is None:
            text.plain = written(text.raw)
        return text.plain

    def plain0_of(text):
        if text.plain0 is None:
            text.plain0 = written(vecfp.undo(cmpflags.undo(text.raw)))
        return text.plain0

    def plain1_of(text):
        if text.plain1 is None:
            text.plain1 = written(vecfp.undo(text.raw))
        return text.plain1

    def emit_function(self, e, texts):
        _keep[0] = e in self.no_local
        try:
            if e in self.plain0:
                texts = _View(texts, plain0_of)
            elif e in self.plain1:
                texts = _View(texts, plain1_of)
            elif e in self.no_local:
                texts = _View(texts, plain_of)
            return base_emit(self, e, texts)
        finally:
            _keep[0] = False

    a64_lift.localize_regs = localize_regs
    a64_lift.op_text = op_text
    a64_lift.Lifter.emit_function = emit_function
    # (the lifter's own sets: no_local is the kit's; plain0 is ours. A Lifter is made once a lift: its __init__ is wrapped)
    base_init = a64_lift.Lifter.__init__

    def init(self, *a, **k):
        base_init(self, *a, **k)
        fastvec.PLAIN0.clear()
        self.plain1 = set()   # (the hot functions: the text edits' and the FP blocks' texts, the PGO profile's control flow)
        self.plain0 = fastvec.PLAIN0   # (functions written with the lift's own passes off: the text edits' hashes were recorded against it)

    a64_lift.Lifter.__init__ = init
