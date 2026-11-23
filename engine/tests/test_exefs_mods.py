# Copyright (C) 2026 Sacrappy

"""Game-free: the builder's code (exeFS) mods (nx2/exefs_mods.py) on modules and patches authored here: the IPS and IPS32 readers (offsets,
runs, the end marker), the pchtxt reader (the offset shift, @enabled / @disabled / @stop, text values, comments), the mods folder
rules (off by name, order, depth), the offset base (file offset minus 0x100), plain and LZ4 modules, what is refused (another build id,
outside the module, across segments, the header, two mods that disagree, a damaged file), that a module no patch touches is not
rewritten, and the lift key: unchanged without patches, different with them, the same again when the mod is taken out.

python -I -B tests/test_exefs_mods.py
"""
import hashlib
import importlib.util
import os
import struct
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import exefs_mods as em  # noqa: E402

spec = importlib.util.spec_from_file_location("make_game_under_test", ROOT / "nx2" / "make_game.py")
make_game = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_game)

BID = bytes(range(1, 21)) + bytes(12)   # (20 bytes of id, the rest zero, as the game's modules)
BID_HEX = BID.hex()
TEXT, RO, DATA = bytes(range(256)) * 16, b"rodata-" * 100, b"data!" * 200   # (4096, 700, 1000 bytes)
MOFF = (0, 0x1000, 0x2000)


def check(what, ok):
    if not ok:
        raise SystemExit("FAIL: " + what)


def refuses(what, fn, *words):
    try:
        fn()
    except em.PatchError as e:
        check("%s: the message names %r (got: %s)" % (what, words, e), all(w in str(e) for w in words))
        return
    raise SystemExit("FAIL: %s was not refused" % what)


def nso(bid=BID, packed=False, flags_hash=True):
    segs = [TEXT, RO, DATA]
    datas = []
    for s in segs:
        if packed:
            import lz4.block
            datas.append(lz4.block.compress(s, store_size=False))
        else:
            datas.append(s)
    hdr = bytearray(0x100)
    hdr[0:4] = b"NSO0"
    flags = (7 if packed else 0) | (0x38 if flags_hash else 0)
    struct.pack_into("<I", hdr, 0xC, flags)
    pos = 0x100
    for i, (d, s) in enumerate(zip(datas, segs)):
        struct.pack_into("<4I", hdr, 0x10 + 0x10 * i, pos, MOFF[i], len(s), 0)
        struct.pack_into("<I", hdr, 0x60 + 4 * i, len(d))
        hdr[0xA0 + 0x20 * i:0xC0 + 0x20 * i] = hashlib.sha256(s).digest()
        pos += len(d)
    hdr[0x40:0x60] = bid
    return bytes(hdr) + b"".join(datas)


def read_back(path):
    m = em.Module(path)
    return [bytes(d) for _, d in m.segs], m


def ips(records, wide=False, runs=()):
    out = bytearray(b"IPS32" if wide else b"PATCH")
    w = 4 if wide else 3
    for off, data in records:
        out += off.to_bytes(w, "big") + struct.pack(">H", len(data)) + data
    for off, n, v in runs:
        out += off.to_bytes(w, "big") + b"\0\0" + struct.pack(">H", n) + bytes([v])
    return bytes(out + (b"EEOF" if wide else b"EOF"))


def world(d, mods):
    """exefs with main (and a second module) and a mods folder: mods = {path inside mods: bytes or str}"""
    exefs, root = Path(d) / "exefs", Path(d) / "mods"
    exefs.mkdir()
    (exefs / "main").write_bytes(nso())
    (exefs / "sdk").write_bytes(nso(bytes([9]) * 20 + bytes(12)))
    for rel, data in mods.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return exefs, root


def main():
    # the readers
    check("IPS records", em.parse_ips(ips([(0x100, b"ab"), (0x2000, b"c")])) == [(0x100, b"ab"), (0x2000, b"c")])
    check("IPS runs are written out", em.parse_ips(ips([], runs=[(0x120, 5, 0xAA)])) == [(0x120, b"\xaa" * 5)])
    check("IPS32 four-byte offsets", em.parse_ips(ips([(0x1000100, b"z")], wide=True)) == [(0x1000100, b"z")])
    check("IPS: an offset spelling EOF ends the file", em.parse_ips(b"PATCH" + b"EOF" + b"junk") == [])
    refuses("a file that is not IPS", lambda: em.parse_ips(b"hello"), "not an IPS")
    refuses("an IPS cut short", lambda: em.parse_ips(b"PATCH\x00\x01"), "cut short")
    refuses("a record past the end", lambda: em.parse_ips(b"PATCH" + (0x100).to_bytes(3, "big") + struct.pack(">H", 9) + b"ab"), "runs past")
    check("a build id loses its trailing zero bytes", em.norm_bid(BID_HEX.upper()) == BID.rstrip(b"\0").hex())

    pch = """// a comment
#a note
@nsobid-%s
@flag offset_shift 0x100
@flag print_values
@enabled
00000010 DEADBEEF // inline
00000020 "Hi\\n"
@disabled
00000030 00 00
@enabled
00000040 01 02 03
@stop
00000050 FF
""" % BID_HEX.upper()
    bid, recs, notes = em.parse_pchtxt(pch)
    check("pchtxt: the build id", bid == em.norm_bid(BID_HEX))
    check("pchtxt: the offset shift is added, the disabled block and what follows @stop are left out, values read",
          recs == [(0x110, b"\xde\xad\xbe\xef"), (0x120, b"Hi\n"), (0x140, b"\x01\x02\x03")])
    check("pchtxt: the disabled block is said", any("@disabled" in n for n in notes))
    refuses("a pchtxt record that is not hex", lambda: em.parse_pchtxt("@enabled\n00000010 xyz\n"), "line 2")
    check("pchtxt: no shift means the address is the file offset", em.parse_pchtxt("@enabled\n00000200 11\n")[1] == [(0x200, b"\x11")])

    # the mods folder: depth, off by name, order
    with tempfile.TemporaryDirectory(prefix="hookshot-exefs-mods-") as d:
        d = Path(d)
        for rel in ("b/exefs", "a/exefs", "-off/exefs", "_off2/exefs", "contents/0100/C/exefs", "contents/-0100/D/exefs", "only-romfs/romfs"):
            (d / rel).mkdir(parents=True)
        found = [(m["name"], m["on"]) for m in em.find_mods(str(d))]
        check("mods found, in name order, off ones marked (%s)" % found, found == [
            ("-off", False), ("_off2", False), ("a", True), ("b", True), ("contents/-0100/D", False), ("contents/0100/C", True),
            ("only-romfs", True)])

    # applying: the offset base, plain and LZ4 modules, the file is rewritten as a plain NSO whose hashes are true
    try:
        import lz4.block  # noqa: F401
        packs = (False, True)
    except ImportError:   # (the builder's own Python has it; a bare one runs the plain case only)
        packs = (False,)
    for packed in packs:
        with tempfile.TemporaryDirectory(prefix="hookshot-exefs-mods-") as d:
            exefs, root = world(d, {"m1/exefs/%s.ips" % BID_HEX.upper(): ips([(0x100 + 0x10, b"\xaa\xbb"), (0x100 + 0x1000 + 3, b"XY")],
                                                                              runs=[(0x100 + 0x2000, 4, 0x77)])})
            if packed:
                (exefs / "main").write_bytes(nso(packed=True))
            before_sdk = (exefs / "sdk").read_bytes()
            said = []
            done = em.apply_mods(str(exefs), str(root), say=said.append)
            segs, m = read_back(str(exefs / "main"))
            want_text = bytearray(TEXT); want_text[0x10:0x12] = b"\xaa\xbb"
            want_ro = bytearray(RO); want_ro[3:5] = b"XY"
            want_data = bytearray(DATA); want_data[0:4] = b"\x77" * 4
            check("the bytes land at file offset minus 0x100, in each segment (packed=%s)" % packed,
                  segs == [bytes(want_text), bytes(want_ro), bytes(want_data)])
            check("the patched module is plain now", m.flags & 7 == 0)
            raw = (exefs / "main").read_bytes()
            check("its segment hashes are true", all(raw[0xA0 + 0x20 * i:0xC0 + 0x20 * i] == hashlib.sha256(s).digest() for i, s in enumerate(segs)))
            check("its build id is as it was", raw[0x40:0x60] == BID)
            check("a module nothing patches is not rewritten", (exefs / "sdk").read_bytes() == before_sdk)
            check("one patch file applied and said", len(done) == 1 and any("applied to main" in s for s in said))

    # pchtxt and IPS together, a mod that is off, another module by its id
    with tempfile.TemporaryDirectory(prefix="hookshot-exefs-mods-") as d:
        sdk_hex = (bytes([9]) * 20).hex()
        exefs, root = world(d, {
            "01-a/exefs/x.pchtxt": "@nsobid-%s\n@flag offset_shift 0x100\n@enabled\n00000010 AA\n" % BID_HEX,
            "02-b/exefs/y.pchtxt": "@nsobid-%s\n@enabled\n00000110 AA 22\n" % sdk_hex,   # (no shift: 0x110 is the file offset, 0x10 the address)
            "-03-c/exefs/%s.ips" % BID_HEX: ips([(0x100 + 0x10, b"\x00")]),
            "04-d/exefs/readme.txt": "not a patch"})
        said = []
        done = em.apply_mods(str(exefs), str(root), say=said.append)
        check("two patch files applied, the off mod not", [x[0] for x in done] == ["01-a", "02-b"])
        check("the off mod and the stray file are said", any("-03-c is off" in s for s in said) and any("readme.txt" in s for s in said))
        check("pchtxt and the other module", read_back(str(exefs / "main"))[0][0][0x10] == 0xAA and
              read_back(str(exefs / "sdk"))[0][0][0x10:0x12] == b"\xaa\x22")

        # (the same twice is fine: the same bytes)
    with tempfile.TemporaryDirectory(prefix="hookshot-exefs-mods-") as d:
        exefs, root = world(d, {"a/exefs/%s.ips" % BID_HEX: ips([(0x200, b"\x01\x02")]), "b/exefs/%s.ips" % BID_HEX: ips([(0x200, b"\x01\x02")])})
        check("two mods writing the same bytes the same way agree", len(em.apply_mods(str(exefs), str(root), say=lambda t: None)) == 2)

    # what is refused, and nothing half done
    cases = [
        ("a patch for another build id", {"a/exefs/%s.ips" % (bytes([5]) * 20).hex(): ips([(0x200, b"\x01")])}, ("a/", "is for build")),
        ("a write past the end", {"a/exefs/%s.ips" % BID_HEX: ips([(0x100 + 0x2000 + 999, b"\x01\x02")])}, ("writes outside",)),
        ("a write across two segments", {"a/exefs/%s.ips" % BID_HEX: ips([(0x100 + 0xFFE, b"\x01\x02\x03\x04")])}, ("writes outside",)),
        ("a write in the gap", {"a/exefs/%s.ips" % BID_HEX: ips([(0x100 + 0x1000 + 0x400, b"\x01")])}, ("writes outside",)),
        ("a write in the header", {"a/exefs/%s.ips" % BID_HEX: ips([(0x40, b"\x01")])}, ("header",)),
        ("a write into the bss", {"a/exefs/%s.ips" % BID_HEX: ips([(0x100 + 0x5000, b"\x01")])}, ("writes outside",)),
        ("two mods that disagree", {"a/exefs/%s.ips" % BID_HEX: ips([(0x200, b"\x01")]), "b/exefs/p.pchtxt": "@nsobid-%s\n@enabled\n00000200 02\n" % BID_HEX},
         ("a/", "b/", "differently")),
        ("a damaged file", {"a/exefs/%s.ips" % BID_HEX: b"PATCH\x00"}, ("a/", "cut short")),
        ("a pchtxt without its build id", {"a/exefs/p.pchtxt": "@enabled\n00000200 02\n"}, ("@nsobid",)),
        ("an IPS named otherwise than its build id", {"a/exefs/main.ips": ips([(0x200, b"\x01")])}, ("build id",)),
    ]
    for what, mods, words in cases:
        with tempfile.TemporaryDirectory(prefix="hookshot-exefs-mods-") as d:
            exefs, root = world(d, mods)
            before = (exefs / "main").read_bytes()
            refuses(what, lambda: em.apply_mods(str(exefs), str(root), say=lambda t: None), *words)
            check(what + ": nothing changed", (exefs / "main").read_bytes() == before)

    # the list the builders show
    with tempfile.TemporaryDirectory(prefix="hookshot-exefs-mods-") as d:
        exefs, root = world(d, {"a/exefs/%s.ips" % BID_HEX: ips([(0x200, b"\x01")]), "-b/exefs/z.pchtxt": "@nsobid-%s\n@enabled\n00000200 02\n" % ("ab" * 20),
                                "c/romfs/x": "y"})
        lst = em.listing(str(root), str(exefs))
        check("the list: mods with code patches only, their state and module (%s)" % lst,
              [(m["name"], m["on"], m["patches"][0]["state"], m["patches"][0]["module"]) for m in lst] ==
              [("-b", False, "other build", None), ("a", True, "ok", "main")])

    # the exefs files that are not patches: said, never silently ignored (issue 36: a mod with a subsdk7 module)
    with tempfile.TemporaryDirectory(prefix="hookshot-exefs-mods-") as d:
        exefs, root = world(d, {"hooks/exefs/%s.ips" % BID_HEX: ips([(0x200, b"")]), "hooks/exefs/subsdk7": b"NSO0", "hooks/exefs/main.npdm": b"x",
                                "only/exefs/subsdk4": b"NSO0", "only/exefs/readme.txt": "hi"})
        lst = {m["name"]: m for m in em.listing(str(root), str(exefs))}
        check("the list: a mod with only a module is listed, with its skipped files", [x["file"] for x in lst["only"]["skipped"]] == ["readme.txt", "subsdk4"] and lst["only"]["patches"] == [])
        check("the list: the skipped subsdk says why", "cannot work" in lst["hooks"]["skipped"][1]["reason"] and
              lst["hooks"]["skipped"][0]["file"] == "main.npdm")
        said = []
        done = em.apply_mods(str(exefs), str(root), say=said.append)
        text = "\n".join(said)
        check("the build log: SKIPPED subsdk7 with the reason", "SKIPPED exefs/subsdk7" in text and "cannot work" in text)
        check("the build log: the warning for a patch that comes with a module", "WARNING hooks carries a run-time code module" in text)
        check("the build log: a mod with nothing to apply is said", "nothing of this mod can be applied" in text and "only" in text)
        check("the build log: the patch is still applied", len(done) == 1 and "hooks/%s.ips applied to main" % BID_HEX in text)
        off = []
        check("the option off: the mods' exefs files are named", em.off_summary(str(root), say=off.append) == 2 and "subsdk7" in "\n".join(off))

    # the lift key: what is read from the exefs folder is what changes it
    with tempfile.TemporaryDirectory(prefix="hookshot-exefs-mods-") as d:
        exefs, root = world(d, {})
        work = Path(d) / "work"
        (work / "image").mkdir(parents=True)
        env = {"OOT_WORK": str(work)}
        with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith(("A64_", "OOT_", "RX_"))}, clear=True):
            plain = make_game.lift_key(str(exefs), str(work), env)
            check("no mods, no patch: the key is the plain game's", em.apply_mods(str(exefs), str(root), say=lambda t: None) == [] and
                  make_game.lift_key(str(exefs), str(work), env) == plain)
            p = root / "m" / "exefs" / (BID_HEX + ".ips")
            p.parent.mkdir(parents=True)
            p.write_bytes(ips([(0x300, b"\x42")]))
            em.apply_mods(str(exefs), str(root), say=lambda t: None)
            patched = make_game.lift_key(str(exefs), str(work), env)
            check("a patch changes the key", patched != plain)
            p.write_bytes(ips([(0x301, b"\x42")]))
            exefs2 = Path(d) / "exefs2"
            exefs2.mkdir()
            (exefs2 / "main").write_bytes(nso())
            (exefs2 / "sdk").write_bytes(nso(bytes([9]) * 20 + bytes(12)))
            em.apply_mods(str(exefs2), str(root), say=lambda t: None)
            check("another patch gives another key", make_game.lift_key(str(exefs2), str(work), env) not in (plain, patched))
            exefs3 = Path(d) / "exefs3"
            exefs3.mkdir()
            (exefs3 / "main").write_bytes(nso())
            (exefs3 / "sdk").write_bytes(nso(bytes([9]) * 20 + bytes(12)))
            p.unlink()
            em.apply_mods(str(exefs3), str(root), say=lambda t: None)
            check("the mod taken out: the plain game's key again", make_game.lift_key(str(exefs3), str(work), env) == plain)
    # the option's state is kept in the game folder
    with tempfile.TemporaryDirectory(prefix="hookshot-exefs-mods-") as d:
        check("off until chosen", not em.read_state(d))
        em.write_state(d, True)
        check("on once chosen, kept for the next build", em.read_state(d))
        em.write_state(d, False)
        check("and off again", not em.read_state(d))
    print("ok: code mods: IPS, IPS32 and pchtxt read, applied at file offset - 0x100, refused when wrong, and the lift key follows them")


if __name__ == "__main__":
    main()
