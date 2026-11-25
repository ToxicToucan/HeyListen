# Copyright (C) 2026 Sacrappy

"""The built-in frame interpolation blends the pictures from before the HUD (RX_VK_FG_HUDLESS), step 4 of the HUD-less chain.

The renderer's own interpolation (block matching, 2x to 8x and Auto) was given the presented frames, HUD and all, so a HUD over a
turning scene was moved with it, and the blocks the HUD is over were held still by it. With RX_VK_FG_HUDLESS it finds its motion in
the HUD-less pictures, blends the HUD-less pictures of two frames (the pair, vk_framegen.cpp hud_source), and lays this frame's HUD
over each made frame by how see-through it is (the HUD layer of step 3: where that is not known, the presented pixel). A present
without the pair (the first, a menu, a movie, another crop or size, Native HDR's PQ, the layer not made) goes on as before.

The pair, the switches and the capture are run by tests/test_fsr3fi_hud_capture.py (its `builtin` fixture) and the fit by
tests/test_fsr3fi_hud_layer.py; this check reads framegen_record's wiring, which no fixture can run without a GPU:
  - the luma the motion is found in comes from the HUD-less picture when there is a pair;
  - both blends (Auto's and the fixed multipliers') read the HUD-less pair and lay the HUD over the made frame after it;
  - the game's own frame, the copy of the presented one, is the set's last, untouched;
  - nothing of it runs without the pair, and the switch is off by default.

    python -I -B tests/test_fg_hudless.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # (python -I leaves the check's own folder off the path)
from srccut import block  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main():
    fg = (ROOT / "gpu/vk/vk_framegen.cpp").read_text(encoding="utf-8")
    switch = block(fg, "bool fg_hudless_wanted(")
    assert 'getenv("RX_VK_FG_HUDLESS")' in switch and "return e && *e && atoi(e) != 0;" in switch, "RX_VK_FG_HUDLESS: off by default"
    wanted = block(fg, "bool framegen_hud_wanted(")
    assert "framegen_on()" in wanted and "(hudless_wanted() && fsr3fi::frames_wanted()) || fg_hudless_wanted()" in wanted
    record = block(fg, "bool framegen_record(")
    pair = record.index("const bool hud_pair = framegen_hud_wanted() && hud_source(")
    assert "&& hud.pair;" in record[pair:pair + 250], "the pair needs a frame before, and the layer"
    luma = record.index("pass(cmd, fg.luma,")
    assert pair < luma and "{{view_now, fg.nearest}, {fg.lumas[cur].view, VK_NULL_HANDLE}}" in record[luma:luma + 120], \
        "the motion is found in the HUD-less pictures when there is a pair, and the HUD pass runs first"
    assert "hud_pair ? hud.hudless[hud.hl].view : fg.frames[cur].view;" in record
    assert "hud_pair ? hud.hudless[hud.hl ^ 1].view : fg.frames[prev].view;" in record
    assert "const int cur = fg.cur, prev = cur ^ 1" in record and record.index("const int cur = fg.cur") < pair
    # (both blends: the HUD-less pair, then the HUD over the made frame, which is the set's own image)
    auto = "pass(cmd, synth, {{view_before, fg.linear}, {view_now, fg.linear},\n                                 {fg.flows[1].view, fg.nearest}, {set->frame[k].view, VK_NULL_HANDLE}},\n                 uw, uh, &t[k], 4);\n            if (hud_pair) hud_composite(cmd, set->frame[k].view, cur, uw, uh);"
    fixed = "pass(cmd, synth, {{view_before, fg.linear}, {view_now, fg.linear},\n                                 {fg.flows[1].view, fg.nearest}, {set->frame[k - 1].view, VK_NULL_HANDLE}},\n                 uw, uh, &t, 4);\n            if (hud_pair) hud_composite(cmd, set->frame[k - 1].view, cur, uw, uh);"
    assert auto in record, "Auto's blend: the pair, then the HUD over the made frame"
    assert fixed in record, "the fixed multipliers' blend: the pair, then the HUD over the made frame"
    assert "fg.frames[prev].view, fg.linear" not in record and "{fg.frames[cur].view, fg.linear}" not in record, \
        "a blend left on the presented frames"
    # (the game's own frame stays the presented one)
    assert record.count("vkCmdCopyImage(cmd, fg.frames[cur].image") == 2
    # (no pair, no layer, no change: the layer is made only with a switch that wants it)
    layer = block(fg, "bool hud_layer(")
    assert "!(hud_layer_wanted() || fg_hudless_wanted())" in layer
    sh = block(fg, "bool hud_source(")
    assert "hud.pair = fg_hudless_wanted() && had_prev && hud.layered && hud.hudless[k ^ 1].image;" in sh
    # (the capture is wanted for the built-in interpolation alone too: vk_draw.cpp asks framegen_hud_wanted, not FSR 3.1)
    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    assert "if (p.stages.empty() || !framegen_hud_wanted()) return nullptr;" in draw
    assert "if (stub_picture && framegen_hud_wanted()) framegen_hud_before(cmd, stub_picture, 0);" in draw
    print("fg hudless: ok")


if __name__ == "__main__":
    main()
