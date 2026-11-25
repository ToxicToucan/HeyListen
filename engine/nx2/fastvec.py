"""(longshot #1576, Veix; ported by hand into HeyListen) Vector code the translator writes slowly, rewritten on a function's
lifted text: TBL with a one-register table and EXT as one SSSE3 instruction each (PSHUFB, PALIGNR), 128-bit vector loads (LDR Q,
LDP Q, LD1 one register) as one 16-byte load, and DUP by element as one broadcast. A64_VBYTES=0 and A64_VMOVES=0 turn the two
passes off (lift time, so in the lift key). The kit's files stay as they are (third_party/ is not edited: CONTRIBUTING.md);
install() wraps a64_lift.fpx_header (the helpers go after the kit's VSET_HEADER) and Lifter.emit_function.

What differs from Longshot's: its passes run on each instruction's text (vectorize_fpx and op_text), where the TBL / EXT loops
still have their raw form. Here they run on a whole function's text after emit_function, in the form op_text leaves them
(a64_vset64 + a64_vst16 for a 16-byte destination, a64_vset_lo for 8 bytes), and not in the hot functions (A64_HOT_FUNCS) nor
in the functions the text edits' hashes were recorded against (nx2/guest_locals.py: PLAIN0): the hot functions are lifted as FP
blocks (a64_block.py, TICK-063) by the shapes of that text, and their PGO profile was recorded on their control flow, which a
removed byte loop changes. Longshot's FMUL by element (VFPX_MUL_ELEM, a64_vfpx_mul32_elem) is not ported: the kit already has
it (TICK-041, a64_vfpx_mul32e). Tests: tests/test_vbytes.py (game-free, from Longshot's, adapted)."""
import os
import re

import a64_lift


def pass_on(name):
    """a pass is on unless A64_<name>=0 (read at lift time: the lift key holds every A64_ variable)"""
    return os.environ.get("A64_" + name, "1").strip().lower() not in ("0", "off", "false", "no")


# (vbytes) TBL with a one-register table and EXT, which the emitter writes as byte loops (8 or 16 single steps through
# the stack, then a 16-byte reload that cannot be forwarded): one SSSE3 instruction each on x86 (PSHUFB; PALIGNR or two
# shifts). TBL: an index of 16 or more gives 0, which PSHUFB does when the index byte's top bit is set: the index plus
# 0x70, saturated, keeps 0-15 below 0x80 with the same low nibble and lifts 16-255 to 0x80 or more. Both sources are
# read before Vd is written, so Vd may be Vn or Vm. (Longshot's count in the market's hot game code: the loops took ~6 %
# of game-code time, TBL 2,995 sites, EXT 29,451.)
VBYTE_TBL = re.compile(
    r"\{ uint8_t _t\[16\],_x\[(8|16)\],_r\[\1\]; memcpy\(_t\+0,c->vreg\[(\d+)\],16\); memcpy\(_x,c->vreg\[(\d+)\],\1\); "
    r"for\(int _i=0;_i<\1;_i\+\+\)\{ unsigned _k=_x\[_i\]; if\(_k<16U\) _r\[_i\]=_t\[_k\]; else _r\[_i\]=0; \} "
    r"(?:a64_vset64\(c->vreg\[(\d+)\],0\); a64_vst16\(c->vreg\[\4\],_r\)|a64_vset_lo\(c->vreg\[(\d+)\],_r,8\)); \}")
VBYTE_EXT = re.compile(
    r"\{ uint8_t _a\[(8|16)\],_b\[\1\],_r\[\1\]; memcpy\(_a,c->vreg\[(\d+)\],\1\); memcpy\(_b,c->vreg\[(\d+)\],\1\); "
    r"for\(int _i=0;_i<\1;_i\+\+\)\{ int _k=_i\+(\d+); _r\[_i\] = \(_k<\1\) \? _a\[_k\] : _b\[_k-\1\]; \} "
    r"(?:a64_vset64\(c->vreg\[(\d+)\],0\); a64_vst16\(c->vreg\[\5\],_r\)|a64_vset_lo\(c->vreg\[(\d+)\],_r,8\)); \}")


def vectorize_bytes(text):
    if "uint8_t _" not in text or not pass_on("VBYTES"):
        return text

    def tbl(m):
        n, d = m.group(1), m.group(4) or m.group(5)
        if (n == "16") != (m.group(4) is not None):   # (16 bytes are written whole, 8 by a64_vset_lo)
            return m.group(0)
        return "a64_tbl1(c->vreg[%s],c->vreg[%s],c->vreg[%s],%d);" % (d, m.group(2), m.group(3), n == "16")

    def ext(m):
        n, d = m.group(1), m.group(5) or m.group(6)
        if (n == "16") != (m.group(5) is not None):
            return m.group(0)
        return "A64_EXT%s(c->vreg[%s],c->vreg[%s],c->vreg[%s],%s);" % (n, d, m.group(2), m.group(3), m.group(4))
    return VBYTE_EXT.sub(ext, VBYTE_TBL.sub(tbl, text))


VBYTE_HEADER = r"""
/* ---- TBL (one table register) and EXT in one SSSE3 instruction (vectorize_bytes in nx2/fastvec.py) ---- */
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


# (vmoves) two moves the compiler leaves slow:
# - a 128-bit vector load (LDR Q, LDP Q, LD1 one register) as recomp_ldp64 into the register's two halves: two 8-byte
#   stores, and the next 16-byte read of the register cannot be forwarded from them (a stall of ~10 cycles). Now one
#   16-byte load and one 16-byte store (a64_vld16; guest memory is read the same way, recomp_load64 is a plain copy).
# - DUP Vd, Vn.T[i] as a loop through the stack and a 16-byte reload: one broadcast (a64_vdup). The element is read
#   before Vd is written, so Vd may be Vn.
VMOVE_LDQ = re.compile(r"recomp_ldp64\(c,([^;]*?),&c->vreg\[(\d+)\]\[0\],&c->vreg\[\2\]\[1\]\);")
VMOVE_DUP = re.compile(
    r"\{ uint8_t _s\[16\]; memcpy\(_s,c->vreg\[(\d+)\],16\); uint64_t _e=0; memcpy\(&_e,_s\+(\d+),(1|2|4|8)\); "
    r"uint8_t _d\[16\]; memset\(_d,0,16\); for\(int _i=0;_i<(\d+);_i\+\+\) memcpy\(_d\+_i\*\3,&_e,\3\); "
    r"a64_vst16\(c->vreg\[(\d+)\],_d\); \}")


def vectorize_moves(text):
    if "c->vreg" not in text or not pass_on("VMOVES"):
        return text
    text = VMOVE_LDQ.sub(lambda m: "a64_vld16(c->vreg[%s],%s);" % (m.group(2), m.group(1)), text)

    def dup(m):
        s, off, w, n, d = m.groups()
        if int(w) * int(n) not in (8, 16):
            return m.group(0)
        return "a64_vdup(c->vreg[%s],c->vreg[%s],%s,%s,%d);" % (d, s, off, w, int(w) * int(n) == 16)
    return VMOVE_DUP.sub(dup, text)


VMOVE_HEADER = r"""
/* ---- whole-register vector load and DUP by element (vectorize_moves in nx2/fastvec.py) ---- */
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


def rewrite(text):
    """a function's text with both passes (the tests ask it of one instruction's op_text)"""
    return vectorize_moves(vectorize_bytes(text))


PLAIN0 = set()   # (the entries of functions written with the lift's passes off: nx2/guest_locals.py fills it; Lifter.plain0 is this set)


_installed = False


def install():
    """the passes around the kit's Lifter.emit_function, and the helpers in fpx_header. Once; nothing with both off"""
    global _installed
    if _installed:
        return
    _installed = True
    if not (pass_on("VBYTES") or pass_on("VMOVES")):
        return
    base_header, base_emit = a64_lift.fpx_header, a64_lift.Lifter.emit_function

    def fpx_header(used):
        text = base_header(used)
        at = a64_lift.VSET_HEADER + "\n"
        if text.count(at) != 1:
            raise SystemExit("fastvec: a64_fpx.h no longer holds the kit's VSET_HEADER once: where its helpers go")
        return text.replace(at, at + VBYTE_HEADER + "\n" + VMOVE_HEADER + "\n")

    hot = []   # (the kit's hot list, read once: the lift's emit processes each read it at their first function)

    def emit_function(self, e, texts):
        text, refs = base_emit(self, e, texts)
        if not hot:
            hot.append(a64_lift.hot_functions())
        return (text if self.fname(e) in hot[0] or e in PLAIN0 else rewrite(text)), refs

    a64_lift.fpx_header, a64_lift.Lifter.emit_function = fpx_header, emit_function
