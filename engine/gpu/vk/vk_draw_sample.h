// (stream DRAW1, RX_VK_DRAW_SAMPLE) A sampler of where the recording threads are inside draw(): vk_draw_sample.cpp
#pragma once

namespace mkwvk::draw_sample {

/// RX_VK_DRAW_SAMPLE=<hz> is set
bool on();
/// The calling thread, for the sampler to stop while it is inside draw(); its slot, -1 when off
int register_thread();
void enter(int slot);
void leave(int slot);
/// The counts by address so far, logged (cumulative)
void report();

}   // namespace mkwvk::draw_sample
