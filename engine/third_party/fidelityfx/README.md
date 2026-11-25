# AMD FSR API headers (FidelityFX SDK v2.4.0)

Unmodified MIT-licensed headers from AMD's FidelityFX SDK, tag `v2.4.0`
(<https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK>, `Kits/FidelityFX/api/include` and
`Kits/FidelityFX/upscalers/include`). See `LICENSE.md`.

`framegeneration/include/ffx_framegeneration.h` and `ffx_framegeneration_api_types.h` (frame generation 4.0.2, `Kits/FidelityFX/framegeneration/include`)
are the same SDK's, for `gpu/vk/vk_fsr4fg.cpp`; the only change is the two `#include` lines, which point into this folder's `api/include`.
AMD's `amd_fidelityfx_framegeneration_vk.dll` (36.8 MB, `Kits/FidelityFX/signedbin`) is not vendored and not fetched by `scripts/ffx_sdk.py` yet
(its SHA-256 is to be pinned from AMD's repository first): beside the loader, it is what FSR 4 frame generation needs.

Only the headers are compiled in (`gpu/vk/vk_fsr4.cpp`, in a build with `HOOKSHOT_FSR=1`). AMD's signed
`amd_fidelityfx_loader_vk.dll` and `amd_fidelityfx_upscaler_vk.dll` (from `Kits/FidelityFX/signedbin` of the same release)
are not vendored: they are loaded at run time, from `RX_VK_FSR_DIR` or the folder of `hookshot.exe`, when the Upscaler row
is on AMD FSR 4.
