// (hookshot, stream P5) Folding an instruction of immediates that has pseudo-operations (flags) attached. Pure: tests/test_pseudo_fold.py.
//
// R2P (frontend/maxwell/translate/impl/move_register_to_predicate.cpp) gives every BitFieldExtract of a constant mask a GetZeroFromOp,
// and the constant propagation pass never folded an instruction that had an associated pseudo-operation: 1.89 M constant-foldable
// operations of the program cache stayed (8.7 k of the 44.9 k instructions of the biggest mesh program), with the IEqual / Select /
// LogicalNot chains that hang on them. The fold here gives the flag pseudo-operations (zero, sign) their value, as the SPIR-V back end
// would have made it from the folded result (emit_spirv_integer.cpp: zero = result == 0, sign = result < 0 as signed), and then the
// instruction folds as any other. RX_VK_FOLD_PSEUDO (on by default, =0: off); a name the program cache's stamp covers (vk_shader.cpp).
#pragma once

#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace Shader::Optimization {

/// The switch's text: on for a number other than 0; unset, empty or anything else: off
inline bool FoldPseudoEnabled(const char* text) {   // (on by default; RX_VK_FOLD_PSEUDO=0: off)
    if (!text || !*text) {
        return true;
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

/// Which flagged instructions of which program the fold may take (a developer's bisection of a picture the fold changed: the
/// three names are under the program cache's stamp, RX_VK_FOLD prefix). Programs are named by the key of the stage's code, in hex,
/// a prefix is enough; the instructions of one program by their number among the flagged all-immediate ones in translation order.
///   RX_VK_FOLD_PSEUDO_ONLY=<key>[,<key>...]   only those programs fold
///   RX_VK_FOLD_PSEUDO_SKIP=<key>[,<key>...]   those programs do not fold
///   RX_VK_FOLD_PSEUDO_RANGE=<first>-<last>    only the instructions first <= n < last of a program fold (either side may be empty)
class FoldSelect {
public:
    FoldSelect() = default;
    FoldSelect(const char* only, const char* skip, const char* range) {
        SplitKeys(only, only_);
        SplitKeys(skip, skip_);
        if (range) {
            const char* q{range};
            if (std::isdigit(static_cast<unsigned char>(*q))) {
                first_ = static_cast<std::uint32_t>(std::strtoul(q, const_cast<char**>(&q), 10));
            }
            if (*q == '-' && std::isdigit(static_cast<unsigned char>(q[1]))) {
                last_ = static_cast<std::uint32_t>(std::strtoul(q + 1, nullptr, 10));
            }
        }
    }

    /// Whether instruction number index of the program whose code key is key may fold
    bool Allows(std::uint64_t key, std::uint32_t index) const {
        if (index < first_ || index >= last_) {
            return false;
        }
        if (!only_.empty() && !Listed(only_, key)) {
            return false;
        }
        return skip_.empty() || !Listed(skip_, key);
    }

private:
    static void SplitKeys(const char* text, std::vector<std::string>& out) {
        for (const char* q = text ? text : ""; *q;) {
            const char* e{q};
            while (*e && *e != ',') {
                ++e;
            }
            if (e > q) {
                out.emplace_back(q, static_cast<std::size_t>(e - q));
            }
            q = *e ? e + 1 : e;
        }
    }

    static bool Listed(const std::vector<std::string>& list, std::uint64_t key) {
        char hex[24];
        std::snprintf(hex, sizeof hex, "%016llx", static_cast<unsigned long long>(key));
        for (const std::string& prefix : list) {
            if (std::strncmp(hex, prefix.c_str(), prefix.size()) == 0) {
                return true;
            }
        }
        return false;
    }

    std::vector<std::string> only_;
    std::vector<std::string> skip_;
    std::uint32_t first_{0};
    std::uint32_t last_{UINT32_MAX};
};

/// The program being translated on this thread (set by the translator before each stage, vk_shader.cpp translate()): the key of
/// the stage's code, the flagged instructions seen so far, and how many of them folded
inline thread_local std::uint64_t g_fold_code_key{0};
inline thread_local std::uint32_t g_fold_seen{0};
inline thread_local std::uint32_t g_fold_taken{0};

} // namespace Shader::Optimization
