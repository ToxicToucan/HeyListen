// Copyright (C) 2026 Sacrappy

using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace HookshotBuilder;

/// Code (exeFS) mods: the mods in the game folder's mods folder that carry code patches (.ips, .pchtxt), what the build does with them
/// (nx2/exefs_mods.py patches the game's modules before the translation) and the window that lists them. The player's choice is
/// make_game.py's --code-mods on|off; the game folder remembers it, so the game's update keeps it.
static class CodeMods
{
    /// What the rebuild costs, said before it starts (measured on a Ryzen 7 7800X3D, 14 build workers; slower PCs take longer)
    public const string RebuildAbout =
        "The whole game is translated to PC code again, then only the changed parts are compiled: about 15 minutes on a fast PC " +
        "(measured: 9 minutes of translation, 6 of compiling), more on a slower one; the build screen shows the time. Taking a code mod out later costs the same.";

    public const string Risk =
        "Code patches change the game's own instructions. They can crash the game, corrupt a save or stop working after a game update: " +
        "keep a copy of your saves first (Export saves). A patch made for another game version is refused, and the build stops with the " +
        "mod's name. Where a patch changes a function the native engine replaces in part, that part falls back to the translated code " +
        "(the build log says \"NOT gated\" for it). RomFS mods need none of this.";

    public sealed record Patch(string File, string Kind, string State, string? Module, int Records, int Bytes, string Note);
    public sealed record Skipped(string File, string Reason);
    public sealed record Mod(string Name, bool On, List<Patch> Patches, List<Skipped> Skipped);

    public static string ModsFolder(string folder) =>
        Environment.GetEnvironmentVariable("HOS_MODS") is { Length: > 0 } m ? m : Path.Combine(folder, "mods");

    public static bool IsOn(string folder)
    {
        try { return File.ReadAllText(Path.Combine(folder, ".hookshot_code_mods")).Trim() == "on"; }
        catch (Exception) { return false; }
    }

    /// The mods with code patches, as nx2/exefs_mods.py --list says them; null when the tools could not answer. The modules' build ids
    /// come from the game files the folder keeps (.hookshot_exefs), so a patch made for another version is told apart
    public static async Task<List<Mod>?> List(Tools tools, string folder)
    {
        try
        {
            var si = new ProcessStartInfo(tools.Python)
            {
                WorkingDirectory = tools.Root, UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true,
                StandardOutputEncoding = Encoding.UTF8,
            };
            foreach (var a in tools.PythonArgs ?? Array.Empty<string>()) si.ArgumentList.Add(a);
            foreach (var a in new[] { "-X", "utf8", "-I", Path.Combine(tools.Root, "nx2", "exefs_mods.py"), "--list", ModsFolder(folder),
                                      "--exefs", Path.Combine(folder, ".hookshot_exefs") })
                si.ArgumentList.Add(a);
            using var p = Process.Start(si)!;
            var text = await p.StandardOutput.ReadToEndAsync();
            await p.WaitForExitAsync();
            if (p.ExitCode != 0) return null;
            using var doc = JsonDocument.Parse(text);
            var mods = new List<Mod>();
            foreach (var m in doc.RootElement.EnumerateArray())
            {
                var patches = new List<Patch>();
                foreach (var q in m.GetProperty("patches").EnumerateArray())
                    patches.Add(new Patch(q.GetProperty("file").GetString() ?? "", q.GetProperty("kind").GetString() ?? "",
                                          q.GetProperty("state").GetString() ?? "", q.GetProperty("module").GetString(),
                                          q.GetProperty("records").GetInt32(), q.GetProperty("bytes").GetInt32(), q.GetProperty("note").GetString() ?? ""));
                var skipped = new List<Skipped>();
                if (m.TryGetProperty("skipped", out var sk))
                    foreach (var q in sk.EnumerateArray())
                        skipped.Add(new Skipped(q.GetProperty("file").GetString() ?? "", q.GetProperty("reason").GetString() ?? ""));
                mods.Add(new Mod(m.GetProperty("name").GetString() ?? "", m.GetProperty("on").GetBoolean(), patches, skipped));
            }
            return mods;
        }
        catch (Exception) { return null; }
    }

    static string State(Mod m, Patch p) =>
        !m.On ? "off (its folder's name starts with - or _)"
        : p.State == "ok" ? $"for {p.Module}: {p.Records} record(s), {p.Bytes} byte(s)" + (p.Note.Length > 0 ? " (" + p.Note + ")" : "")
        : "REFUSED (the build stops until it is turned off): " + p.Note;

    static Brush Res(Window w, string key, Brush fallback) => w.TryFindResource(key) as Brush ?? fallback;

    /// The window: the mods found, each patch with its state, the risks, the time. Returns what the player chose: null (closed),
    /// or (rebuild, on): rebuild the game now with the option on (or off, to take the patches out). canRebuild false (the builder's
    /// options page, no game yet): the list only, the option is the page's own tick
    public static (bool On, bool Rebuild)? Show(Window owner, List<Mod>? mods, string folder, bool canRebuild)
    {
        var ink = Res(owner, "Ink", Brushes.White);
        var soft = Res(owner, "InkSoft", Brushes.LightGray);
        var warn = Res(owner, "Warn", Brushes.Orange);
        var win = new Window
        {
            Title = "Code mods (exeFS)", Owner = owner, WindowStartupLocation = WindowStartupLocation.CenterOwner,
            Width = 640, SizeToContent = SizeToContent.Height, MaxHeight = 720, ResizeMode = ResizeMode.NoResize,
            Background = Res(owner, "Page", Brushes.Black), Foreground = ink,
        };
        var stack = new StackPanel { Margin = new Thickness(24) };
        TextBlock Text(string s, Brush? b = null, double size = 13, double top = 8, FontWeight? weight = null) => new()
        {
            Text = s, Foreground = b ?? soft, FontSize = size, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, top, 0, 0),
            FontWeight = weight ?? FontWeights.Normal,
        };
        stack.Children.Add(Text("Code mods (exeFS)", ink, 20, 0, FontWeights.Bold));
        stack.Children.Add(Text("Put a mod in the mods folder of the game folder, in its own folder with an exefs folder inside: " +
                                "mods\\<name>\\exefs\\<build id>.ips, or any name ending in .pchtxt. " + ModsFolder(folder)));
        stack.Children.Add(Text(Risk, warn, 12.5, 12));
        var list = new StackPanel { Margin = new Thickness(0, 14, 0, 0) };
        if (mods == null) list.Children.Add(Text("The mods could not be listed (the build tools did not answer).", warn));
        else if (mods.Count == 0) list.Children.Add(Text("No mod with exefs files (code patches .ips or .pchtxt) was found."));
        else
            foreach (var m in mods)
            {
                list.Children.Add(Text(m.Name + (m.On ? "" : "  (off)"), ink, 14, 10, FontWeights.SemiBold));
                foreach (var p in m.Patches)
                    list.Children.Add(Text($"{p.File}: " + State(m, p), p.State == "ok" || !m.On ? soft : warn, 12.5, 2));
                foreach (var k in m.Skipped)
                    list.Children.Add(Text($"{k.File}: NOT USED. " + k.Reason, warn, 12.5, 2));
                if (m.On && m.Skipped.Any(k => k.File.StartsWith("subsdk", StringComparison.OrdinalIgnoreCase) || k.File == "sdk" || k.File == "rtld"))
                    list.Children.Add(Text("This mod needs a run-time code module, which a recompiled game cannot run: its patches may do nothing, or not what the mod promises.", warn, 12.5, 2));
            }
        stack.Children.Add(new ScrollViewer { Content = list, MaxHeight = 280, VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
        var on = IsOn(folder);
        var tick = new CheckBox { Content = "Apply the code mods of this folder when the game is built", IsChecked = on, Foreground = ink, Margin = new Thickness(0, 16, 0, 0) };
        (bool On, bool Rebuild)? result = null;
        if (canRebuild)
        {
            stack.Children.Add(tick);
            stack.Children.Add(Text(RebuildAbout, soft, 12.5, 8));
            var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 18, 0, 0) };
            var rebuild = new Button { Content = "Rebuild the game now", Padding = new Thickness(18, 8, 18, 8), Margin = new Thickness(0, 0, 10, 0) };
            var close = new Button { Content = "Close", Padding = new Thickness(18, 8, 18, 8) };
            rebuild.Click += (_, _) =>
            {
                var text = tick.IsChecked == true
                    ? "The game will be rebuilt with these code mods. " + RebuildAbout + "\n\nContinue?"
                    : "The game will be rebuilt without code mods. " + RebuildAbout + "\n\nContinue?";
                if (MessageBox.Show(win, text, "HeyListen", MessageBoxButton.YesNo, MessageBoxImage.Warning) != MessageBoxResult.Yes) return;
                result = (tick.IsChecked == true, true);
                win.Close();
            };
            close.Click += (_, _) => win.Close();
            buttons.Children.Add(rebuild);
            buttons.Children.Add(close);
            stack.Children.Add(buttons);
        }
        else
        {
            stack.Children.Add(Text("Tick \"Apply code mods\" on the options page to build with them. " + RebuildAbout, soft, 12.5, 12));
            var close = new Button { Content = "Close", Padding = new Thickness(18, 8, 18, 8), HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 18, 0, 0) };
            close.Click += (_, _) => win.Close();
            stack.Children.Add(close);
        }
        win.Content = stack;
        win.ShowDialog();
        return result;
    }
}
