<!-- Copyright (C) 2026 Sacrappy -->
# LAPTOP1: 25-30 fps while turning the camera on a low-end laptop (Ryzen 7 5700U, Radeon Vega 8)

Branch `perf/lowend` (worktree `E:\OOT\HeyListen-wt\laptop1`, main 318a4a839 + perf/texcache d7abeb700 merged). Scratch
`E:\OOT\scratch-laptop1`. Lease name `laptop1`. The laptop is run by its pilot session from run lists; its logs come back to
`E:\OOT\scratch-laptop1\L<n>\<arm>`.

Target machine: Ryzen 7 5700U, Radeon Vega 8 integrated (0x1002:0x164c), 15.4 GB shared RAM, no mesh shaders (issue 640 fallback),
driver 26.5.2, Windows; 1920x1080 output, Render resolution 50% (960x540), the game's own upscaling network (Upscaler 0):
`E:\OOT\scratch-texcache\laptop1\TX3_warm\game_log.txt` line 225.

## State (25 Nov 01:00)

Steps on the laptop (fps still / turning, 60 s turning window of MIR_ref_w1; logs `E:\OOT\scratch-laptop1\L<n>\<arm>\game_log.txt`):

| step | still | turning | source |
|---|---|---|---|
| this afternoon, main14 (wait on, no disk cache) | 11-13 | 6-8 (11.7 in one run) | MORNING.md 15:57, 16:26, 16:40 |
| texcache4, warm disk cache, defaults | 10.3-12.4 | 10.1 / 10.5 | L1 R0, R1 |
| + handheld profile | 15.1-15.7 | 13.5 | L1 H0 |
| fog off alone | 11.2-13.1 | 12.2 | L1 F0 |
| handheld + fog off | 16.3-16.7 | 13.9 / 14.3 | L2 LE, LE2 |
| + render 33% + mirror instead of the wait (texcache4) | 19.0-19.5 | 15.0 / 15.3 | L2 L33M, L33M2 |
| the same set, texcache5 | 18.3-19.7 | 16.0-16.4 | L5 Sa, Sb, Sc |
| + RX_VK_PARALLEL=0 (not adopted) | 18.4-19.0 | 17.2-17.8 | L5 Pa, Pb, Pc |
| lowend1, the same set by its switches | 17.8-19.1 | 15.9 / 15.9 | L6 B1, B2 |
| bound: no mesh fallback at all (grass, leaves missing) | 25.1-25.5 | 20.7 | L3 SN |

- Shipped on perf/lowend (all off by default or by hardware rule): the Volumetric fog row (RX_VK_FOG); Render resolution 33% for the
  game's own upscaler and Off; the first-start low-end rule for integrated GPUs without mesh shaders (33%, handheld from the next
  start, fog off; its four keys only; HL_LOWEND); the VRAM mirror in place of the wait on AMD no-mesh APUs; the fallback's timing
  spans; the indexed fallback (RX_VK_FALLBACK_INDEXED, off: no gain).
- Build lowend2 (a0c6f8b4f) queued; its main-PC check (s6) and the laptop's rule list 7 and the 3050 fog list wait for it.
- What is left to 25-30 turning: the mesh fallback (~11 ms of ~46), the GPU gaps (~9 ms), the upload bursts while turning
  (5-16 ms): see "Levers that need more than a night".

## Where the frame goes before any lever (texcache4, warm disk cache, MIR_ref_w1)

Source: TEXCACHE list 3 arm 4 (`E:\OOT\scratch-texcache\laptop3\TX3_4_warm\game_log.txt`), 120-present blocks:

| window | fps | draws | GPU ms a frame | GPU uploads ms | gaps between recordings ms | replay "present" ms | "draw" | "descriptors" |
|---|---|---|---|---|---|---|---|---|
| still | 11.8-12.6 | 1896-1927 | 67.6-67.7 | 0.1 | 16.9-17.4 | 53-54 | 32-34 | 14.7-15.5 |
| turning | 9.3-10.7 | 2032-2220 | 73.1-75.5 | 5-17 | 15-16 | 60-72 | 45-53 | 24-29 |

Reading (to confirm with list 1's thread times and pass map): at rest the frame is the GPU's work plus the gaps where it waits for
the next recording; turning adds GPU uploads and about 10 ms of descriptor work and 5-8 ms of texture lookups on the replay side.
Both the GPU (67 ms) and the replay (53 ms) are over the 33-40 ms a 25-30 fps frame allows.

## List 1: the cost map (texcache4, warm cache, 24 Nov 20:52-21:18)

Logs `E:\OOT\scratch-laptop1\L1\<arm>\game_log.txt`, pilot summary `L1\pilot-summary.txt`, read with `E:\OOT\scratch-laptop1\ana.py`.
Every arm WAIT_OWN=1 (the AMD integrated default), 50% render, game's own upscaler.

| arm | what | fps still | fps turning | GPU ms still / turning ("vk: GPU time") | gaps ms |
|---|---|---|---|---|---|
| R0, R1 | reference | 10.3-12.4 | 10.1 / 10.5 | 65-69 / 71-77 | 13-27 |
| H0 | HOS_HANDHELD=1 (the game's handheld profile) | 15.1-15.7 | 13.5 | 48 / 52-55 | 13-17 |
| F0 | fog off (RX_VK_SKIP_PROG on its three programs) | 11.2-13.1 | 12.2 | 59 / 60-61 | 14-16 |
| HF | handheld + fog off | 16.0-17.2 | (7.6: invalid, see below) | 44 / 47-54 | 13-17 |
| THF | the same with the pass map | 14.6-16.6 | 13.6 | | |

- HF's turning is not the lever: its disk cache served 268 textures against 2344-3809 in the others ("vk: BC disk cache" line;
  the map list read nothing, fixed in texcache5), and its GPU uploads were 57-80 ms a frame.
- The machine is GPU-bound: whole-CPU use 10-30% (perf.csv of R0), and frame time = GPU busy + the GPU's empty gaps between
  recordings (R0 still: 69.5 + 27 ms at 10.3-12.4 fps; HF still: 44 + 16 ms at 16-17 fps). HOS_THREAD_TIMES gave no lines on
  Windows (the thread-time sampler is in the Linux path only).
- The game's upscaling network does not run on this GPU: "matrix multiplies in software (no cooperative matrices)", "program
  ... not translated: a network left out on this card" (11 programs), and our TAAU stand-in (linear + FXAA) draws in its place.
  So there is no network cost to remove; any render share works with the stand-in.

### Pass map (RX_VK_PASS_TIMING=2; blocks of 600 presents; T0 block 5 = turning, THF block 5 = still; ms a frame)

| item | T0 (reference) | THF (handheld + fog off) |
|---|---|---|
| busy / window | 77.4 / 92.3 | 47.5 / 63.4 |
| passes | 32.6 (191 spans) | 21.8 (191) |
| dispatches | 19.2 (133) | 9.3 (123) |
| barriers | 3.7 (420) | 3.8 (424) |
| ASTC uploads (u) | 8.1 | 0.2 |
| holes inside command buffers (glue) | 12.4 | 11.2 |
| empty between buffers (gaps) | 14.9 | 15.9 |
| fog 27ee9883 / ee7764a6 / 1e2fcee3 | 5.45 / ~2.8 / ~1.8 (docked: 96x72x88 volume) | off (handheld makes the volume 72x48x56: 1.8 ms in TH) |
| shadow targets | 2400x2400 f126 2.3 + 3 x 1600x1600 f124 2.1 | 1600x1600 f126 0.9 + 3 x 1000x1000 f124 0.46 |
| scene passes | 960x540 (17 kinds) | 768x432 (15 kinds) |
| output-size passes | 1920x1080 f43 1.1, f122 clears | 2560x1440 f43 0.73, 1536x864 f122 0.57 |

The handheld profile is the game's own lower settings, not only a smaller picture: scene 768x432 (from 960x540), shadow maps
1600+1000 (from 2400+1600), fog volume 72x48x56 (from 96x72x88). With the fog off too, no single item over 1.2 ms is left:
what remains is spread over ~190 passes and ~120 dispatches, plus ~420 barriers (3.8 ms), the glue (11 ms) and the gaps (16 ms).

Pictures (`L1\R0\still.png`, `L1\HF\still.png`): HF has no haze (crisp castle and hills, more saturated) and a softer scene; HUD,
Link, Navi, grass right. Acceptable for a low-end preset.

## List 2: the low-end set, the render share under 50%, the mirror instead of the wait (texcache4, 24 Nov 21:19-22:05)

Logs `E:\OOT\scratch-laptop1\L2\<arm>\game_log.txt`, pilot summary `L2\pilot-summary.txt`. LE = HOS_HANDHELD=1 + fog off
(RX_VK_SKIP_PROG on its three programs); M = RX_VK_WAIT_OWN=0 RX_VK_MIRROR=1; 33 / 25 = HOS_RES_SCALE=0.3333 / 0.25.

| arm | fps still (windows 2-4) | fps turning (~60 s) | GPU ms still / turning | gaps ms |
|---|---|---|---|---|
| LE, LE2 | 16.3-16.6, 16.3-16.7 | 13.9, 14.3 | 44-46 / 49-51 | 13-16 |
| LM | 13.7-15.8 | 13.6 | | |
| L33 | 17.2-18.1 | 14.2 | | |
| L33M, L33M2 | 19.0-19.5, 14.0-18.5 | 15.0, 15.3 | 45-47 / 47-49 | 8.5-11 |
| L25M | 17.3-17.9 | 14.9 | 45-48 / 47-49 | 9-13 |

- The scale arms were scaled (T33M: scene passes 512x288 against 768x432; the "Render resolution" line shows the menu's row,
  not HOS_RES_SCALE). The GPU time does NOT follow the render size at this level: 44-48 ms at 768x432, 512x288 and 384x216.
- The mirror in place of the wait takes the gaps from 13-16 to 8.5-11 ms and gives about +1 fps turning at 33% (L33 14.2 ->
  L33M 15.0/15.3), nothing at 50% (LM 13.6 against LE 13.9/14.3). Pictures with the mirror (`L2\L33M\turning+150..450.png`,
  `L2\L25M\...`): HUD, grass and hills right.
- Pictures: 33% acceptable, 25% blocky (`L2\L25M\turning+150.png`).
- Turning frame (L33M2): GPU 48.5 + uploads ~8 + gaps ~10 = ~66 ms, 15 fps.

Pass map at 512x288 (T33M block 5, still, 600 presents): busy 43.9 = passes 18.2 (191 spans, 95 us each), dispatches 9.2 (122),
barriers 3.4 (435), meshconv 0.6, bubbles between spans inside command buffers 11.6; plus 8.8 ms empty between buffers (window
52.7). Largest spans: barriers "other" 1.8 (246 a frame), the game's barriers 1.1 (109), dispatch b6122162 1.3 and indirect
44511d69 1.0 (culling for the mesh passes), 1600x1600 shadow clear 0.8, the 2560x1440 f43 composite 0.7, one-draw grass passes
d905f52b / 739acd67 0.7 each even at 512x288. So what is left is a fixed cost per pass, per dispatch and per barrier, not pixels.

### Why the fixed cost: the no-mesh fallback splits passes

Each fallback mesh draw (`vk_draw.cpp` fallback_record: ~49 a frame here, the "meshconv" count) ends the pass, records 4 full
barriers, fills its records header, dispatches the task and mesh stages as compute, then starts the pass again (a load and a store
of every attachment in GENERAL layout). The laptop's N2 (nomesh notes, docked): RX_VK_NO_MESH_FALLBACK=1 drops passes 193 -> 113
and barriers 438 -> 185 a frame and 5.5 ms of GPU time. List 3 measures that bound at the low-end set.

## List 3: what is left once the picture is light (texcache5, 24 Nov 22:20-22:45)

Logs `E:\OOT\scratch-laptop1\L3\<arm>\game_log.txt`, summary `L3\pilot-summary.txt`. LS = HOS_HANDHELD=1 + fog off +
HOS_RES_SCALE=0.3333 + RX_VK_WAIT_OWN=0 RX_VK_MIRROR=1. texcache5 ran clean in all 8 runs (its first game runs; the map list is
read: 208-639 textures from it).

| arm | fps still | fps turning | GPU ms |
|---|---|---|---|
| S0, S1 (LS) | 16.4-18.4, 17.9-18.4 | 15.2, 15.8 | 45.1, 44.9 |
| SB, SB2 (RX_VK_BARRIER_HAZARD=3) | 19.2-19.5, 15.4-18.4 | 15.9, 15.7 | 43.3, 46.0 |
| SN (RX_VK_NO_MESH_FALLBACK=1: grass, tree leaves and a HUD counter NOT drawn) | 25.1-25.5 | 20.7 | 32.2 |
| SP (RX_VK_PARALLEL=0) | 18.1-18.8 | 16.9 | 45.9 |
| SU (RX_VK_UPLOAD_BUDGET_MS=12) | 18.6-19.6 | 16.2 | 45.4 |

- The no-mesh fallback costs ~13 ms of GPU a frame at the low-end set (45 -> 32 ms), a third of the frame. Without it the laptop
  would be at 25 still / 21 turning. SN's picture (`L3\SN\still.png`) has no grass, bare tree trunks and no stick count in the HUD:
  a bound, not an option.
- Barrier elision and the upload budget are inside the noise (~0.6 fps); PARALLEL=0 +1 fps turning, to confirm (list 4).
- Where the 13 ms goes: in the pass maps the fallback's barriers are cheap spans (7-10 us, ~250 a frame) and its compute
  dispatches had no span: they are most of the 11-12 ms of "holes inside buffers". So the cost is the translated task and mesh
  programs' own work and what they write, not the pass splits. f5f2bd64b names them (spans "fallback task/mesh stage prog <key>").

### The fallback writes each vertex about three times

`emit_spirv_mesh.cpp` MeshFallbackEpilogue writes one record per primitive CORNER, each holding every vertex line of that vertex
(d8e59341: 25 lines, 100 bytes): a 128-vertex, ~126-triangle meshlet writes 378 records, then the generated vertex stage reads
378 vertices with no reuse. 7d11e1655 adds an indexed form (RX_VK_FALLBACK_INDEXED=1, off by default; for triangles with no
per-primitive lines): a record a vertex (max_vertices of them: the stage buffer has no vertex count) and three 32-bit indices a
primitive, at the end of the records buffer, drawn with vkCmdDrawIndexedIndirect. Expected: about a third of the record writes
and vertex invocations for full meshlets. Moves the shader stamp (the laptop prepares its shaders again once).

## Lists 4-5: the parallel replay off (texcache5)

`L4\`, `L5\` (summaries `pilot-summary.txt`). RX_VK_PARALLEL=0 at the low-end set, 3 interleaved rounds (L5): turning 17.4 / 17.2
/ 17.8 against 16.2 / 16.0 / 16.4 (+1.3 fps, no overlap), at rest 18.4-19.0 against 18.3-19.7 (about 0.5-1 fps slower). In the
blocks (L5 Sc/Pc) the GPU uploads fall from 4-8 to 1.6-3 ms a frame turning while the GPU time rises 1-2 ms: GPU + uploads ~54
against ~51 ms. Not made a rule: a lead (uploads serial, recording parallel) for later.
LF (RX_VK_LOG_FALLBACK=1): about 49 fallback draws a frame, ~40 mesh workgroups each, every workgroup asked was run.

## lowend1 on the main PC (24-25 Nov 23:15-23:58)

Build `build/s5/bin/lowend1` = perf/lowend 7d11e1655. Slot a, logs `build/s5/l1-v0`, `l1-vf`, `l1-vl`; shots
`E:\OOT\scratch-laptop1\shots\v0\vk_012600.png` (defaults: haze in the distance, picture right), `shots\vf\vk_015000.png`
(RX_VK_FOG=0: no haze, nothing black or undefined). l1-vl: "low-end GPU ..., HL_LOWEND=1: the Minimum preset ... for this run only
(nothing saved)". The Deku Tree / graveyard fog pictures and the compute-path check were cut by the slot lease's end (40 min).

## Levers (measured on the laptop; fps still / turning against the line above it)

| lever | how shipped | gain | source |
|---|---|---|---|
| disk cache of transcoded textures (TEXCACHE) | perf/texcache, its rule | turning 6-8 -> 10-11 | TEXCACHE lists 1-3 |
| handheld profile (the game's own low settings: 768x432, smaller shadows and fog volume) | low-end rule, from the 2nd start | 11.5 / 10.3 -> 15.4 / 13.5 | L1 H0 |
| volumetric fog off | Volumetric fog row (RX_VK_FOG), low-end rule | GPU -9 ms docked; +1.5 / +1.9 alone; ~+1 with handheld | L1 F0, HF |
| render 33% | Render resolution step for the game's own / Off, low-end rule | still +1.3, turning ~0 (GPU time does not follow the size there) | L2 L33 |
| VRAM mirror in place of the wait | default on AMD no-mesh APUs | +1 turning at 33%, gaps 13-16 -> 9-11 ms | L2 L33M, L7 D0 vs D1 |
| barrier elision (RX_VK_BARRIER_HAZARD=3) | not shipped | noise | L3 SB, SB2 |
| upload budget 12 ms | not shipped | noise | L3 SU |
| parallel replay off | not shipped | turning +1.3, still -0.5 to -1 | L5 |
| indexed fallback | RX_VK_FALLBACK_INDEXED, off | noise (11.14 -> 11.10 ms) | L6 I0-I2 |
| (bound) no mesh fallback | - | 25 / 20.7 | L3 SN |

The proposed rule (shipped, lowend2 + 44e37b785): integrated GPU without mesh shaders, first start, once (lowend_rule=1): Render
resolution 33%, Volumetric fog off, handheld profile (next start); said in the log; the Insert menu changes them back; HL_LOWEND=0
never, =1 for one run. Picture change on those machines only: softer (33%), no haze or mist (fog), the game's handheld settings.

## List 6: lowend1 on the laptop (25 Nov 00:00-00:55)

`L6\<arm>`, `L6\pilot-summary.txt`, `options_before_RULE.ini` / `options_after_RULE.ini`. LE1 = handheld + RX_VK_FOG=0 + 33% +
mirror without the wait. All 7 runs reached play, no FATAL / device lost.

| arm | fps still | fps turning |
|---|---|---|
| B1, B2 (LE1) | 18.2-19.1, 17.8-18.6 | 15.9, 15.9 |
| I1, I2 (LE1 + RX_VK_FALLBACK_INDEXED=1) | 18.7-20.1, 18.2-18.5 | 16.0, 16.8 |
| RULE (first start with lowend1's first rule: Minimum preset, fog off, handheld only from the next start, wait kept) | 12.7-14.6 | 12.7 |

- The fallback's spans (B0, still): "fallback" 11.1 ms a frame in 207 spans, and the unnamed glue fell from 11.6 to 1.1 ms: the
  glue WAS the fallback. Mesh stages hold it: 739acd67 2.5 ms, d905f52b 1.8, 4f0c0b40 1.6, d8e59341 0.44, a6a688ba 0.43,
  d4abbcb4 0.32; task stages and fills are under the top-25 cut; their draws add 739acd67 0.77, d905f52b 0.75 ms.
- The indexed form (I0 against B0): d905f52b 1.8 -> 1.2 ms and its draw 0.75 -> 0.43, but 4f0c0b40 1.6 -> 2.3 (far-tree meshlets are
  mostly culled: a record for each of 128 vertices costs more than the corners of a few triangles) and 739acd67 unchanged; the
  fallback in all 11.14 -> 11.10 ms. The cost is the game's mesh programs running as compute, not the records. Kept as a switch,
  off. Pictures right with it (`L6\I1\turning.png`, looked at by the coordinator and me).
- The first rule (lowend1) wrote the whole Minimum preset and a full save: upscaling 0->2, sharpness 0->30, frame_pacing 0->1 (the
  preset's rows), microphone 2->3 (the save writing the live device index; the device is the same, found by name), all rows'
  defaults added. Fixed in 9b87191e6: the rule writes resolution, handheld_profile, volumetric_fog and lowend_rule only, in place.
- `--prepare-shaders` did nothing there ("0 programs"): that game folder has no shader list (hookshot_shaders.bin, written by the
  Builder's nx2/make_game.py through shader_census.py); its program cache holds what the runs translated in play (573-746 keys).
  Not a handicap for these numbers: every run logs "0.00 waits for shaders or pipelines" and ~400 ms of translation in all
  (`L6\B1\game_log.txt`). A player's Builder install has the list and prepares at its first start.

## List 7: lowend2, the rule end to end (25 Nov 01:50-02:20)

`L7\<arm>`, `L7\pilot-summary.txt`, `L7\owner_options.ini`, `L7\options_after_<arm>.ini`. All 6 runs reached play at the first start.

| arm | fps still | fps turning |
|---|---|---|
| D1: this afternoon's defaults (the wait, no mirror; 50%, docked, fog on) | 11.6-13.2 | 11.0 |
| D0: lowend2's defaults on this laptop with HL_LOWEND=0 (mirror on, wait off) | 13.0-13.8 | 11.7 |
| R1: first start, the rule applies (33%, fog off, mirror; not yet handheld) | 15.2-16.4 | 14.2 |
| R2, R3: second and third start (handheld too) | 17.2-18.8, 17.2-18.1 | 16.2, 15.5 |
| LS2: the rule's set by switches | 19.0-19.8 | 16.6 |

- The options diff after R1 is the four keys only (resolution 4 -> 6, handheld_profile 0 -> 1, volumetric_fog=0 and lowend_rule=1
  appended; microphone, frame_pacing and the order untouched), but the file came back LF from CRLF: fixed in 44e37b785 (binary
  read, lowend_rule::ini_put_text keeps every line's ending and writes the file's own; tested with CRLF, LF, no file, a key
  prefix). Not yet run in a build.
- R2's picture (`L7\R2\late.png`, looked at by the coordinator): field, Link, HUD, minimap right, soft as 33% is.

## lowend2 on the main PC (25 Nov 01:47-02:21, slot b)

Logs `build/s5/l1-v0`, `l1-vf`, `l1-vl`, `l1-d1`, `l1-d0`, `l1-g1`, `l1-g0`; shots `E:\OOT\scratch-laptop1\shots\<tag>`.
- Defaults (v0): play at the first try, "VRAM mirror ... on" (discrete, as before), "volumetric fog on (the Volumetric fog row)",
  picture right (`shots\v0\vk_006000.png`).
- HL_LOWEND=1 (vl): the rule's line for the run, "Render resolution: 33% chosen, drawn at 33% with Upscaler 0", picture right with
  the game's own network at 33% (`shots\vl\vk_012000.png`).
- Fog on/off side by side: Deku Tree `shots\d1\vk_012600.png` / `shots\d0\vk_018000.png`: off loses the light shafts' haze and the
  glow around Navi, the room is darker and crisper, nothing black, flashing or undefined. Graveyard `shots\g1\vk_010800.png` /
  `shots\g0\vk_015000.png`: off loses the mist over the graves and the archway; clean. The fog is atmosphere in these places: a
  real look change, right for a player's option and the low-end rule, not for a default.

## Levers that need more than a night

0. **Two meshlets a workgroup on wave64 GPUs** (the first lever after the release). Evidence: the fallback's mesh stages are ~9 ms
   of a ~46 ms frame at the low-end set (L6 B0: 739acd67 2.5, d905f52b 1.8, 4f0c0b40 1.6, d8e59341 0.44, a6a688ba 0.43, d4abbcb4
   0.32); every hot mesh stage is 32 threads a workgroup (GPU11's meshinfo); Vega (GCN) runs only 64-wide waves, so each workgroup
   holds a wave with half its lanes idle, and the log says "programs translated for 64 lanes, 32-lane warps emulated". Design: the
   fallback's mesh compute pipeline gets 64 threads; thread t works for workgroup 2*w + t/32 with local id t%32; the stage buffer
   (isbe_attributes, the map) and every workgroup array get a second half indexed by t/32; the slot, the primitive count and the
   allocator handoff become per half; barriers stay workgroup-wide, so a program whose barriers are not in uniform control flow
   across both halves must stay single (decide per program at translation: no barrier under a branch on workgroup data); the
   dispatch halves (rounded up; the odd last workgroup's upper half writes nothing). Bound: about half of the mesh stages, 4-5 ms
   (~10% of the frame). 2-3 days in emit_spirv_mesh.cpp and the dispatch.

1. **Batch the no-mesh fallback's draws in a pass** (the biggest structural item). Today each fallback mesh draw ends the pass,
   4 full barriers, its compute, then the pass again: ~49 splits a frame here, 28 of them one-draw shadow passes. Recording the
   pass's draws into a secondary command buffer while their compute goes before the pass (one records pair per draw of the batch,
   from a ring) would make one pass of each run. Bound: the whole fallback is 5.5 ms of 59.6 docked (N2, nomesh notes); list 3's SN
   arm gives the bound at the low-end set. Estimate 2-3 days (the parallel replay's segments, the records memory: a pair is up to
   ~23 MB, so a ring must be sized by what the draws output, not the worst case).
2. **Attachments in GENERAL layout everywhere** (`vk_draw.cpp` begin_pass: every colour and depth attachment GENERAL, LOAD/STORE;
   colour images MUTABLE_FORMAT + STORAGE). On AMD that likely keeps DCC/HTILE compression off for most targets, which costs
   bandwidth on a shared-memory APU. Not measured; needs per-image layout tracking. Estimate a week; risky for every GPU.
3. **The GPU's empty gaps between buffers** (9-16 ms a frame): the GPU waits for the next recording. With the wait (WAIT_OWN=1) the
   CPU and GPU take turns; the mirror instead lowers them to ~9 ms. Overlap beyond that is the DECOUPLE stream's order thread / lanes.
4. **Upload bursts while turning** (GPU uploads 5-16 ms a frame, the CPU unswizzle of the game's BC textures): an unswizzle of BC
   into a buffer then a copy on the GPU (TEXCACHE's lead (a)); the small BC1/BC4 uploads that take 30-300 ms for a few hundred KB
   are stalls, cause not found (not paging: T0/T1 of the nomesh notes).
5. **Grass / foliage density**: the one-draw grass passes cost ~0.7 ms each even at 512x288; no density or distance control is
   known (GPU11: only the task shaders' "draw everything" bit).
