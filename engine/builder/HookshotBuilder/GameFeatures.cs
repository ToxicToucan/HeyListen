// Copyright (C) 2026 Sacrappy

using System.IO;
using System.Text;

namespace HookshotBuilder;

/// What a game folder's renderer (hookshot_vk.dll) was built with, as nx2/make_game.py leaves it: NVIDIA DLSS is compiled in only
/// when the build had NVIDIA's SDK (the tick), AMD FSR 4 only with --fsr. Ticks changed after the build mean the folder is built
/// again (issue 130: a game built without DLSS, the box ticked later, "Play" and no DLSS in the menu)
static class GameFeatures
{
    /// make_game.py's NGX_MARK and FSR_MARK: in the folder when the build has the feature
    public const string DlssMark = ".hookshot_dlss";
    public const string FsrMark = ".hookshot_fsr";

    /// Text only a renderer built with NVIDIA's SDK has (gpu/vk/vk_dlss.cpp's log line when NGX starts)
    static readonly byte[] DlssText = Encoding.ASCII.GetBytes("NVIDIA DLSS: NGX started");

    public static bool HasDlss(string folder)
    {
        try
        {
            if (File.Exists(Path.Combine(folder, DlssMark))) return true;
            // (a folder built before the mark: its runtime may be there, with a renderer that has no DLSS, as in issue 130; only the
            // renderer itself says. A build without DLSS leaves no runtime, so nothing is read then)
            if (!File.Exists(Path.Combine(folder, "ngx", "nvngx_dlss.dll"))) return false;
            foreach (var renderer in new[] { Path.Combine(folder, "bin", "hookshot_vk.dll"), Path.Combine(folder, "hookshot_vk.dll") })
                if (File.Exists(renderer)) return Contains(renderer, DlssText);
        }
        catch (Exception) { }
        return false;
    }

    public static bool HasFsr(string folder) => File.Exists(Path.Combine(folder, FsrMark));

    /// What the ticks ask that the folder does not have, in words ("with NVIDIA DLSS", "without AMD FSR 4"); empty: nothing
    public static List<string> Changes(string folder, bool dlss, bool fsr)
    {
        var changes = new List<string>();
        if (dlss != HasDlss(folder)) changes.Add((dlss ? "with" : "without") + " NVIDIA DLSS");
        if (fsr != HasFsr(folder)) changes.Add((fsr ? "with" : "without") + " AMD FSR 4");
        return changes;
    }

    /// Whether the file holds the bytes (read through, a block at a time)
    static bool Contains(string path, byte[] needle)
    {
        using var f = File.OpenRead(path);
        var buffer = new byte[(1 << 20) + needle.Length];
        var kept = 0;
        while (true)
        {
            var n = f.Read(buffer, kept, buffer.Length - kept);
            if (n <= 0) return false;
            var total = kept + n;
            if (buffer.AsSpan(0, total).IndexOf(needle) >= 0) return true;
            kept = Math.Min(needle.Length - 1, total);
            Array.Copy(buffer, total - kept, buffer, 0, kept);
        }
    }
}
