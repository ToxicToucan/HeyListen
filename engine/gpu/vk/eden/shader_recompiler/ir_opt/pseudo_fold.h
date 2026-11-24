// (hookshot, stream P5) Folding an instruction of immediates that has pseudo-operations (flags) attached. Pure: tests/test_pseudo_fold.py.
//
// R2P (frontend/maxwell/translate/impl/move_register_to_predicate.cpp) gives every BitFieldExtract of a constant mask a GetZeroFromOp,
// and the constant propagation pass never folded an instruction that had an associated pseudo-operation: 1.89 M constant-foldable
// operations of the program cache stayed (8.7 k of the 44.9 k instructions of the biggest mesh program), with the IEqual / Select /
// LogicalNot chains that hang on them. The fold here gives the flag pseudo-operations (zero, sign) their value, as the SPIR-V back end
// would have made it from the folded result (emit_spirv_integer.cpp: zero = result == 0, sign = result < 0 as signed), and then the
// instruction folds as any other. RX_VK_FOLD_PSEUDO (off by default in the fork, =1: on); a name the program cache's stamp covers
// (vk_shader.cpp).
// (fork, 2026-11-24) Off by default: with it on, NVIDIA 621+ drivers (630.20 here) draw some leaves pitch black, whole fronds and
// tree leaves among lit ones (HeyListen issue #97). At the owner's Kakariko spot the bush in front of Link was 44-46% near-black
// with it on and 25-31% with RX_VK_FOLD_PSEUDO=0 alone (RX_VK_MESH_G3=0: 52-57%, RX_VK_MESH_NOINLINE_ABOVE=0: 38-45%, no help).
#pragma once

#include <cstdint>
#include <cstdlib>

namespace Shader::Optimization {

/// The switch's text: on for a number other than 0; unset, empty or anything else: off
inline bool FoldPseudoEnabled(const char* text) {   // (fork: off by default; RX_VK_FOLD_PSEUDO=1: on)
    if (!text || !*text) {
        return false;
    }
    char* end{};
    const long value{std::strtol(text, &end, 10)};
    return end != text && *end == '\0' && value != 0;
}

/// The zero flag of a 32-bit result (what the back end's OpIEqual(result, 0) says)
constexpr bool ZeroFlagOf(std::uint32_t result) {
    return result == 0;
}

/// The sign flag of a 32-bit result (what the back end's OpSLessThan(result, 0) says)
constexpr bool SignFlagOf(std::uint32_t result) {
    return static_cast<std::int32_t>(result) < 0;
}

} // namespace Shader::Optimization
