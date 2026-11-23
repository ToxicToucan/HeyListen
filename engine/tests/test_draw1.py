"""The per-draw switches of stream DRAW1 (gpu/vk/vk_draw.cpp, vk_memory.cpp, vk_draw_sample.cpp) are still there, each with its off value
and its check; source-shape test, no game or GPU required.

RX_VK_DRAW_LAZY      the "may this draw be left out" test is made only for a draw whose program or pipeline is not ready
RX_VK_DRAW_HPTR      a texture handle's "is a storage buffer's pointer" answer kept with the program's texture slots
RX_VK_DRAW_PROGFRONT (off by default) the program picks answered from the last 32 answers (program_for's rule), program_for itself when none stands
RX_VK_DRAW_PIPEFAST  (off by default) the last 16 pipelines (exact state equality) tried before the thread's map, not the last 2
RX_VK_DRAW_EXCL      the capture's left-out ranges searched in a per-thread copy, not under cap_excl_mu at every buffer binding
RX_VK_DRAW_CHECK     the old answer worked out too, compared and counted (mismatches logged)
RX_VK_DRAW_SAMPLE    the sampler of where draw() spends its time (diagnosis)

The shader stamp files (vk_shader.cpp, vk_bank.cpp, vk_state.h) must not carry any of this: a change to them makes every player
translate every program again (nx2/translator_files.py).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / "gpu/vk" / name).read_text(encoding="utf-8")


def need(text, what, needles):
    for n in needles:
        if n not in text:
            raise SystemExit("%s no longer has: %s" % (what, n))


def main():
    draw = read("vk_draw.cpp")
    need(draw, "vk_draw.cpp", [
        'getenv("RX_VK_DRAW_LAZY")', 'getenv("RX_VK_DRAW_HPTR")', 'env_on("RX_VK_DRAW_PROGFRONT")', 'env_on("RX_VK_DRAW_PIPEFAST")', 'getenv("RX_VK_DRAW_CHECK")',
        "may_skip_now()", "prog_front_find(", "prog_front_keep(", "draw_check_note(", "struct FineDraw", "fine_t()",
    ])
    # the picks kept here follow program_for's own rule: its helpers' bodies are copied, so a change there must come here
    shader = read("vk_shader.cpp")
    need(shader, "vk_shader.cpp (prog_front_* copies its rules)", [
        "uint64_t h = (uint64_t)p.nstage << 32 | p.magic;",
        "for (int i = 0; i < 6; i++) h = h * 1099511628211ull ^ p.sd[i].data ^ (p.sd[i].control << 1);",
        "return h ^ p.serial;",
        "c2 = d.handle_program == ~0U && d.cbuf_index == 2 && (!d.has_secondary || d.secondary_cbuf_index == 2);",
        "if (fbind != bind && !by_bind && !compute && f.amask != ~0u) {",
    ])
    mem = read("vk_memory.cpp")
    need(mem, "vk_memory.cpp", ['getenv("RX_VK_DRAW_EXCL")', "cap_excl_gen.fetch_add(", 'getenv("RX_VK_DRAW_CHECK")'])
    sample = read("vk_draw_sample.cpp")
    need(sample, "vk_draw_sample.cpp", ['getenv("RX_VK_DRAW_SAMPLE")', "SuspendThread", "GetThreadContext"])
    for stamped in ("vk_shader.cpp", "vk_bank.cpp", "vk_state.h"):
        if "RX_VK_DRAW_" in read(stamped):
            raise SystemExit("%s is in the shader stamp and must not carry the DRAW1 switches" % stamped)
    print("PASS: the DRAW1 switches and their checks are in place")


if __name__ == "__main__":
    main()
