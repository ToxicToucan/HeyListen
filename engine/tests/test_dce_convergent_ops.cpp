// Copyright (C) 2026 Sacrappy

// Issue 97 (black leaves): R2P with the constant mask 0x40 after a VOTE.ANY P6 gives
//   P6 = Select(zero(BitFieldUExtract(64, 6, 1)), VoteAny(true), not zero(BitFieldUExtract(src, 6, 1)))
// The pseudo-operation fold turns the condition into the immediate false, FoldSelect keeps the second arm, and the vote has no
// reader left. Dead code elimination must keep it (and every warp-wide operation): the program's ballot and shuffle after it
// depend on the lanes having met at the vote. Runs the real ConstantPropagationPass and DeadCodeEliminationPass.
#include <cstdio>
#include <cstdlib>
#include <exception>

#include "shader_recompiler/environment.h"
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

int count(IR::Block& block, IR::Opcode op) {
    int n{};
    for (const IR::Inst& inst : block) {
        n += inst.GetOpcode() == op ? 1 : 0;
    }
    return n;
}
} // namespace

int main() {
#ifdef _WIN32
    _putenv_s("RX_VK_FOLD_PSEUDO", "1");
#else
    setenv("RX_VK_FOLD_PSEUDO", "1", 1);
#endif
    int bad{};
    {   // the exact R2P shape: mask 0x40, index 6 (the only bit that writes P6)
        ObjectPool<IR::Inst> inst_pool;
        ObjectPool<IR::Block> block_pool;
        IR::Program program{};
        IR::Block* const block{block_pool.Create(inst_pool)};
        program.blocks.push_back(block);
        program.post_order_blocks.push_back(block);
        IR::IREmitter ir{*block};
        const IR::U1 vote{ir.VoteAny(ir.Imm1(true))};
        const IR::U32 src{ir.GetCbuf(ir.Imm32(0), ir.Imm32(0x20))};
        const IR::U1 src_bit{ir.LogicalNot(ir.GetZeroFromOp(ir.BitFieldExtract(src, ir.Imm32(6), ir.Imm32(1), false)))};
        const IR::U1 inv_mask_bit{ir.GetZeroFromOp(ir.BitFieldExtract(ir.Imm32(64), ir.Imm32(6), ir.Imm32(1), false))};
        const IR::U1 p6{IR::U1{ir.Select(inv_mask_bit, vote, src_bit)}};
        ir.ConditionRef(p6);
        Env env;
        try {
            Optimization::ConstantPropagationPass(env, program);
            Optimization::DeadCodeEliminationPass(program);
        } catch (const std::exception& e) {
            std::printf("FAIL: pass threw %s\n", e.what());
            return 1;
        }
        if (count(*block, IR::Opcode::VoteAny) != 1) {
            std::printf("FAIL: the VoteAny of R2P mask 0x40 was removed (%d left)\n", count(*block, IR::Opcode::VoteAny));
            ++bad;
        }
    }
    {   // every warp-wide operation survives with no reader
        ObjectPool<IR::Inst> inst_pool;
        ObjectPool<IR::Block> block_pool;
        IR::Program program{};
        IR::Block* const block{block_pool.Create(inst_pool)};
        program.blocks.push_back(block);
        program.post_order_blocks.push_back(block);
        IR::IREmitter ir{*block};
        const IR::U1 t{ir.Imm1(true)};
        const IR::U32 x{ir.GetCbuf(ir.Imm32(0), ir.Imm32(0x20))};
        (void)ir.VoteAll(t); (void)ir.VoteAny(t); (void)ir.VoteEqual(t); (void)ir.SubgroupBallot(t);
        (void)ir.ShuffleIndex(x, ir.Imm32(0), ir.Imm32(31), ir.Imm32(0));
        (void)ir.ShuffleUp(x, ir.Imm32(1), ir.Imm32(0), ir.Imm32(0));
        (void)ir.ShuffleDown(x, ir.Imm32(1), ir.Imm32(31), ir.Imm32(0));
        (void)ir.ShuffleButterfly(x, ir.Imm32(1), ir.Imm32(31), ir.Imm32(0));
        Optimization::DeadCodeEliminationPass(program);
        const IR::Opcode ops[] = {IR::Opcode::VoteAll, IR::Opcode::VoteAny, IR::Opcode::VoteEqual, IR::Opcode::SubgroupBallot,
                                  IR::Opcode::ShuffleIndex, IR::Opcode::ShuffleUp, IR::Opcode::ShuffleDown,
                                  IR::Opcode::ShuffleButterfly};
        for (IR::Opcode op : ops) {
            if (count(*block, op) != 1) {
                std::printf("FAIL: opcode %d removed with no reader\n", static_cast<int>(op));
                ++bad;
            }
        }
    }
    std::printf(bad ? "FAILED\n" : "ok: the vote of R2P mask 0x40 and the other warp-wide operations survive dead code elimination\n");
    return bad ? 1 : 0;
}
