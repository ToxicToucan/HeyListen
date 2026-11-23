# Copyright (C) 2026 Sacrappy

"""Statistics of the SPIR-V Hookshot's translator kept in hookshot_programs.bin, and where they sit in the GPU's frame time.

It reads a local program cache (the layout scripts/validate_spirv.py reads: records of a u64 key, a u64 size and a payload whose
first u32 is the kind, 0x80000000 set when the record is zstd-packed; Python 3.14's compression.zstd or the zstandard module
unpack it) and counts, per stage of each program, what the translator emitted: instructions by class, the global-memory helper
functions (their call sites and the code they weigh), storage-buffer counts, the predicate / LOP3 emulation (bit-field extracts
and compares of constants), loops, local arrays, 16-bit float work, texture operations and compare-exchange loops. Nothing of the
game is kept: the output holds counts and program keys only (the cache itself stays where it is).

    python -I scripts/spirv_stats.py scan   <hookshot_programs.bin> --out <dir>        (one JSON line a stage into <dir>/stages.jsonl)
    python -I scripts/spirv_stats.py report <dir> [--logs <dir> ...] [--top 25]        (totals, and the join with the GPU cost)

--logs takes game logs (or folders holding game_log.txt) of runs with RX_VK_PASS_TIMING=2: the last "GPU pass timing" block of each
gives a program's draws' and dispatches' ms a frame, averaged over the logs given. Keep <dir> outside the repository.
"""
import argparse
import array
import collections
import concurrent.futures
import glob
import json
import os
import re
import struct
import sys

CACHE_MAGIC = 0x31505348
KIND_ZSTD = 0x80000000
SPV_MAGIC = b"\x03\x02\x23\x07"
STAGES = {0x1: "vertex", 0x2: "tesscontrol", 0x4: "tesseval", 0x8: "geometry", 0x10: "fragment", 0x20: "compute",
          0x40: "task", 0x80: "mesh"}

# SPIR-V opcodes used below (SPIR-V 1.6 unified spec)
OP_NAME, OP_ENTRY, OP_EXEC_MODE = 5, 15, 16
OP_TYPE_BOOL, OP_TYPE_INT, OP_TYPE_FLOAT, OP_TYPE_VECTOR, OP_TYPE_MATRIX = 20, 21, 22, 23, 24
OP_TYPE_IMAGE, OP_TYPE_SAMPLED_IMAGE, OP_TYPE_ARRAY, OP_TYPE_RTARRAY, OP_TYPE_STRUCT, OP_TYPE_POINTER = 25, 27, 28, 29, 30, 32
OP_CONST_TRUE, OP_CONST_FALSE, OP_CONST, OP_CONST_COMPOSITE, OP_CONST_NULL = 41, 42, 43, 44, 46
OP_FUNCTION, OP_PARAM, OP_FUNCTION_END, OP_CALL = 54, 55, 56, 57
OP_VARIABLE, OP_LOAD, OP_STORE = 59, 61, 62
OP_ACCESS_CHAIN, OP_INBOUNDS_AC, OP_PTR_AC, OP_ARRAY_LENGTH = 65, 66, 67, 68
OP_LABEL, OP_LOOP_MERGE, OP_BRANCH_COND = 248, 246, 250
OP_CAS = 230
OP_BFUE = 203

ARR_SC = {7: "Function", 6: "Private", 4: "Workgroup", 2: "Uniform", 12: "StorageBuffer", 1: "UniformConstant", 9: "PushConstant",
          3: "Output", 0: "Input"}

# ops with no result id / no result type: skipped when tracking result types
NO_RESULT = set([1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 14, 15, 16, 17, 18, 62, 63, 64, 71, 72, 73, 74, 75, 99, 217, 218, 219, 220, 224, 225,
                 228, 246, 247, 249, 250, 251, 252, 253, 254, 255, 56, 330, 331, 332, 4416])
ONLY_ID = set(list(range(19, 40)) + [248, 55 * 0 - 1])   # (type declarations and labels: an id, no type)

# classes of opcodes (counted by name below); ranges are inclusive
CLASS_OF = {}


def _cls(name, lo, hi=None):
    for op in range(lo, (hi if hi is not None else lo) + 1):
        CLASS_OF[op] = name


_cls("int_arith", 126, 126)
_cls("float_arith", 127, 127)
_cls("int_arith", 128, 128)
_cls("float_arith", 129, 129)
_cls("int_arith", 130, 130)
_cls("float_arith", 131, 131)
_cls("int_arith", 132, 132)
_cls("float_arith", 133, 133)
_cls("int_arith", 134, 135)
_cls("float_arith", 136, 136)
_cls("int_arith", 137, 139)
_cls("float_arith", 140, 141)
_cls("float_arith", 142, 148)
_cls("int_arith", 149, 152)
_cls("shift", 194, 196)
_cls("bitwise", 197, 200)
_cls("bitfield", 201, 205)
_cls("logical", 164, 168)
_cls("select", 169, 169)
_cls("compare_int", 170, 179)
_cls("compare_float", 180, 189)
_cls("convert", 109, 125)
_cls("convert", 126 * 0 + 115, 115)
_cls("composite", 79, 83)
_cls("extinst", 12, 12)
_cls("load", OP_LOAD)
_cls("store", OP_STORE)
_cls("access_chain", 65, 67)
_cls("array_length", 68)
_cls("call", OP_CALL)
_cls("phi", 245)
_cls("control_flow", 246, 247)
_cls("control_flow", 248, 255)
_cls("atomic", 227, 242)
_cls("barrier", 224, 225)
_cls("image_sample", 86, 94)
_cls("image_sample", 305, 314)
_cls("image_fetch_gather", 95, 97)
_cls("image_rw", 98, 99)
_cls("image_rw", 60, 60)
_cls("image_other", 100, 107)
_cls("derivative", 207, 209)
_cls("derivative", 210, 212)
_cls("subgroup", 333, 366)
_cls("subgroup", 4421, 4432)
_cls("variable", OP_VARIABLE)
_cls("undef", 1)
_cls("kill", 252)

# pure ops whose operands are all ids: folded by a constant-propagation pass when every operand is a constant
PURE = set([OP_BFUE, 202, 201, 170, 171, 172, 173, 174, 175, 176, 177, 178, 179, 168, 167, 166, 164, 165, 169, 199, 197, 198, 200,
            194, 195, 196, 128, 130, 132, 113, 114, 124, 126])
# arithmetic ops whose scalar type width / kind is worth knowing (16-bit float, 64-bit integer, ...)
TYPED = set(range(126, 142)) | set(range(194, 206)) | set(range(109, 126)) | set([164, 165, 169]) | set(range(170, 190)) | set([12])
IMAGE_OPS = set(list(range(86, 100)) + list(range(305, 315)))


def zstd_decompress(data):
    try:
        from compression import zstd           # Python 3.14+
        return zstd.decompress(data)
    except ImportError:
        pass
    try:
        import zstandard
    except ImportError:
        raise SystemExit("packed records need Python 3.14's compression.zstd or the zstandard module")
    return zstandard.ZstdDecompressor().decompress(data, max_output_size=1 << 30)


def records(path, want=None):
    """(key, kind, bytes, packed) of each record want(key) takes, in file order (layout and the packed flag as
    scripts/validate_spirv.py reads them)"""
    with open(path, "rb") as f:
        head = f.read(16)
        if len(head) < 16 or struct.unpack_from("<I", head)[0] != CACHE_MAGIC:
            raise SystemExit("%s: not a Hookshot program cache" % path)
        while True:
            rec = f.read(16)
            if len(rec) < 16:
                return
            key, size = struct.unpack("<QQ", rec)
            if want is not None and not want(key):
                f.seek(size, 1)
                continue
            data = f.read(size)
            if len(data) < size:
                return
            if size < 4:
                continue
            kind = struct.unpack_from("<I", data)[0]
            yield key, kind & ~KIND_ZSTD, data, bool(kind & KIND_ZSTD)


def unpack(data, packed):
    return zstd_decompress(data[4:]) if packed else data


def stages(data):
    """(nvn stage, vk stage, SPIR-V bytes) of each stage of a record (found by the magic word and the three fields before it)"""
    out = []
    i = data.find(SPV_MAGIC)
    while i != -1:
        if i >= 16:
            nvn, vk = struct.unpack_from("<iI", data, i - 16)
            n = struct.unpack_from("<Q", data, i - 8)[0]
            if 0 <= nvn <= 7 and vk in STAGES and 5 <= n and i + 4 * n <= len(data):
                out.append((nvn, vk, data[i:i + 4 * n]))
                i = data.find(SPV_MAGIC, i + 4 * n)
                continue
        i = data.find(SPV_MAGIC, i + 1)
    return out


def analyse(spirv):
    """The counts of one module"""
    w = array.array("I")
    w.frombytes(spirv[:len(spirv) // 4 * 4])
    n = len(w)
    ops = collections.Counter()
    typed = collections.Counter()          # (class, scalar kind+width) of the arithmetic ops
    ttype = {}                              # type id -> ("b"|"i"|"f", width) scalar, ("v", comp, count), ("p", sc, pointee), ("a", elem, len id), ("s", members)
    const_val = {}                          # id -> literal low word (OpConstant)
    consts = set()
    idtype = {}                             # result id -> result type id
    idsc = {}                               # pointer id -> storage class
    mem = collections.Counter()             # (op, storage class name)
    foldable = collections.Counter()
    bfue_const_base = 0
    funcs = []                              # [fid, control, nparam, first param type, instrs, calls Counter, arraylength, uniform loads, ssbo ac, cond branches, loops, cas]
    cur = None
    ssbo_vars = 0
    ssbo_arrays = 0
    local_arrays = []                       # (sc, bytes)
    entry_ops = 0

    def sizeof(t):
        d = ttype.get(t)
        if not d:
            return 0
        k = d[0]
        if k in ("i", "f"):
            return d[1] // 8
        if k == "b":
            return 4
        if k == "v":
            return sizeof(d[1]) * d[2]
        if k == "a":
            return sizeof(d[1]) * const_val.get(d[2], 0)
        if k == "s":
            return sum(sizeof(m) for m in d[1])
        return 0

    def scalar_of(t):
        d = ttype.get(t)
        while d and d[0] == "v":
            d = ttype.get(d[1])
        return d

    i = 5
    cnt_instr = 0
    while i < n:
        wd = w[i]
        op = wd & 0xFFFF
        c = wd >> 16
        if c == 0 or i + c > n:
            break
        cnt_instr += 1
        ops[op] += 1
        if cur is not None:
            cur[4] += 1
        if op == OP_TYPE_INT:
            ttype[w[i + 1]] = ("i", w[i + 2])
        elif op == OP_TYPE_FLOAT:
            ttype[w[i + 1]] = ("f", w[i + 2])
        elif op == OP_TYPE_BOOL:
            ttype[w[i + 1]] = ("b", 1)
        elif op == OP_TYPE_VECTOR:
            ttype[w[i + 1]] = ("v", w[i + 2], w[i + 3])
        elif op == OP_TYPE_POINTER:
            ttype[w[i + 1]] = ("p", w[i + 2], w[i + 3])
        elif op == OP_TYPE_ARRAY:
            ttype[w[i + 1]] = ("a", w[i + 2], w[i + 3])
        elif op == OP_TYPE_RTARRAY:
            ttype[w[i + 1]] = ("r", w[i + 2])
        elif op == OP_TYPE_STRUCT:
            ttype[w[i + 1]] = ("s", tuple(w[i + 2:i + c]))
        elif op == OP_CONST:
            const_val[w[i + 2]] = w[i + 3] if c > 3 else 0
            consts.add(w[i + 2])
            idtype[w[i + 2]] = w[i + 1]
        elif op in (OP_CONST_TRUE, OP_CONST_FALSE, OP_CONST_NULL, OP_CONST_COMPOSITE):
            consts.add(w[i + 2])
            idtype[w[i + 2]] = w[i + 1]
        elif op == OP_FUNCTION:
            cur = [w[i + 2], w[i + 3], 0, None, 1, collections.Counter(), 0, 0, 0, 0, 0, 0, 0]
            funcs.append(cur)
        elif op == OP_PARAM:
            if cur is not None:
                if cur[3] is None:
                    cur[3] = w[i + 1]
                cur[2] += 1
        elif op == OP_FUNCTION_END:
            cur = None
        elif op == OP_CALL:
            if cur is not None:
                cur[5][w[i + 3]] += 1
        elif op == OP_VARIABLE:
            ptr_t = w[i + 1]
            sc = w[i + 3]
            idtype[w[i + 2]] = ptr_t
            idsc[w[i + 2]] = sc
            d = ttype.get(ptr_t)
            pointee = d[2] if d and d[0] == "p" else None
            pd = ttype.get(pointee)
            if sc == 12:
                ssbo_vars += 1
                if pd and pd[0] == "a":
                    ssbo_arrays += 1
            if sc in (6, 7) and pd and pd[0] == "a":
                local_arrays.append((sc, sizeof(pointee)))
        elif op in (OP_ACCESS_CHAIN, OP_INBOUNDS_AC, OP_PTR_AC):
            base = w[i + 3]
            sc = idsc.get(base)
            idsc[w[i + 2]] = sc
            idtype[w[i + 2]] = w[i + 1]
            if cur is not None and sc == 12:
                cur[8] += 1
        elif op == OP_LOAD:
            sc = idsc.get(w[i + 3])
            mem[(0, sc)] += 1
            idtype[w[i + 2]] = w[i + 1]
            if cur is not None:
                if sc == 2:
                    cur[7] += 1
                elif sc == 12:
                    cur[12] += 1
        elif op == OP_STORE:
            sc = idsc.get(w[i + 1])
            mem[(1, sc)] += 1
            if cur is not None and sc == 12:
                cur[12] += 1
        elif op == OP_ARRAY_LENGTH:
            if cur is not None:
                cur[6] += 1
        elif op == OP_BRANCH_COND:
            if cur is not None:
                cur[9] += 1
        elif op == OP_LOOP_MERGE:
            if cur is not None:
                cur[10] += 1
        elif op == OP_CAS:
            if cur is not None:
                cur[11] += 1
            idtype[w[i + 2]] = w[i + 1]
        elif op in PURE:
            rid = w[i + 2]
            rt = w[i + 1]
            idtype[rid] = rt
            ok = True
            for k in range(i + 3, i + c):
                if w[k] not in consts:
                    ok = False
                    break
            if ok:
                consts.add(rid)
                foldable[op] += 1
                if op == OP_BFUE:
                    bfue_const_base += 1
            if op in TYPED:
                s = scalar_of(rt)
                if s:
                    typed[(CLASS_OF.get(op, "other"), s[0] + str(s[1]))] += 1
            i += c
            continue
        if op in TYPED and op not in NO_RESULT and c > 2:
            rt = w[i + 1]
            s = scalar_of(rt)
            if s:
                typed[(CLASS_OF.get(op, "other"), s[0] + str(s[1]))] += 1
        i += c
    # derived: pointers' loads: the Uniform loads happen in functions, counted per function above

    # global-memory helpers: a function taking an u64 first (the guest address) that touches storage buffers and branches
    u64_types = {t for t, d in ttype.items() if d == ("i", 64)}
    fid_info = {f[0]: f for f in funcs}
    calls = collections.Counter()
    for f in funcs:
        for callee, k in f[5].items():
            calls[callee] += k
    helpers = []
    narrow = []
    ssbo_narrow = [f for f in funcs if f[3] not in u64_types and f[10] and f[11] and f[2] == 4]   # (the storage buffers' own 8/16-bit stores)
    for f in funcs:
        if f[3] not in u64_types:
            continue
        if f[10] and f[11] and f[2] == 4:      # (address, value, bit offset, bit count: the 8/16-bit store's compare-exchange loop)
            narrow.append({"instr": f[4], "calls": calls.get(f[0], 0), "cas": f[11]})
        elif f[8] >= 1 and f[9] >= 1:
            kind = "load" if f[2] == 1 else "write_or_atomic" if f[2] == 2 else "cas" if f[2] == 3 else "other"
            helpers.append({"instr": f[4], "calls": calls.get(f[0], 0), "inline": bool(f[1] & 1), "arraylen": f[6],
                            "ubo_loads": f[7], "ssbo_ac": f[8], "branches": f[9], "params": f[2], "kind": kind})
    helper_ids = {f[0] for f in funcs if f[3] in u64_types and ((f[10] and f[11] and f[2] == 4) or (f[8] >= 1 and f[9] >= 1))}
    direct_ssbo = sum(f[12] for f in funcs if f[0] not in helper_ids)   # (storage-buffer loads and stores outside the helpers: accesses the pass bound)
    gm_call_sites = sum(h["calls"] for h in helpers)
    gm_body = sum(h["instr"] for h in helpers)
    gm_effective = sum(h["instr"] * h["calls"] for h in helpers)
    gm_dead = sum(h["instr"] for h in helpers if h["calls"] == 0)   # (emitted, never called: the driver drops them)
    gm_loads = [h for h in helpers if h["kind"] == "load"]
    # 16-bit float / 64-bit integer work
    cls = collections.Counter()
    for op, k in ops.items():
        cls[CLASS_OF.get(op, "other")] += k
    f16 = sum(k for (c_, t), k in typed.items() if t == "f16")
    f32 = sum(k for (c_, t), k in typed.items() if t == "f32")
    f64 = sum(k for (c_, t), k in typed.items() if t == "f64")
    i64 = sum(k for (c_, t), k in typed.items() if t == "i64")
    bfue_total = ops.get(OP_BFUE, 0)
    img_sample = sum(k for op, k in ops.items() if op in IMAGE_OPS)
    out = {
        "words": n, "instr": cnt_instr, "funcs": len(funcs),
        "classes": dict(cls),
        "f16_ops": f16, "f32_ops": f32, "f64_ops": f64, "i64_ops": i64,
        "bfue": bfue_total, "bfue_const_base": bfue_const_base, "ieq": ops.get(170, 0), "ineq": ops.get(171, 0),
        "select": ops.get(169, 0), "const_foldable": sum(foldable.values()),
        "const_foldable_by_op": {str(k): v for k, v in foldable.items()},
        "loops": ops.get(OP_LOOP_MERGE, 0), "cas": ops.get(OP_CAS, 0),
        "ssbo_vars": ssbo_vars, "ssbo_arrays": ssbo_arrays,
        "local_arrays": len(local_arrays), "local_array_bytes": sum(b for _, b in local_arrays),
        "mem": {"%s_%s" % ("st" if a else "ld", ARR_SC.get(b, str(b))): k for (a, b), k in mem.items()},
        "image_sample": img_sample, "image_total": sum(k for op, k in ops.items() if op in IMAGE_OPS or 98 <= op <= 107),
        "gm_helpers": len(helpers), "gm_call_sites": gm_call_sites, "gm_body_instr": gm_body, "gm_effective_instr": gm_effective,
        "direct_ssbo_accesses": direct_ssbo, "gm_dead_body_instr": gm_dead, "gm_load_call_sites": sum(h["calls"] for h in gm_loads),
        "gm_load_helper_instr": max([h["instr"] for h in gm_loads if h["calls"]] + [0]),
        "narrow_store_body_instr": sum(h["instr"] for h in narrow),
        "ssbo_narrow_store_calls": sum(calls.get(f[0], 0) for f in ssbo_narrow),
        "gm_max_candidates": max([h["arraylen"] for h in helpers] + [0]),
        "gm_helper_detail": helpers[:12], "narrow_store_helpers": len(narrow), "narrow_store_calls": sum(h["calls"] for h in narrow),
        "calls": ops.get(OP_CALL, 0),
    }
    return out


def op_histogram(spirv):
    """{opcode: count} of a module (no other analysis)"""
    w = array.array("I")
    w.frombytes(spirv[:len(spirv) // 4 * 4])
    ops = collections.Counter()
    i, n = 5, len(w)
    while i < n:
        c = w[i] >> 16
        if c == 0 or i + c > n:
            break
        ops[w[i] & 0xFFFF] += 1
        i += c
    return ops


def cmd_ops(a):
    """opcode histograms of the stages of the given keys (a number, or its name where this file knows it)"""
    names = {v: k for k, v in globals().items() if k.startswith("OP_") and isinstance(v, int)}
    want = [k.lower().removeprefix("0x") for k in a.key]
    for key, kind, data, packed in records(a.cache, lambda k: any(("%016x" % k).startswith(x) for x in want)):
        for nvn, vk, spirv in stages(unpack(data, packed)):
            h = op_histogram(spirv)
            print("%016x %s: %d instructions" % (key, STAGES[vk], sum(h.values())))
            print("   " + ", ".join("%s %d" % (names.get(o, "op%d" % o), c) for o, c in h.most_common(a.top)))
            break


def scan_record(job):
    key, kind, data, packed = job
    try:
        data = unpack(data, packed)
    except Exception as e:   # (a damaged record)
        return [{"key": "%016x" % key, "error": str(e)}]
    res = []
    for nvn, vk, spirv in stages(data):
        r = analyse(spirv)
        r["key"] = "%016x" % key
        r["stage"] = STAGES[vk]
        r["nvn"] = nvn
        r["kind"] = kind
        res.append(r)
    return res


def cmd_scan(a):
    os.makedirs(a.out, exist_ok=True)
    want = [k.lower().removeprefix("0x") for k in a.key]
    n_prog = n_stage = 0

    def jobs():
        keep = (lambda key: any(("%016x" % key).startswith(k) for k in want)) if want else None
        for key, kind, data, packed in records(a.cache, keep):
            yield key, kind, data, packed

    pool = concurrent.futures.ProcessPoolExecutor(a.jobs) if a.jobs > 1 else None
    with open(os.path.join(a.out, "stages.jsonl"), "w") as out:
        for res in (pool.map(scan_record, jobs(), chunksize=8) if pool else map(scan_record, jobs())):
            n_prog += 1
            for r in res:
                out.write(json.dumps(r, separators=(",", ":")) + "\n")
                n_stage += 1
            if n_prog % 2000 == 0:
                print("%d programs" % n_prog, file=sys.stderr)
    if pool:
        pool.shutdown()
    print("%d programs, %d stages -> %s" % (n_prog, n_stage, os.path.join(a.out, "stages.jsonl")))


# ---------------------------------------------------------------- report

PROG_RE = re.compile(r"vk:\s+([\d.]+) ms\s+([\d.]+) runs\s+[\d.]+ us a run\s+([\d.]+) draws\s+[\d.]+ us a draw\s+prog ([0-9a-f]{16})")
DISP_RE = re.compile(r"vk:\s+m\s+([\d.]+) ms\s+([\d.]+) x\s+[\d.]+ us\s+[\d.]+ items\s+[\d.]+ MB\s+dispatch\s+prog ([0-9a-f]{16})")
PASS_RE = re.compile(r"vk:\s+m\s+([\d.]+) ms\s+([\d.]+) x\s+[\d.]+ us\s+[\d.]+ items\s+[\d.]+ MB\s+pass (\d+x\d+)")


def read_log(path):
    """{key: (draw ms a frame, dispatch ms a frame)}, the frame's passes total ms, from the last 600-present block of a log"""
    start = None
    lines = []
    with open(path, errors="replace") as f:
        for ln in f:
            if "GPU pass timing, 600 presents" in ln:
                lines = []
                start = True
            if start:
                lines.append(ln)
    draws, disp, total = {}, {}, 0.0
    in_top = False
    for ln in lines:
        if "the 20 programs whose runs of draws took the most" in ln:
            in_top = True
            continue
        if in_top and "late waits" in ln:
            in_top = False
        m = PROG_RE.search(ln)
        if m and in_top:
            draws[m.group(4)] = draws.get(m.group(4), 0.0) + float(m.group(1))
            continue
        m = DISP_RE.search(ln)
        if m and ln.find("tail") < 0:
            disp[m.group(3)] = disp.get(m.group(3), 0.0) + float(m.group(1))
        m = re.search(r"vk:\s+([\d.]+) ms a frame in all", ln)
        if m and not total:
            total = float(m.group(1))
    return draws, disp, total


def load_costs(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "**", "game_log.txt"), recursive=True))
        else:
            files.append(p)
    draws, disp = collections.defaultdict(list), collections.defaultdict(list)
    totals = []
    for f in files:
        d, s, t = read_log(f)
        if not d and not s:
            continue
        totals.append(t)
        for k, v in d.items():
            draws[k].append(v)
        for k, v in s.items():
            disp[k].append(v)
    n = max(len(totals), 1)
    return ({k: sum(v) / n for k, v in draws.items()}, {k: sum(v) / n for k, v in disp.items()}, sum(totals) / n, len(totals))


def read_stages(d):
    out = []
    seen = set()
    for name in sorted(glob.glob(os.path.join(d, "stages*.jsonl"))):   # (a second scan of other keys may sit beside the first)
        with open(name) as f:
            for ln in f:
                r = json.loads(ln)
                if "error" in r:
                    continue
                ident = (r["key"], r["stage"], r["instr"], r["words"])
                if ident not in seen:   # (the same stage twice: a record kept for two pipeline states)
                    seen.add(ident)
                    out.append(r)
    return out


def fmt(n):
    return "{:,}".format(int(n))


def cmd_report(a):
    st = read_stages(a.dir)
    by_stage = collections.defaultdict(list)
    for r in st:
        by_stage[r["stage"]].append(r)
    keys = {r["key"] for r in st}
    print("programs %s, stages %s, instructions %s" % (fmt(len(keys)), fmt(len(st)), fmt(sum(r["instr"] for r in st))))
    cols = ["instr", "gm_call_sites", "gm_effective_instr", "bfue", "ieq", "const_foldable", "loops", "cas", "local_arrays",
            "local_array_bytes", "f16_ops", "f32_ops", "i64_ops", "image_sample", "narrow_store_calls"]
    print("\nper stage kind (sum): stages, " + ", ".join(cols))
    for s, rows in sorted(by_stage.items()):
        print("  %-10s %7s  " % (s, fmt(len(rows))) + "  ".join("%s=%s" % (c, fmt(sum(r[c] for r in rows))) for c in cols))
    print("\nclass totals (instructions):")
    cls = collections.Counter()
    for r in st:
        cls.update(r["classes"])
    tot = sum(cls.values())
    for c, k in cls.most_common(24):
        print("  %-18s %12s  %5.1f%%" % (c, fmt(k), 100.0 * k / tot))
    # stages with global-memory helpers
    with_gm = [r for r in st if r["gm_call_sites"]]
    print("\nstages with global-memory helper calls: %d of %d; their instr %s of %s; effective (body x calls) %s" % (
        len(with_gm), len(st), fmt(sum(r["instr"] for r in with_gm)), fmt(sum(r["instr"] for r in st)),
        fmt(sum(r["gm_effective_instr"] for r in with_gm))))
    for s, rows in sorted(by_stage.items()):
        g = [r for r in rows if r["gm_call_sites"]]
        if g:
            print("  %-10s %5d stages: call sites %s, helper bodies %s instr, effective %s instr (%.0f%% of those stages' code with the helpers inlined)" % (
                s, len(g), fmt(sum(r["gm_call_sites"] for r in g)), fmt(sum(r["gm_body_instr"] for r in g)),
                fmt(sum(r["gm_effective_instr"] for r in g)),
                100.0 * sum(r["gm_effective_instr"] for r in g) / max(1, sum(r["instr"] - r["gm_body_instr"] + r["gm_effective_instr"] for r in g))))
    cand = collections.Counter()
    for r in with_gm:
        cand[r["gm_max_candidates"]] += 1
    print("  candidates in the widest helper (stages): " + ", ".join("%d:%d" % kv for kv in sorted(cand.items())[:30]))
    print("\ntop %d stages by instruction count:" % a.top)
    for r in sorted(st, key=lambda r: -r["instr"])[:a.top]:
        print("  %s %-8s instr %8s gm calls %4d helper body %6s bfue %5d ieq %5d fold %5d cas %d loops %d" % (
            r["key"], r["stage"], fmt(r["instr"]), r["gm_call_sites"], fmt(r["gm_body_instr"]), r["bfue"], r["ieq"],
            r["const_foldable"], r["cas"], r["loops"]))
    if a.logs:
        draws, disp, total, nlogs = load_costs(a.logs)
        print("\nGPU cost per program from %d logs (frame busy %.2f ms): draws %d programs, dispatches %d programs" % (
            nlogs, total, len(draws), len(disp)))
        by_key = collections.defaultdict(list)
        variants = collections.Counter()
        for r in st:   # (a key may have several records, one a pipeline state it was built for: the largest of each stage is shown)
            variants[(r["key"], r["stage"])] += 1
            rows = by_key[r["key"]]
            same = [x for x in rows if x["stage"] == r["stage"]]
            if not same:
                rows.append(r)
            elif r["instr"] > same[0]["instr"]:
                rows[rows.index(same[0])] = r
        cost = collections.Counter()
        for k, v in draws.items():
            cost[k] += v
        for k, v in disp.items():
            cost[k] += v
        print("  ms/frame  draw+disp  stages (instr, gm call sites, helper body, bfue, ieq, foldable, cas, loops, local arr B)")
        covered = 0.0
        for k, ms in cost.most_common(a.top):
            covered += ms
            rows = by_key.get(k, [])
            line = "; ".join("%s %s i, gm %d x%d(eff %s), bfue %d, ieq %d, fold %d, cas %d, loop %d, la %dB, f16 %d, tex %d" % (
                r["stage"][:4], fmt(r["instr"]), r["gm_call_sites"], r["gm_body_instr"], fmt(r["gm_effective_instr"]), r["bfue"],
                r["ieq"], r["const_foldable"], r["cas"], r["loops"], r["local_array_bytes"], r["f16_ops"], r["image_total"])
                for r in rows) or "(not in the cache)"
            print("  %6.3f  %5.3f+%5.3f  %s  %s" % (ms, draws.get(k, 0), disp.get(k, 0), k, line))
        print("  top %d programs cover %.2f ms; all listed %.2f ms" % (a.top, covered, sum(cost.values())))
        # what share of the measured programs' ms carries each inefficiency
        listed = [(k, ms) for k, ms in cost.items() if k in by_key]
        def share(pred):
            return sum(ms for k, ms in listed if any(pred(r) for r in by_key[k]))
        print("  of the listed %.2f ms: with global helpers %.2f, with CAS %.2f, with local arrays %.2f, with loops %.2f, "
              "with >=100 foldable constant ops %.2f, with f16 ops %.2f" % (
                  sum(ms for _, ms in listed), share(lambda r: r["gm_call_sites"] > 0), share(lambda r: r["cas"] > 0),
                  share(lambda r: r["local_arrays"] > 0), share(lambda r: r["loops"] > 0),
                  share(lambda r: r["const_foldable"] >= 100), share(lambda r: r["f16_ops"] > 0)))
        # ms by stage kind of the program's heaviest stage
        stage_ms = collections.Counter()
        for k, ms in listed:
            for r in by_key[k]:
                stage_ms[r["stage"]] += ms * (1.0 if len(by_key[k]) == 1 else r["instr"] / sum(x["instr"] for x in by_key[k]))
        print("  ms spread over stage kinds by instruction share: " + ", ".join("%s %.2f" % kv for kv in stage_ms.most_common()))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("cache")
    s.add_argument("--out", required=True)
    s.add_argument("--key", action="append", default=[])
    s.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    o = sub.add_parser("ops")
    o.add_argument("cache")
    o.add_argument("--key", action="append", required=True)
    o.add_argument("--top", type=int, default=30)
    r = sub.add_parser("report")
    r.add_argument("dir")
    r.add_argument("--logs", nargs="*", default=[])
    r.add_argument("--top", type=int, default=25)
    a = p.parse_args(argv)
    return {"scan": cmd_scan, "ops": cmd_ops, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
