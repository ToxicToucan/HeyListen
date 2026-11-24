// Copyright (C) 2026 Sacrappy

// The pseudo-operation fold (RX_VK_FOLD_PSEUDO) against the semantics the SPIR-V back end gives the unfolded instruction:
// BitFieldUExtract/SExtract (SPIR-V: defined for offset + count <= 32, count 0 gives 0, count 32 gives the base), and, or, xor;
// the zero flag is result == 0, the sign flag result < 0 as signed. Runs the real ConstantPropagationPass.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <vector>

#include "shader_recompiler/environment.h"
#include "shader_recompiler/exception.h"
#include "shader_recompiler/frontend/ir/basic_block.h"
#include "shader_recompiler/frontend/ir/ir_emitter.h"
#include "shader_recompiler/frontend/ir/program.h"
#include "shader_recompiler/ir_opt/passes.h"

using namespace Shader;

namespace {
class Env final : public Environment {
public:
    u64 ReadInstruction(u32) override { return 0; }
    u32 ReadCbufValue(u32, u32) override { return 0; }
    TextureType ReadTextureType(u32) override { return {}; }
    TexturePixelFormat ReadTexturePixelFormat(u32) override { return {}; }
    bool IsTexturePixelFormatInteger(u32) override { return false; }
    u32 ReadViewportTransformState() override { return 0; }
    u32 TextureBoundBuffer() const override { return 0; }
    u32 LocalMemorySize() const override { return 0; }
    u32 SharedMemorySize() const override { return 0; }
    std::array<u32, 3> WorkgroupSize() const override { return {1, 1, 1}; }
    bool HasHLEMacroState() const override { return false; }
    std::optional<ReplaceConstant> GetReplaceConstBuffer(u32, u32) override { return std::nullopt; }
    void Dump(u64, u64) override {}
};

enum Kind { UExt, SExt, And, Or, Xor };
struct Case { Kind kind; u32 a, b, c; };

// SPIR-V semantics (defined range only): returns false when the case is undefined in SPIR-V
bool Reference(const Case& k, u32& out) {
    switch (k.kind) {
    case And: out = k.a & k.b; return true;
    case Or: out = k.a | k.b; return true;
    case Xor: out = k.a ^ k.b; return true;
    case UExt: {
        if (static_cast<u64>(k.b) + k.c > 32) return false;
        const u64 mask{(1ull << k.c) - 1};
        out = static_cast<u32>((static_cast<u64>(k.a) >> k.b) & mask);
        return true;
    }
    case SExt: {
        if (static_cast<u64>(k.b) + k.c > 32) return false;
        if (k.c == 0) { out = 0; return true; }
        const u64 field{(static_cast<u64>(k.a) >> k.b) & ((1ull << k.c) - 1)};
        const bool neg{((field >> (k.c - 1)) & 1) != 0};
        out = static_cast<u32>(neg ? (field | (~0ull << k.c)) : field);
        return true;
    }
    }
    return false;
}

long long wrong = 0, undefined_cases = 0, threw = 0, checked = 0;
const char* const kNames[] = {"UExtract", "SExtract", "And", "Or", "Xor"};
int printed = 0;

// folds a batch of cases in one program; returns false if the pass threw
bool Run(const std::vector<Case>& cases) {
    ObjectPool<IR::Inst> inst_pool;
    ObjectPool<IR::Block> block_pool;
    IR::Program program{};
    IR::Block* const block{block_pool.Create(inst_pool)};
    program.blocks.push_back(block);
    program.post_order_blocks.push_back(block);
    IR::IREmitter ir{*block};
    struct Out { IR::U32 v; IR::U1 z, s; };
    std::vector<Out> outs;
    for (const Case& k : cases) {
        IR::U32 v;
        switch (k.kind) {
        case UExt: v = ir.BitFieldExtract(ir.Imm32(k.a), ir.Imm32(k.b), ir.Imm32(k.c), false); break;
        case SExt: v = ir.BitFieldExtract(ir.Imm32(k.a), ir.Imm32(k.b), ir.Imm32(k.c), true); break;
        case And: v = ir.BitwiseAnd(ir.Imm32(k.a), ir.Imm32(k.b)); break;
        case Or: v = ir.BitwiseOr(ir.Imm32(k.a), ir.Imm32(k.b)); break;
        case Xor: v = ir.BitwiseXor(ir.Imm32(k.a), ir.Imm32(k.b)); break;
        }
        const IR::U1 z{ir.GetZeroFromOp(v)};
        const IR::U1 s{ir.GetSignFromOp(v)};
        outs.push_back({v, z, s});
    }
    Env env;
    try {
        Optimization::ConstantPropagationPass(env, program);
    } catch (const std::exception&) {
        return false;
    }
    for (size_t i = 0; i < cases.size(); ++i) {
        const Case& k{cases[i]};
        u32 want{};
        if (!Reference(k, want)) {
            continue;   // undefined in SPIR-V: nothing to compare (reported as such by the caller)
        }
        ++checked;
        const IR::Value v{outs[i].v.Resolve()}, z{outs[i].z.Resolve()}, s{outs[i].s.Resolve()};
        bool ok{v.IsImmediate() && v.U32() == want};
        // the flags must be the back end's: an immediate with that value, or (unfolded) a pseudo-op left in place
        if (z.IsImmediate()) ok = ok && z.U1() == (want == 0);
        if (s.IsImmediate()) ok = ok && s.U1() == (static_cast<std::int32_t>(want) < 0);
        if (!ok) {
            ++wrong;
            if (printed++ < 20) {
                std::printf("DIFF %s(%08x, %u, %u): folded %s %08x, SPIR-V gives %08x\n", kNames[k.kind], k.a, k.b, k.c,
                            v.IsImmediate() ? "to" : "not", v.IsImmediate() ? v.U32() : 0u, want);
            }
        }
    }
    return true;
}

void Feed(const std::vector<Case>& cases) {
    if (Run(cases)) return;
    for (const Case& k : cases) {   // the pass threw: find out which ones, one by one
        if (!Run({k})) {
            ++threw;
            u32 dummy;
            if (Reference(k, dummy) && printed++ < 20) {
                std::printf("THROW on a case SPIR-V defines: %s(%08x, %u, %u)\n", kNames[k.kind], k.a, k.b, k.c);
            }
        }
    }
}
} // namespace

int main() {
#ifdef _WIN32
    _putenv_s("RX_VK_FOLD_PSEUDO", "1");
#else
    setenv("RX_VK_FOLD_PSEUDO", "1", 1);
#endif
    const u32 bases[] = {0, 1, 0x7FFFFFFFu, 0x80000000u, 0xFFFFFFFFu, 0x55555555u, 0xAAAAAAAAu, 0x80000001u, 0xDEADBEEFu};
    // edge cases, each alone (the pass may throw on an undefined one)
    for (Kind kind : {UExt, SExt}) {
        for (u32 off = 0; off <= 40; ++off) {
            for (u32 cnt = 0; cnt <= 40; ++cnt) {
                u32 dummy;
                if (!Reference({kind, 0, off, cnt}, dummy)) ++undefined_cases;
                for (u32 base : bases) {
                    Feed({{kind, base, off, cnt}});
                }
            }
        }
    }
    for (Kind kind : {And, Or, Xor}) {
        for (u32 a : bases) for (u32 b : bases) Feed({{kind, a, b, 0}});
    }
    // random, in batches (the defined range for the extracts, any immediates for the logic ops)
    std::uint64_t rng{0x9E3779B97F4A7C15ull};
    auto next = [&]() { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return static_cast<u32>(rng >> 16); };
    const int batches{3000}, per{1000};
    for (int b = 0; b < batches; ++b) {
        std::vector<Case> cases;
        for (int i = 0; i < per; ++i) {
            const Kind kind{static_cast<Kind>(next() % 5)};
            Case k{kind, next(), next(), next()};
            if (kind == UExt || kind == SExt) {
                k.b = next() % 33;
                k.c = next() % (33 - k.b);
            }
            cases.push_back(k);
        }
        Feed(cases);
    }
    std::printf("checked %lld defined cases, %lld differ, %lld throw (offset+count over 32 or count 0 in the pass), %lld undefined (offset, count) pairs\n",
                checked, wrong, threw, undefined_cases);
    return wrong ? 1 : 0;
}
