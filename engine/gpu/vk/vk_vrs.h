// Copyright (C) 2026 Sacrappy

// RX_VK_VRS: variable-rate shading, the pipeline's 2x2 rate (VK_KHR_fragment_shading_rate) on the draws of low-frequency passes.
// Off by default: the extension is not even enabled then. tests/vrs_test.cpp
//
//   RX_VK_VRS=<item>[,<item>...]   an item is a program key's first hex digits (8 or more, the fragment program of the draw:
//                                  the "prog" lines of RX_VK_PASS_TIMING=2 name them) or WxH (every draw of a pass of that size
//                                  that has colour targets and no depth: a post pass), or "all" (every draw: a diagnostic)
//   RX_VK_VRS_MIN_W=<n>            draws into targets narrower than n pixels stay 1x1 (default 64)
//
// A 2x2 draw shades once for each block of four pixels and writes the result to all four: right for what is smooth (fog, a blur,
// the diffuse light, a bloom), visible on an edge. The rate never applies to a multisampled target or to a draw that writes depth.
#pragma once

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace mkwvk::vrs {

struct Rule {
    std::vector<std::string> progs;   // (lower-case hex prefixes)
    std::vector<std::pair<uint32_t, uint32_t>> sizes;
    bool all = false;
    uint32_t min_w = 64;
    bool any() const { return all || !progs.empty() || !sizes.empty(); }
};

/// The rule from RX_VK_VRS's text (null or empty: nothing selected)
inline Rule parse(const char* text, uint32_t min_w = 64) {
    Rule r;
    r.min_w = min_w;
    for (const char* q = text; q && *q;) {
        const char* e = q;
        while (*e && *e != ',') e++;
        const std::string w(q, e);
        q = *e ? e + 1 : e;
        if (w.empty()) continue;
        if (w == "all") {
            r.all = true;
            continue;
        }
        const size_t x = w.find('x');
        if (x != std::string::npos && x > 0 && x + 1 < w.size() && isdigit((unsigned char)w[0])) {
            r.sizes.emplace_back((uint32_t)atoi(w.c_str()), (uint32_t)atoi(w.c_str() + x + 1));
            continue;
        }
        std::string h;
        for (const char c : w) h += (char)tolower((unsigned char)c);
        r.progs.push_back(h);
    }
    return r;
}

/// Whether a draw is shaded at 2x2: program key (as a draw has it), the pass's size, its colour targets, depth, samples, and
/// whether the draw writes depth
inline bool select(const Rule& r, uint64_t key, uint32_t w, uint32_t h, int colours, bool depth, bool depth_write, int samples) {
    if (!r.any() || samples > 1 || depth_write || w < r.min_w || h < 2) return false;
    if (r.all) return true;
    char k[20];
    snprintf(k, sizeof k, "%016llx", (unsigned long long)key);
    for (const std::string& p : r.progs)
        if (p.size() <= 16 && strncmp(k, p.c_str(), p.size()) == 0) return true;
    for (const auto& s : r.sizes)
        if (s.first == w && s.second == h && colours > 0 && !depth) return true;
    return false;
}

/// Whether the switch is set at all (read once)
inline bool wanted() {
    static const bool v = [] {
        const char* e = getenv("RX_VK_VRS");
        return e && *e && strcmp(e, "0") != 0;
    }();
    return v;
}

} // namespace mkwvk::vrs
