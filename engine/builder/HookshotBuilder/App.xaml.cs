// Copyright (C) 2026 Sacrappy

using System.Windows;

namespace HookshotBuilder;

public partial class App : Application
{
    /// --cli <make_game.py's arguments>: the build without a window (HeadlessCli.cs), for RecompLauncher. No window is made
    protected override void OnStartup(StartupEventArgs e)
    {
        if (e.Args.Length > 0 && e.Args[0] == "--cli")
        {
            Environment.Exit(HeadlessCli.Run(e.Args[1..]));
            return;
        }
        base.OnStartup(e);
    }
}
