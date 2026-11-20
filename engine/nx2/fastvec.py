"""Vector code the translator writes slowly, rewritten on the lifted text (HL_FASTVEC=1, off by default): TBL with a
one-register table and EXT (vbytes), 128-bit vector loads and DUP by element (vmoves), as one SSE instruction each.

Ported from Longshot deb08f36f, "perf: less game work a frame in Castle Town (registers as locals, vector rewrites)"
(MagicBeanDev/Longshot #1576, by Veix, with HumanBean's follow-ups), GPL-3.0-or-later. Its helpers (a64_tbl1, A64_EXT16,
A64_EXT8, a64_vld16, a64_vdup) are Longshot's; what changed for HeyListen:

  - the passes run on an instruction's final text (after the kit's widen_vstores, which HeyListen's op_text runs: the
    stores are a64_vset64 / a64_vst16 / a64_vset_lo there), not inside vectorize_fpx: one function, fastvec_text, that
    takes both forms (A64_NO_VSET=1 leaves the plain stores);
  - the hot functions (A64_HOT_FUNCS) are written with the kit's text, and fastvec_text runs after hot_text on them:
    HeyListen's own hot passes (A64_FPX_BLOCK's blocks take EXT, DUP and the Q loads into a block; A64_FPX_XMM's Q loads)
    go first and these take what they leave;
  - FMUL by element (Longshot's VFPX_MUL_ELEM, its a64_vfpx_mul32_elem) is not ported: HeyListen's kit has its own,
    A64_VFPX_ELEM (TICK-041, on by default: a64_vfpx_mul32e / a64_vfpx_fma32e), which matches the same text first.
    A64_VFPX_ELEM=0 is its switch here; Longshot's A64_NO_VFPX_ELEM is not read.

Switches, read at lift time (nx2/make_game.py's lift key holds HL_FASTVEC and every A64_*): HL_FASTVEC=1 turns the passes on
(nx2/lift.py calls install() only then: a lift without it is the lift as before, byte for byte); A64_NO_VBYTES=1 and
A64_NO_VMOVES=1 then keep the kit's text for that pass. The helpers use SSSE3 (PSHUFB, PALIGNR) where the build's -march has
it, else a plain C fallback (RX_MARCH=x86-64). Tests: tests/test_vbytes.py (game-free, bit-exact against the kit's text).
"""
import os
import re

import a64_lift


def enabled():
    """HL_FASTVEC=1: the vector rewrites for this lift (off by default in this fork)"""
    return os.environ.get("HL_FASTVEC", "") == "1"


# the instruction's store of its result: the translator's (c->vreg[d][0]=0; c->vreg[d][1]=0; memcpy(c->vreg[d],_r,n);), or
# after widen_vstores a64_vset64 + a64_vst16 (16 bytes) or a64_vset_lo (8 bytes). (d, n) are checked by the callers
_TAIL = (r"(?:c->vreg\[(?P<d0>\d+)\]\[0\]=0; c->vreg\[(?P=d0)\]\[1\]=0; memcpy\(c->vreg\[(?P=d0)\],_r,(?P<n0>8|16)\);"
         r"|a64_vset64\(c->vreg\[(?P<d1>\d+)\],0\); a64_vst16\(c->vreg\[(?P=d1)\],_r\);"
         r"|a64_vset_lo\(c->vreg\[(?P<d2>\d+)\],_r,8\);)")


def _dest(m, n):
    """the destination register of the tail when it stores n bytes, else None"""
    if m.group("d0") is not None:
        return m.group("d0") if int(m.group("n0")) == n else None
    if m.group("d1") is not None:
        return m.group("d1") if n == 16 else None
    return m.group("d2") if n == 8 else None


# (vbytes) TBL with a one-register table and EXT, which the emitter writes as byte loops (8 or 16 single steps through
# the stack, then a 16-byte reload that cannot be forwarded): one SSSE3 instruction each on x86 (PSHUFB; PALIGNR or two
# shifts). TBL: an index of 16 or more gives 0, which PSHUFB does when the index byte's top bit is set: the index plus
# 0x70, saturated, keeps 0-15 below 0x80 with the same low nibble and lifts 16-255 to 0x80 or more. Both sources are
# read before Vd is written, so Vd may be Vn or Vm. (Longshot: TBL 2,995 sites in the game, EXT 29,451; the loops took
# ~6 % of game-code time in the market.) A64_NO_VBYTES=1 keeps them as they were.
VBYTE_TBL = re.compile(
    r"\{ uint8_t _t\[16\],_x\[(?P<n>8|16)\],_r\[(?P=n)\]; memcpy\(_t\+0,c->vreg\[(?P<t>\d+)\],16\); "
    r"memcpy\(_x,c->vreg\[(?P<x>\d+)\],(?P=n)\); "
    r"for\(int _i=0;_i<(?P=n);_i\+\+\)\{ unsigned _k=_x\[_i\]; if\(_k<16U\) _r\[_i\]=_t\[_k\]; else _r\[_i\]=0; \} "
    + _TAIL + r" \}")
VBYTE_EXT = re.compile(
    r"\{ uint8_t _a\[(?P<n>8|16)\],_b\[(?P=n)\],_r\[(?P=n)\]; memcpy\(_a,c->vreg\[(?P<a>\d+)\],(?P=n)\); "
    r"memcpy\(_b,c->vreg\[(?P<b>\d+)\],(?P=n)\); "
    r"for\(int _i=0;_i<(?P=n);_i\+\+\)\{ int _k=_i\+(?P<imm>\d+); _r\[_i\] = \(_k<(?P=n)\) \? _a\[_k\] : _b\[_k-(?P=n)\]; \} "
    + _TAIL + r" \}")


def _tbl(m):
    n = int(m.group("n"))
    d = _dest(m, n)
    if d is None:
        return m.group(0)
    return "a64_tbl1(c->vreg[%s],c->vreg[%s],c->vreg[%s],%d);" % (d, m.group("t"), m.group("x"), n == 16)


def _ext(m):
    n = int(m.group("n"))
    d = _dest(m, n)
    if d is None or int(m.group("imm")) >= n:
        return m.group(0)
    return "A64_EXT%d(c->vreg[%s],c->vreg[%s],c->vreg[%s],%s);" % (n, d, m.group("a"), m.group("b"), m.group("imm"))


def vectorize_bytes(text):
    if "uint8_t _" not in text or os.environ.get("A64_NO_VBYTES"):
        return text
    return VBYTE_EXT.sub(_ext, VBYTE_TBL.sub(_tbl, text))


VBYTE_HEADER = r"""
/* ---- TBL (one table register) and EXT in one SSSE3 instruction (vectorize_bytes in nx2/fastvec.py, Longshot's) ---- */
#if defined(__SSSE3__)
#include <tmmintrin.h>
static inline __attribute__((always_inline)) void a64_tbl1(uint64_t *d, const uint64_t *t, const uint64_t *x, int q) {
    const __m128i tv = _mm_loadu_si128((const __m128i *)t);
    const __m128i xv = q ? _mm_loadu_si128((const __m128i *)x) : _mm_loadl_epi64((const __m128i *)x);
    __m128i r = _mm_shuffle_epi8(tv, _mm_adds_epu8(xv, _mm_set1_epi8(0x70)));
    if (!q) r = _mm_move_epi64(r);
    A64_OPAQUE(r);
    _mm_storeu_si128((__m128i *)d, r);
}
/* EXT 16 bytes: Vn's bytes from imm up, then Vm's (PALIGNR takes the high source first) */
#define A64_EXT16(d, n, m, imm) do { \
    const __m128i a64_e_n_ = _mm_loadu_si128((const __m128i *)(n)), a64_e_m_ = _mm_loadu_si128((const __m128i *)(m)); \
    __m128i a64_e_r_ = _mm_alignr_epi8(a64_e_m_, a64_e_n_, imm); \
    A64_OPAQUE(a64_e_r_); \
    _mm_storeu_si128((__m128i *)(d), a64_e_r_); } while (0)
#else
static inline void a64_tbl1(uint64_t *d, const uint64_t *t, const uint64_t *x, int q) {
    uint8_t tb[16], xb[16], r[16] = {0};
    memcpy(tb, t, 16); memcpy(xb, x, 16);
    for (int i = 0; i < (q ? 16 : 8); i++) r[i] = xb[i] < 16 ? tb[xb[i]] : 0;
    memcpy(d, r, 16);
}
#define A64_EXT16(d, n, m, imm) do { uint8_t a64_e_a_[32], a64_e_r_[16]; memcpy(a64_e_a_, (n), 16); \
    memcpy(a64_e_a_ + 16, (m), 16); memcpy(a64_e_r_, a64_e_a_ + (imm), 16); memcpy((d), a64_e_r_, 16); } while (0)
#endif
/* EXT 8 bytes: the low halves, Vn's from byte imm up then Vm's; the top half zeroed */
#define A64_EXT8(d, n, m, imm) do { uint64_t a64_e_n_ = (n)[0], a64_e_m_ = (m)[0]; \
    const uint64_t a64_e_r_ = (imm) ? (a64_e_n_ >> (8 * (imm))) | (a64_e_m_ << ((64 - 8 * (imm)) & 63)) : a64_e_n_; \
    a64_vset64((d), a64_e_r_); } while (0)
"""


# (vmoves) two moves the compiler leaves slow, on the final text (after widen_vstores):
# - a 128-bit vector load (LDR Q, LDP Q, post-index LDR Q) as recomp_ldp64 into the register's two halves: two 8-byte
#   stores, and the next 16-byte read of the register cannot be forwarded from them (a stall of ~10 cycles). Now one
#   16-byte load and one 16-byte store (a64_vld16; guest memory is read the same way, recomp_load64 is a plain copy).
# - DUP Vd, Vn.T[i] as a loop through the stack and a 16-byte reload: one broadcast (a64_vdup). The element is read
#   before Vd is written, so Vd may be Vn. (Longshot: 139,843 and 26,529 sites.) A64_NO_VMOVES=1 keeps them as before.
VMOVE_LDQ = re.compile(r"recomp_ldp64\(c,([^;]*?),&c->vreg\[(\d+)\]\[0\],&c->vreg\[\2\]\[1\]\);")
VMOVE_DUP = re.compile(
    r"\{ uint8_t _s\[16\]; memcpy\(_s,c->vreg\[(\d+)\],16\); uint64_t _e=0; memcpy\(&_e,_s\+(\d+),(1|2|4|8)\); "
    r"uint8_t _d\[16\]; memset\(_d,0,16\); for\(int _i=0;_i<(\d+);_i\+\+\) memcpy\(_d\+_i\*\3,&_e,\3\); "
    r"(?:a64_vst16\(c->vreg\[(\d+)\],_d\);|memcpy\(c->vreg\[(\d+)\],_d,16\);) \}")


def _dup(m):
    s, off, w, n, d1, d2 = m.groups()
    if int(w) * int(n) not in (8, 16):
        return m.group(0)
    return "a64_vdup(c->vreg[%s],c->vreg[%s],%s,%s,%d);" % (d1 or d2, s, off, w, int(w) * int(n) == 16)


def vectorize_moves(text):
    if "c->vreg" not in text or os.environ.get("A64_NO_VMOVES"):
        return text
    text = VMOVE_LDQ.sub(lambda m: "a64_vld16(c->vreg[%s],%s);" % (m.group(2), m.group(1)), text)
    return VMOVE_DUP.sub(_dup, text)


VMOVE_HEADER = r"""
/* ---- whole-register vector load and DUP by element (vectorize_moves in nx2/fastvec.py, Longshot's) ---- */
#if RECOMP_FPX_HOST == 2
static inline __attribute__((always_inline)) void a64_vld16(uint64_t *d, uint64_t a) {
    __m128i x = _mm_loadu_si128((const __m128i *)A64_PTR(a));
    A64_OPAQUE(x);
    _mm_storeu_si128((__m128i *)d, x);
}
static inline __attribute__((always_inline)) void a64_vdup(uint64_t *d, const uint64_t *s, int off, int w, int q) {
    uint64_t e = 0;
    memcpy(&e, (const uint8_t *)s + off, w);
    __m128i r = w == 1 ? _mm_set1_epi8((char)e) : w == 2 ? _mm_set1_epi16((short)e) : w == 4 ? _mm_set1_epi32((int)e)
                                                                                           : _mm_set1_epi64x((long long)e);
    if (!q) r = _mm_move_epi64(r);
    A64_OPAQUE(r);
    _mm_storeu_si128((__m128i *)d, r);
}
#else
static inline void a64_vld16(uint64_t *d, uint64_t a) { memcpy(d, A64_PTR(a), 16); }
static inline void a64_vdup(uint64_t *d, const uint64_t *s, int off, int w, int q) {
    uint8_t r[16] = {0};
    uint64_t e = 0;
    memcpy(&e, (const uint8_t *)s + off, w);
    for (int i = 0; i < (q ? 16 : 8) / w; i++) memcpy(r + i * w, &e, w);
    memcpy(d, r, 16);
}
#endif
"""


def fastvec_text(text):
    """an instruction's (or a whole function's) lifted C with the rewrites above"""
    return vectorize_moves(vectorize_bytes(text))


class _Vec(str):
    """an instruction's C with the rewrites; .kit: the kit's text it was made from (what a hot function is written with)"""
    kit = None


class _Kit:
    """a hot function's view of the instructions' texts: the kit's own (emit_function reads texts[key] only)"""

    def __init__(self, texts):
        self.texts = texts

    def __getitem__(self, key):
        text, flags = self.texts[key]
        return (getattr(text, "kit", None) or text), flags


_installed = False


def install():
    """the wrappers around the kit's op_text, hot_text, fpx_header and Lifter.emit_function. Once; before nx2/guest_locals.py's
    install_plain (whose emit_function goes around this one, so that its context-flag texts come here and are taken back
    to the kit's for a hot function)"""
    global _installed
    if _installed:
        return
    _installed = True
    base_op_text, base_hot, base_header = a64_lift.op_text, a64_lift.hot_text, a64_lift.fpx_header
    base_emit = a64_lift.Lifter.emit_function
    hot = a64_lift.hot_functions()

    def op_text(text, fpx):
        r = base_op_text(text, fpx)
        v = fastvec_text(r)
        if v == r:
            return r
        v = _Vec(v)
        v.kit = r
        return v

    def hot_text(text):
        return fastvec_text(base_hot(text))

    def fpx_header(used):
        text = base_header(used)
        at = a64_lift.VSET_HEADER + "\n"
        if text.count(at) != 1:
            raise SystemExit("fastvec: a64_fpx.h no longer holds the kit's VSET_HEADER once: where its helpers go")
        return text.replace(at, at + VBYTE_HEADER + "\n" + VMOVE_HEADER + "\n")

    def emit_function(self, e, texts):
        return base_emit(self, e, _Kit(texts) if hot and self.fname(e) in hot else texts)

    a64_lift.op_text, a64_lift.hot_text, a64_lift.fpx_header = op_text, hot_text, fpx_header
    a64_lift.Lifter.emit_function = emit_function
