"""Guest registers and flags as C locals (HL_GUEST_LOCALS=1, off by default): the kit's A64_LOCAL_REGS / A64_LOCAL_FLAGS
turned on for the lift, with the rules that keep the game's patches, hooks and gates working.

Ported from Longshot deb08f36f, "perf: less game work a frame in Castle Town (registers as locals, vector rewrites)"
(MagicBeanDev/Longshot #1576, by Veix, with HumanBean's follow-ups: the ran-functions gate, the packed-flags rule),
GPL-3.0-or-later. Adapted to HeyListen: its lift has no fastfp / dead_flags / walk wrappers and no entry goto, its own patch
modules (oot_patches.py and what it applies: oot_native_resolution.py, native_replace.py, hl_gates.py's gates and hooks),
its loop-header polls (nx2/poll.py) and its hot-function passes (A64_HOT_FUNCS: hot_text, the FPX blocks).

A function's most used guest registers (6) and the four flags live in C locals, written back to the context only where
something else may read them (the kit's context marks: calls, returns, tail calls, indirect jumps, SVCs, traps), where
the lifted code otherwise loads and stores the context around every instruction.

Nothing here runs unless HL_GUEST_LOCALS=1 (enabled()): nx2/lift.py calls install() / install_plain() / the keep_*
functions only then, so a lift without it is the lift as before, byte for byte. Only the standalone lift (--standalone, the
game HeyListen builds) takes it: see POLLS below for why the emulator lift does not.

  - the defaults: a64_lift.local_regs / local_flags answer 6 registers and flags on; A64_LOCAL_REGS=<k> and
    A64_LOCAL_FLAGS=0 change them (the kit took any A64_LOCAL_FLAGS value, "0" too, as on), and the kit's
    A64_PACKED_FLAGS experiment turns the flags off (its a64_set_nzcv writes the context where localize_regs cannot see
    it). Lift-time switches: nx2/make_game.py's lift key holds them.
  - a function that keeps its registers in the context (Lifter.no_local) keeps its flags there too (the kit made them
    locals all the same): localize_regs is wrapped for that, and emit_function tells it which function is being written.
  - keep_spliced(): a function with C spliced after one of its instructions (Lifter.after: oot_wide, oot_jitter,
    oot_occlusion, oot_view, and the fork's oot_shadow / oot_fov / oot_near: all go through Lifter.after) joins no_local:
    that C reads and writes the context.
  - keep_entered_below(): a function whose code starts below its entry joins no_local (in Longshot nx2/lift_fixes.py
    jumps to the entry from the head, over the locals' loads; HeyListen's lift_fixes writes no such jump, so here it is a
    conservative rule that costs a few functions their locals).
  - keep_not_ran(): only the functions nx2/ran_funcs.txt names (play was seen to run them; Longshot's list, the same game
    build e0f3ccfa) may have locals; the rest are written as without this change (A64_LOCAL_FUNCS=all: every function may).
  - keep_mentioned(patch_addresses()): every function that holds an address a patch module names (a head `void f_8...`, a
    label `L_8...`, a hex `0x8...`) or that host/hl_*.c gates or hooks (hl_gates.py: HL_GATE, HL_STEP_HOOK, HL_MOVIE_HOOK
    and the lift hooks, which find their function by its address and, for gates, by the hash of its context text).
  - a function the patches would change keeps the context: install(touched) takes oot_patches.touches, which runs every
    text edit oot_patches.apply makes on the one function's text as it reads with the context kept, as the edits will
    read it (its polls in: some anchors include them; hot_text done for a hot function); localize_regs asks it of every function before giving it locals.
  - guard_patches(): around oot_patches.apply, the functions written with locals are hashed before and after the edits; one
    that an edit changed stops the lift (the patch's host code would read a stale context). Mark-free: whatever wrote it.
  - install_plain(): such a kept function is also written with the kit's context-flag text for every instruction (the kit
    decides once per lift how an instruction writes flags; hl_gates' horse and floor hooks look for `a64_fcmp32(c,...`).

POLLS (nx2/poll.py's `A64_POLL(c);` at loop headers). In the standalone build A64_POLL calls a64_poll(c) (host/mkw_glue.c):
it clears c->poll and calls hos_vcore_checkpoint(), the virtual cores' yield and the background throttle, which by its
contract (host/hos_thread.c) never reads or writes the thread's guest context and runs no guest code: the loop's registers
may be in host registers there, and the thread comes back to the same C frame. So locals need no write-back at a poll, and
a function with polls may have locals (most of the gain is in loops). The emulator lift (no --standalone) is different:
its a64_poll hands the thread to Eden (nx2/rt/eden_shim.c host.poll), which may read the context while it is away, so
lift.py gives that lift no locals at all. The poll stays in every function either way: nothing here removes one.
"""
import bisect
import glob
import hashlib
import os
import re

import a64_lift

REGS = 6   # (Longshot, measured 2026-11-15: 4 and 8 are slower than 6; 8 spills on x86)
HERE = os.path.dirname(os.path.abspath(__file__))


def enabled():
    """HL_GUEST_LOCALS=1: the locals policy for this lift (off by default in this fork)"""
    return os.environ.get("HL_GUEST_LOCALS", "") == "1"


def local_regs():
    """A64_LOCAL_REGS: how many of a function's guest registers become locals (0: none; unset: REGS)"""
    v = os.environ.get("A64_LOCAL_REGS", "")
    return int(v) if v.isdigit() else REGS


def local_flags():
    """the four flags as locals, unless A64_LOCAL_FLAGS=0, or the kit's A64_PACKED_FLAGS experiment is set (any value,
    as the kit's pack_flags reads it): it folds a flag-setting instruction's four stores into a64_set_nzcv(c, ...),
    which writes the context where localize_regs does not see it, so a later B.EQ would read a stale local Z"""
    if os.environ.get("A64_PACKED_FLAGS"):
        return False
    return os.environ.get("A64_LOCAL_FLAGS", "1")[:1] != "0"


def keep_spliced(lifter):
    """the functions with C spliced after an instruction keep the context: into lifter.no_local. Returns how many"""
    if not lifter.after:
        return 0
    spliced = {e for e, (body, _) in lifter.funcs.items() if any(pc in body for pc in lifter.after)}
    lifter.no_local |= spliced
    return len(spliced)


def keep_entered_below(lifter):
    """the functions whose code starts below their entry keep the context: into lifter.no_local. Returns how many.
    A PLT stub is written without its body and is not one"""
    below = {e for e, (body, _) in lifter.funcs.items() if e not in lifter.plt and body and min(body) != e}
    lifter.no_local |= below
    return len(below)


RAN = os.path.join(HERE, "ran_funcs.txt")


def ran_functions():
    """the functions that may get locals (A64_LOCAL_FUNCS): unset, the ones nx2/ran_funcs.txt names (Longshot's: the
    functions a 100-minute play session ran, 77,725 of the game's ~295,000); `all`, every function (None). No such file:
    every function too. The file is in the lift key (a .txt in nx2/: make_game.py's lift_key hashes it)"""
    if os.environ.get("A64_LOCAL_FUNCS", "").strip().lower() == "all" or not os.path.isfile(RAN):
        return None
    with open(RAN, encoding="ascii") as f:
        return {int(w[2:], 16) for line in f for w in line.split()[:1] if w.startswith("f_")}


def keep_not_ran(lifter, ran=None):
    """the functions no play session was seen to run keep the context: into lifter.no_local. Returns how many, or None
    when every function may have locals. Such a function is written as without this change, so one the list misses
    that does run is as fast as before, never slower. ran: a set of entries (ran_functions() when not given)"""
    ran = ran_functions() if ran is None else ran
    if ran is None:
        return None
    cold = {e for e in lifter.funcs if e not in ran and e not in lifter.plt}
    lifter.no_local |= cold
    return len(cold)


def keep_mentioned(lifter, addresses):
    """the functions that hold one of `addresses` (an entry, or an instruction's address) keep the context: into
    lifter.no_local. Returns how many"""
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
    return len(found)


# (a function by its own head, a label, a hex address; not a function merely called: "f_80522050(c)" names the callee,
# and the call's place is its return address, which is named too)
_ADDRESS = re.compile(r"(?:void f_|L_|0x)(8[0-9a-fA-F]{7})(?![0-9a-fA-F])")
# the modules whose edits or hooks find their place in the lifted text (the fork's oot_*.py come in by the glob)
PATCH_MODULES = ("oot_patches.py", "oot_native_resolution.py", "native_replace.py", "hl_gates.py")


def patch_sources():
    return sorted({os.path.join(HERE, m) for m in PATCH_MODULES} | set(glob.glob(os.path.join(HERE, "oot_*.py"))))


def hl_hook_addresses(host=None):
    """every guest function host/hl_*.c gates or hooks (nx2/hl_gates.py: HL_GATE, HL_STEP_HOOK, HL_MOVIE_HOOK, the lift
    hooks). Their places are found by the function's address and the hash of its context text, so each keeps the context.
    (Also asked by make_game.py's lift key with HL_GUEST_LOCALS=1: a gate added to host/ later lifts again)"""
    import hl_gates
    host = host or hl_gates.HOST
    out = set()
    for got in (hl_gates.wanted(host), hl_gates.wanted_steps(host), hl_gates.wanted_movies(host), hl_gates.wanted_lifts(host)):
        out.update(int(a, 16) for a in got)
    return out


def patch_addresses(host=None):
    """every guest code address the patch modules name, and every function host/hl_*.c hooks: the places their edits are
    pinned to. An address named for another reason costs one function its locals"""
    out = set()
    for path in patch_sources():
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                out.update(int(a, 16) for a in _ADDRESS.findall(f.read()))
    return out | hl_hook_addresses(host)


# ---- the check after the edits (guard_patches) ----
LOCAL_DECL = re.compile(r"^    uint(?:64_t x\d+_ = c->x\[|8_t fn_ = c->n)", re.M)   # (localize_regs' declarations)
HEAD = re.compile(r"^void f_([0-9a-f]+)\(Ctx \*__restrict A64_NOESC c\) \{$", re.M)


def local_bodies(s):
    """{entry: hash of its body} of the functions in one file's text that have locals (head to its closing brace: the lines
    between functions, which hl_gates' presence lines and the files' declarations go into, are not in it)"""
    out = {}
    for m in HEAD.finditer(s):
        end = s.find("\n}\n", m.end())
        body = s[m.start():end + 3 if end >= 0 else len(s)]
        if LOCAL_DECL.search(body):
            out[m.group(1)] = hashlib.sha1(body.encode("utf-8", "surrogatepass")).hexdigest()
    return out


def local_functions(gen):
    out = {}
    for path in sorted(glob.glob(os.path.join(glob.escape(gen), "code_*.c"))):
        with open(path, encoding="utf-8", newline="") as f:
            out.update(local_bodies(f.read()))
    return out


def check_unchanged(before, after):
    """stops the lift when an edit changed a function that has locals. Returns how many functions with locals were checked"""
    bad = sorted(e for e, h in before.items() if after.get(e) != h)
    if bad:
        raise SystemExit("locals: the patches changed %d functions that were given locals (f_%s): their edits and the host code "
                         "they call read the context. nx2/oot_patches.py touches() or nx2/guest_locals.py patch_addresses() "
                         "must name them" % (len(bad), " f_".join(bad[:12])))
    return len(before)


_checked = [None]


def guard_patches():
    """oot_patches.apply wrapped (chunks.edit_c calls it by the module's name): the functions with locals hashed before
    and after it; any it changed stops the lift. How many were checked: checked()"""
    import oot_patches
    base = oot_patches.apply
    if getattr(base, "_guest_locals", False):
        return

    def apply(gen, *args, **kw):
        before = local_functions(gen)
        r = base(gen, *args, **kw)
        _checked[0] = check_unchanged(before, local_functions(gen))
        return r
    apply._guest_locals = True
    oot_patches.apply = apply


def checked():
    return _checked[0]


# ---- the wrappers ----
_keep = [False]   # (the function being emitted keeps its context: emit_function below sets it for localize_regs)
_hot = [False]    # (and is a hot one: its text goes through hot_text before the patches see it)
_kept = [0]       # (functions the patches would change, kept in the context by localize_regs since emit_function last read it)
_installed = False


def install(touched=None):
    """the defaults, and localize_regs and emit_function wrapped. Once. touched(text): whether the game's patches would
    change this function, given its text with the context kept, as the patches will read it (polls in, hot_text done)"""
    global _installed
    if _installed:
        return
    _installed = True
    import poll
    a64_lift.local_regs = local_regs
    a64_lift.local_flags = local_flags
    base_localize = a64_lift.localize_regs
    base_emit = a64_lift.Lifter.emit_function
    hot = a64_lift.hot_functions()

    def kept(text):
        """the function with its registers and flags in the context"""
        a64_lift.local_flags = lambda: False
        try:
            return base_localize(text, False, None)
        finally:
            a64_lift.local_flags = local_flags

    def as_patched(plain):
        """the text the patches will see: polls added (poll.py's wrapper, outside this one), hot_text for a hot function"""
        text = plain + "\n"
        if poll._installed:
            text = poll.add_polls(text)[0]
        return a64_lift.hot_text(text) if _hot[0] else text

    def localize_regs(text, local, dirty):
        if _keep[0]:
            return kept(text)
        if touched is not None and (local or local_flags()):
            plain = kept(text)
            if touched(as_patched(plain)):   # (a patch finds its place here: it stays as the patch expects it)
                _kept[0] += 1
                return plain
        return base_localize(text, local, dirty)

    def emit_function(self, e, texts):
        _keep[0] = e in self.no_local
        _hot[0] = bool(hot) and self.fname(e) in hot
        _kept[0] = 0
        try:
            return base_emit(self, e, texts)
        finally:
            _keep[0] = _hot[0] = False
            if _kept[0]:
                self.stats["locals_kept_for_patches"] += 1

    a64_lift.localize_regs = localize_regs
    a64_lift.Lifter.emit_function = emit_function


class _Both(str):
    """an instruction's C as the lift writes it; .raw: the translator's text it was made from, for the same instruction
    with the flags in the context (.plain, made the first time a function asks for it); .kit: carried from nx2/fastvec.py's
    text (the kit's own, for the hot functions)"""
    raw = None
    plain = None
    kit = None


class _Plain:
    """a function's view of the instructions' texts as written with the flags in the context (emit_function reads
    texts[key] only); plain_of makes such a text when it is first asked for"""

    def __init__(self, texts, plain_of):
        self.texts = texts
        self.plain_of = plain_of

    def __getitem__(self, key):
        text, flags = self.texts[key]
        return (self.plain_of(text) if isinstance(text, _Both) else text), flags


def install_plain():
    """op_text and emit_function wrapped, after install() (and after nx2/fastvec.py's install, whose emit_function this
    wraps from outside): a function in no_local is written with the kit's context-flag text for every instruction too. The
    kit decides once for the whole lift how an instruction reads and writes the flags (a scalar compare is
    `{ if (!a64_fcmp32(c,a,b)) ... }` with the flags in the context and `_fk = a64_fcmp32k(...)` with local flags), whatever
    function the text ends up in; a patch finds its place by the lifted text (hl_gates.py's horse and floor hooks look for
    `a64_fcmp32(c,...`). So an instruction's text keeps the translator's text it came from, and only when a function in
    no_local is written are its instructions made once more with the flags in the context, once per distinct text."""
    base_op_text = a64_lift.op_text
    base_emit = a64_lift.Lifter.emit_function

    def op_text(t, fpx):
        r = base_op_text(t, fpx)
        if not a64_lift.local_flags():
            return r
        b = _Both(r)
        b.raw = t
        b.kit = getattr(r, "kit", None)
        return b

    def plain_of(text):
        if text.plain is None:
            keep = a64_lift.local_flags
            a64_lift.local_flags = lambda: False
            try:
                text.plain = base_op_text(text.raw, set())
            finally:
                a64_lift.local_flags = keep
        return text.plain

    def emit_function(self, e, texts):
        return base_emit(self, e, _Plain(texts, plain_of) if e in self.no_local else texts)

    a64_lift.op_text = op_text
    a64_lift.Lifter.emit_function = emit_function
