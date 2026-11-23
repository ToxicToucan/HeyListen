# Copyright (C) 2026 Sacrappy

"""Code (exeFS) mods, applied by the builder to the game's modules before they are translated.

    python -I nx2/exefs_mods.py --list <mods folder> [--exefs <exefs folder>]     the mods with code patches, as JSON lines
    python -I nx2/exefs_mods.py --apply <mods folder> --exefs <exefs folder>      patch the folder's modules in place (a test)

The game is translated to PC code ahead of time, so a mod that changes its instructions has to reach the translation: the
build patches the bytes of the (decompressed) modules in its own work copy of the exefs, and the lift reads those. The
copy kept in the game folder (.hookshot_exefs) is never changed, so taking a mod out and building again gives the plain game.

Where the patches are: <mods>/<name>/exefs/ of every mod that is on (the same folders and the same rule as the RomFS mods of
host/mods.c: a folder, at any depth up to 4, holding a romfs or exefs folder is a mod; a name starting with '-' or '_', or
inside such a folder, is off; the mods are taken in the order of their names). Two formats:

  <build id>.ips      IPS ("PATCH" ... "EOF", 3-byte offsets) or IPS32 ("IPS32" ... "EEOF", 4-byte offsets), as Atmosphere reads
                      them: records are offset, 2-byte size, data; a size of 0 is a run (2-byte length, one byte); all big-endian.
                      The file name is the module's build id in hex (trailing zero bytes may be left out).
  <any name>.pchtxt   text (the IPSwitch format of emulators): @nsobid-<build id>, @flag offset_shift <n>, blocks opened by
                      @enabled or @disabled, records "<hex address> <hex bytes or "text">", // comments, # notes, @stop.

Offsets, both formats: counted from the start of the module's file, whose header is 0x100 bytes long, so the module's own
address (the one a disassembler shows with the module at 0) is the offset minus 0x100. A pchtxt address gets its offset_shift
added first (usually 0x100, which turns disassembler addresses into file offsets). A module's address range holds .text, then
.rodata and .data; a patch may write anywhere inside those three (code, constants, data) but never across two of them, into
the gap between them, into the header, or past the end (the .bss has no bytes in the file).

A patch is refused, whole, with the mod's name and the reason, when: it is for a build id no module of this game has; it writes
outside the module; its file is not readable as its format; or two mods write the same byte differently (the same value is
fine; within one file, a later record wins, as in any IPS). Nothing is half applied: the modules are changed only when every
patch has been checked.
"""
import argparse
import hashlib
import json
import os
import re
import struct
import sys

HEADER = 0x100
MODULES = ["rtld", "main"] + ["subsdk%d" % i for i in range(10)] + ["sdk"]
OFF_PREFIXES = ("-", "_")
MAX_DEPTH = 4
STATE_FILE = ".hookshot_code_mods"   # (in the game folder: "on" or "off", what the last build chose; an update keeps it)


class PatchError(Exception):
    pass


# --- finding the mods ---------------------------------------------------------------------------------------------------

def mods_root(game_folder):
    """The mods folder the game itself reads: HOS_MODS, else mods beside the executable (the game folder)"""
    return os.environ.get("HOS_MODS") or os.path.join(game_folder, "mods")


def _walk(root, rel, depth, off, found):
    d = os.path.join(root, rel) if rel else root
    if depth > 0 and (os.path.isdir(os.path.join(d, "romfs")) or os.path.isdir(os.path.join(d, "exefs"))):
        found.append({"name": rel.replace(os.sep, "/"), "path": d, "on": not off})
        return
    if depth >= MAX_DEPTH:
        return
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return
    for n in names:
        if n.startswith(".") or not os.path.isdir(os.path.join(d, n)):
            continue
        _walk(root, os.path.join(rel, n) if rel else n, depth + 1, off or n.startswith(OFF_PREFIXES), found)


def find_mods(root):
    """Every mod under root as {name, path, on}, in the order host/mods.c uses (its names, case ignored)"""
    found = []
    if root and os.path.isdir(root):
        _walk(root, "", 0, False, found)
    found.sort(key=lambda m: m["name"].lower())
    return found


def patch_files(mod):
    """The code patches of a mod: [(file name, path, kind)] ("ips" or "pchtxt") and the other files of its exefs (left alone)"""
    d = os.path.join(mod["path"], "exefs")
    patches, others = [], []
    if os.path.isdir(d):
        for n in sorted(os.listdir(d), key=str.lower):
            p = os.path.join(d, n)
            if not os.path.isfile(p):
                continue
            low = n.lower()
            if low.endswith(".ips"):
                patches.append((n, p, "ips"))
            elif low.endswith(".pchtxt"):
                patches.append((n, p, "pchtxt"))
            else:
                others.append(n)
    return patches, others


RUNTIME_MODULE = ("a code module that hooks the game while it runs (it rewrites the game's instructions in memory). A recompiled game "
                  "has no original instructions in memory to hook, so it cannot work: it is not loaded")


def why_skipped(name):
    """Why an exefs file of a mod that is not a patch (.ips, .pchtxt) is not used: [text]. Say it at build time, never silently"""
    low = name.lower()
    if low in MODULES:
        return "%s is %s" % (name, RUNTIME_MODULE)
    if low == "main.npdm":
        return "main.npdm describes the module set's permissions for the console's system software: the recompiled game does not read it"
    return "%s is not a code patch (.ips or .pchtxt), the only kind the build applies" % name


def skipped_rows(others):
    """[{file, reason}] of the exefs files of a mod that the build does not apply"""
    return [{"file": n, "reason": why_skipped(n)} for n in others]


def has_runtime_module(others):
    return any(n.lower() in MODULES for n in others)


# --- the formats --------------------------------------------------------------------------------------------------------

def norm_bid(text):
    """A build id as hex without its trailing zero bytes, in lower case: the form two spellings of one id share"""
    t = text.strip().lower()
    if len(t) % 2 or not re.fullmatch(r"[0-9a-f]*", t):
        raise PatchError("%r is not a build id (hex digits, an even count)" % text)
    while t.endswith("00"):
        t = t[:-2]
    return t


def parse_ips(data):
    """[(file offset, bytes)] of an IPS or IPS32 file; the runs are written out"""
    if data[:5] == b"PATCH":
        width, tail = 3, b"EOF"
    elif data[:5] == b"IPS32":
        width, tail = 4, b"EEOF"
    else:
        raise PatchError("not an IPS file (it starts neither with PATCH nor with IPS32)")
    pos, out = 5, []
    while True:
        if data[pos:pos + len(tail)] == tail:   # (as Atmosphere: checked before each record, so a 3-byte offset of "EOF" ends the file)
            return out
        if pos + width + 2 > len(data):
            raise PatchError("the file is cut short at byte %d (no end marker)" % pos)
        off = int.from_bytes(data[pos:pos + width], "big")
        size = struct.unpack_from(">H", data, pos + width)[0]
        pos += width + 2
        if size:
            if pos + size > len(data):
                raise PatchError("the record at offset 0x%x runs past the end of the file" % off)
            out.append((off, bytes(data[pos:pos + size])))
            pos += size
        else:
            if pos + 3 > len(data):
                raise PatchError("the run at offset 0x%x is cut short" % off)
            n = struct.unpack_from(">H", data, pos)[0]
            out.append((off, bytes([data[pos + 2]]) * n))
            pos += 3


_ESC = {"n": 10, "r": 13, "t": 9, "0": 0, "\\": 92, '"': 34, "'": 39, "a": 7, "b": 8, "f": 12, "v": 11}


def _quoted(rest, where):
    """bytes of a "text" value (C escapes) at the start of rest"""
    out, i = bytearray(), 1
    while i < len(rest):
        c = rest[i]
        if c == '"':
            return bytes(out)
        if c == "\\":
            i += 1
            e = rest[i] if i < len(rest) else ""
            if e == "x":
                h = rest[i + 1:i + 3]
                if not re.fullmatch(r"[0-9a-fA-F]{2}", h):
                    raise PatchError("%s: bad \\x escape" % where)
                out.append(int(h, 16))
                i += 2
            elif e in _ESC:
                out.append(_ESC[e])
            else:
                raise PatchError("%s: unknown escape \\%s" % (where, e))
        else:
            out += c.encode("utf-8")
        i += 1
    raise PatchError("%s: the text is not closed" % where)


def parse_pchtxt(text):
    """(build id or None, [(file offset, bytes)], notes) of a pchtxt: only the records inside @enabled blocks, before @stop"""
    bid, shift, enabled, out, notes = None, 0, False, [], []
    skipped = 0
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().lstrip("﻿")
        where = "line %d" % n
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        if line.startswith("@"):
            word, _, arg = line.partition(" ")
            arg = arg.split("//")[0].strip()
            if word.lower().startswith("@nsobid-"):
                if bid is not None:
                    raise PatchError("%s: a second @nsobid" % where)
                bid = norm_bid(word[len("@nsobid-"):])
            elif word == "@flag":
                name, _, val = arg.partition(" ")
                if name == "offset_shift":
                    try:
                        shift = int(val.strip(), 0)
                    except ValueError:
                        raise PatchError("%s: offset_shift needs a number" % where)
                elif name != "print_values":
                    notes.append("%s: the flag %r is not known and was ignored" % (where, name))
            elif word == "@enabled":
                enabled = True
            elif word == "@disabled":
                enabled = False
            elif word == "@stop":
                break
            else:
                notes.append("%s: %s is not known and was ignored" % (where, word))
            continue
        addr_text, _, rest = line.partition(" ")
        rest = rest.strip()
        if not re.fullmatch(r"[0-9a-fA-F]{1,16}", addr_text) or not rest:
            raise PatchError("%s: not an address and a value: %s" % (where, line[:60]))
        if not enabled:
            skipped += 1
            continue
        if rest.startswith('"'):
            value = _quoted(rest, where)
        else:
            hexa = re.sub(r"\s+", "", rest.split("//")[0])
            if not re.fullmatch(r"([0-9a-fA-F]{2})+", hexa):
                raise PatchError("%s: the value is not whole hex bytes: %s" % (where, rest[:60]))
            value = bytes.fromhex(hexa)
        out.append((int(addr_text, 16) + shift, value))
    if skipped:
        notes.append("%d record(s) in @disabled blocks left out" % skipped)
    return bid, out, notes


# --- the modules ----------------------------------------------------------------------------------------------------------

class Module:
    """An NSO read as plain segments, so that bytes can be patched and the file written back as a plain NSO (flags without
    compression). A module no patch touches is never written: its file stays as it was, bit for bit."""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            raw = f.read()
        if raw[:4] != b"NSO0":
            raise PatchError("%s is not a module" % os.path.basename(path))
        self.header = bytearray(raw[:HEADER])
        self.flags = struct.unpack_from("<I", raw, 0xC)[0]
        self.bid = raw[0x40:0x60].hex()
        segs = [struct.unpack_from("<4I", raw, 0x10 + 0x10 * i) for i in range(3)]
        csize = struct.unpack_from("<3I", raw, 0x60)
        if self.flags & 0x80:
            raise PatchError("%s is still compressed with ZBIC (unpack.py runs first)" % os.path.basename(path))
        self.segs = []   # [moff, bytearray]
        first = min(s[0] for s in segs)
        self.body = raw[HEADER:first]   # (the module name, which the header points at)
        for i, ((foff, moff, size, _), cs) in enumerate(zip(segs, csize)):
            d = raw[foff:foff + cs]
            if self.flags & (1 << i):
                import lz4.block   # (the translator's own decoder: a64-kit tools/nso.py)
                d = lz4.block.decompress(d, uncompressed_size=size)
            if len(d) != size:
                raise PatchError("%s: segment %d is %d bytes, %d expected" % (os.path.basename(path), i, len(d), size))
            self.segs.append([moff, bytearray(d)])

    def locate(self, off, n):
        """(segment, index in it) of the n bytes at module offset off, or None when they are not inside one segment"""
        for seg in self.segs:
            if seg[0] <= off and off + n <= seg[0] + len(seg[1]):
                return seg, off - seg[0]
        return None

    def write(self):
        hdr = bytearray(self.header)
        body = bytearray(self.body)
        for i, (moff, d) in enumerate(self.segs):
            struct.pack_into("<4I", hdr, 0x10 + 0x10 * i, HEADER + len(body), moff, len(d), struct.unpack_from("<I", hdr, 0x1C + 0x10 * i)[0])
            struct.pack_into("<I", hdr, 0x60 + 4 * i, len(d))
            if self.flags & (8 << i):   # (the segment's hash, kept true)
                hdr[0xA0 + 0x20 * i:0xC0 + 0x20 * i] = hashlib.sha256(bytes(d)).digest()
            body += d
        struct.pack_into("<I", hdr, 0xC, self.flags & ~7)
        tmp = self.path + ".patched"
        with open(tmp, "wb") as f:
            f.write(hdr)
            f.write(body)
        os.replace(tmp, self.path)


def module_ids(exefs):
    """{normalised build id: file name} of the modules in an exefs folder"""
    ids = {}
    for n in MODULES:
        p = os.path.join(exefs, n)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                head = f.read(0x60)
            if head[:4] == b"NSO0":
                ids[norm_bid(head[0x40:0x60].hex())] = n
    return ids


def read_patch(name, path, kind):
    """(normalised build id, [(file offset, bytes)], notes) of one patch file"""
    with open(path, "rb") as f:
        data = f.read()
    if kind == "ips":
        stem = name[:-4]
        if len(stem) % 2 or len(stem) > 64 or not re.fullmatch(r"[0-9a-fA-F]*", stem):
            raise PatchError("the file name must be the module's build id in hex (up to 64 digits)")
        return norm_bid(stem), parse_ips(data), []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    bid, recs, notes = parse_pchtxt(text)
    if bid is None:
        raise PatchError("no @nsobid line: the module it is for is not named")
    return bid, recs, notes


def inspect(mod, ids):
    """What a mod's code patches are, for a list: [{file, kind, state, module, records, bytes, note}] and the exefs files left alone.
    state: 'ok' (for a module of this game), 'other build', 'unreadable'"""
    patches, others = patch_files(mod)
    rows = []
    for name, path, kind in patches:
        row = {"file": name, "kind": kind, "state": "ok", "module": None, "records": 0, "bytes": 0, "note": ""}
        try:
            bid, recs, notes = read_patch(name, path, kind)
            row["records"], row["bytes"] = len(recs), sum(len(v) for _, v in recs)
            row["note"] = "; ".join(notes)
            if bid in ids:
                row["module"] = ids[bid]
            else:
                row["state"] = "other build"
                row["note"] = "for build %s, not this game's (%s)" % (bid[:16] or "?", ", ".join(sorted(k[:8] for k in ids)))
        except (PatchError, OSError) as e:
            row["state"], row["note"] = "unreadable", str(e)
        rows.append(row)
    return rows, others


def _note_changed(out, mo, old, new, mod):
    """the runs of bytes of new that differ from old, at module offset mo, as (lo, hi, mod) in out (a patch that writes what is
    there already changes nothing, so it touches no function)"""
    i = 0
    while i < len(new):
        if old[i] == new[i]:
            i += 1
            continue
        j = i
        while j < len(new) and old[j] != new[j]:
            j += 1
        out.append((mo + i, mo + j, mod))
        i = j


def apply_mods(exefs, root, say=print, ranges=None):
    """Patch the modules in exefs with every enabled mod under root. Returns the patches applied: [(mod, file, module, records, bytes)].
    ranges, a dict given in, is filled with what was written: {module: [(lo, hi, mod name)]} in the module's own addresses (file
    offset - 0x100), the bytes that differ from before only. Raises PatchError (the first problem, with the mod's name) and changes
    nothing then"""
    ids = module_ids(exefs)
    plan = {}      # module file -> {offset: (value, source)}
    writes = []    # (module file, offset, bytes, source)
    applied = []
    for mod in find_mods(root):
        patches, others = patch_files(mod)
        if not mod["on"]:
            if patches:
                say("code mods: %s is off (its name starts with - or _)" % mod["name"])
            continue
        for n in others:
            say("code mods: %s: SKIPPED exefs/%s: %s" % (mod["name"], n, why_skipped(n)))
        if others and not patches:
            say("code mods: %s: nothing of this mod can be applied (no .ips or .pchtxt file in its exefs folder)" % mod["name"])
        elif has_runtime_module(others):
            say("code mods: WARNING %s carries a run-time code module: the patches of it that are applied below may only be hooks "
                "into that module, and then do nothing useful, or worse, in this game" % mod["name"])
        for name, path, kind in patches:
            src = "%s/%s" % (mod["name"], name)
            try:
                bid, recs, notes = read_patch(name, path, kind)
            except (PatchError, OSError) as e:
                raise PatchError("%s: %s" % (src, e))
            for note in notes:
                say("code mods: %s: %s" % (src, note))
            if bid not in ids:
                raise PatchError("%s is for build %s, and this game's modules are %s. Turn the mod off (put - before its folder's name) "
                                 "or use the one made for this game version." % (src, bid[:16] or "?", ", ".join("%s %s" % (v, k[:16]) for k, v in sorted(ids.items(), key=lambda x: x[1]))))
            module = ids[bid]
            writes.append((module, recs, src))
            plan.setdefault(module, {})
            own = {}   # (a later record of one file wins over an earlier one of the same file)
            for off, value in recs:
                mo = off - HEADER
                if mo < 0:
                    raise PatchError("%s writes into the module's header (file offset 0x%x, the code starts at 0x%x)" % (src, off, HEADER))
                for i, b in enumerate(value):
                    own[mo + i] = b
            for mo, b in own.items():
                old = plan[module].get(mo)
                if old and old[1] != src and old[0] != b:
                    raise PatchError("%s and %s write byte 0x%x of %s differently (0x%02x and 0x%02x): turn one of the two mods off" %
                                     (old[1], src, mo, module, old[0], b))
                plan[module][mo] = (b, src)
            applied.append((mod["name"], name, module, len(recs), sum(len(v) for _, v in recs)))
    if not applied:
        return applied
    loaded = {}
    for module, recs, src in writes:
        if module not in loaded:
            loaded[module] = Module(os.path.join(exefs, module))
        m = loaded[module]
        for off, value in recs:
            mo = off - HEADER
            where = m.locate(mo, len(value))
            if where is None:
                raise PatchError("%s writes outside %s: 0x%x..0x%x is not inside its .text, .rodata or .data (the module's address range "
                                 "is %s)" % (src, module, mo, mo + len(value), ", ".join("0x%x..0x%x" % (s[0], s[0] + len(s[1])) for s in m.segs)))
            seg, i = where
            if ranges is not None:
                _note_changed(ranges.setdefault(module, []), mo, seg[1][i:i + len(value)], value, src.split("/")[0] if "/" in src else src)
            seg[1][i:i + len(value)] = value
    for module, m in loaded.items():
        m.write()
    for mod, name, module, n, nbytes in applied:
        say("code mods: %s/%s applied to %s: %d record(s), %d byte(s)" % (mod, name, module, n, nbytes))
    return applied


def off_summary(root, say=print):
    """When the builder's Code mods option is off: say which mods have exefs files that are therefore not used (the build log)"""
    n = 0
    for mod in find_mods(root):
        patches, others = patch_files(mod)
        if patches or others:
            n += 1
            say("code mods: the option is off: exefs of %s (%s) is not applied" % (mod["name"], ", ".join([p[0] for p in patches] + others)))
    return n


def read_state(game_folder):
    try:
        with open(os.path.join(game_folder, STATE_FILE)) as f:
            return f.read().strip() == "on"
    except OSError:
        return False


def write_state(game_folder, on):
    if on == read_state(game_folder) and (on or not os.path.exists(os.path.join(game_folder, STATE_FILE))):
        return
    with open(os.path.join(game_folder, STATE_FILE), "w") as f:
        f.write("on\n" if on else "off\n")


def listing(root, exefs):
    ids = module_ids(exefs) if exefs and os.path.isdir(exefs) else {}
    mods = []
    for mod in find_mods(root):
        rows, others = inspect(mod, ids)
        if rows or others:
            mods.append({"name": mod["name"], "on": mod["on"], "patches": rows, "skipped": skipped_rows(others)})
    return mods


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--list", metavar="MODS")
    p.add_argument("--apply", metavar="MODS")
    p.add_argument("--exefs")
    a = p.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if a.list:
        print(json.dumps(listing(a.list, a.exefs)))
    elif a.apply and a.exefs:
        try:
            apply_mods(a.exefs, a.apply)
        except PatchError as e:
            print("refused: %s" % e)
            sys.exit(1)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()


# --- the list the engine reads ------------------------------------------------------------------------------------------------

PATCH_LIST = "hookshot_code_patches.txt"   # (in the game folder, beside the build: host/hl_patched.c reads it at start)


def patched_functions(ranges, image_dir, say=print):
    """What the patches touch, in guest addresses after relocation (the image's module bases: image_dir/image.json), and the guest
    functions that contain them (the lift's function table, image_dir/functions.json: a function runs from its first instruction for
    as many instructions as the lift counted). Returns (text of the list file, {function address as 8 hex digits: mod name}).
    Also says when a patch lands in a function the OS layer answers itself (an HLE hook of the image): the patch has no effect there"""
    import bisect
    with open(os.path.join(image_dir, "image.json"), encoding="utf-8") as f:
        meta = json.load(f)
    bases = {m["name"]: m["base"] for m in meta["modules"]}
    with open(os.path.join(image_dir, "functions.json"), encoding="utf-8") as f:
        table = json.load(f)
    starts = sorted(int(k, 16) for k in table)
    hooks = {h["addr"]: h.get("sym") or h.get("c") for h in meta.get("hooks", [])}
    lines = ["# code patches (exeFS mods) of this build, in guest addresses after relocation: what the engine's native answers must leave to the "
             "lifted code (host/hl_patched.c)"]
    funcs = {}
    for module, runs in sorted(ranges.items()):
        if module not in bases:
            continue
        for lo, hi, mod in sorted(runs):
            g_lo, g_hi = bases[module] + lo, bases[module] + hi
            lines.append("range 0x%x 0x%x %s %s" % (g_lo, g_hi, module, mod))
            at = bisect.bisect_right(starts, g_lo) - 1
            for i in range(max(0, at - 8), len(starts)):
                a = starts[i]
                if a >= g_hi:
                    break
                end = a + 4 * table["%08x" % a][0]
                if end > g_lo and a < g_hi:
                    funcs.setdefault("%08x" % a, (a, end, mod))
    for key, (a, end, mod) in sorted(funcs.items()):
        lines.append("function 0x%x 0x%x %s" % (a, end, mod))
        if a in hooks:
            say("code mods: %s patches the function at 0x%x, which the OS layer answers itself (%s): the patch has no effect there" %
                (mod, a, hooks[a]))
    return "\n".join(lines) + "\n", {k: v[2] for k, v in funcs.items()}
