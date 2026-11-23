// Copyright (C) 2026 Sacrappy

using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

namespace HookshotBuilder;

/// HeyListenBuilder.exe --cli <make_game.py's arguments>: the build without a window, what RecompLauncher's setup step runs
/// (recomplauncher.json "prepare"; the same arguments as the AppImage's --cli). make_game.py's lines go to this program's output
/// as they come ("progress 0.4 Compiling" with --prepare), and the exit code is 0 when the game was built. If this program is
/// stopped, what it started stops with it.
static class HeadlessCli
{
    public static int Run(string[] args)
    {
        KillWithMe();
        var tools = Tools.Find();
        if (tools == null)
        {
            Console.Error.WriteLine("error: The build tools are not in HeyListen's folder (the tools folder beside this program).");
            return 2;
        }
        var list = args.ToList();
        // (as the window does: the build files in the builder's own folder, NVIDIA's SDK in its own, only when asked)
        if (!list.Contains("--work")) list.AddRange(new[] { "--work", Storage.Work });
        var runner = new Runner(tools, list, null, list.Contains("--fetch-ngx-sdk") ? Storage.NgxSdk : null,
                                list.Contains("--fetch-fsr-runtime") ? Storage.FfxSdk : null);
        string? failure = null;
        var said = false;   // (make_game.py already said why it failed: "error: ...")
        using var done = new ManualResetEventSlim();
        runner.Line += line => { lock (done) { Console.Out.WriteLine(line); said |= line.StartsWith("error: "); } };
        runner.Finished += f => { failure = f; done.Set(); };
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; runner.Cancel(); };
        runner.Start();
        done.Wait();
        Console.Out.Flush();
        if (failure == null) return 0;
        if (!said) Console.Error.WriteLine("error: " + failure);
        return 1;
    }

    /// This program in a job that ends its processes when it ends (a launcher that stops the setup step ends only this one:
    /// the compilers it started would go on for the rest of the build)
    static void KillWithMe()
    {
        try
        {
            var job = CreateJobObject(IntPtr.Zero, null);
            var info = new ExtendedLimits { Basic = new BasicLimits { LimitFlags = 0x2000 } };   // KILL_ON_JOB_CLOSE
            var size = Marshal.SizeOf<ExtendedLimits>();
            var mem = Marshal.AllocHGlobal(size);
            try
            {
                Marshal.StructureToPtr(info, mem, false);
                if (SetInformationJobObject(job, 9, mem, (uint)size)) AssignProcessToJobObject(job, Process.GetCurrentProcess().Handle);
            }
            finally { Marshal.FreeHGlobal(mem); }
        }
        catch (Exception) { }
    }

    [StructLayout(LayoutKind.Sequential)]
    struct BasicLimits
    {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct IoCounters { public ulong A, B, C, D, E, F; }

    [StructLayout(LayoutKind.Sequential)]
    struct ExtendedLimits
    {
        public BasicLimits Basic;
        public IoCounters Io;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr attributes, string? name);
    [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint size);
    [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
}
