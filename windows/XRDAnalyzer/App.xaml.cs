// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

using System.Diagnostics;
using Microsoft.UI.Xaml;
using Microsoft.Windows.AppLifecycle;
using Windows.ApplicationModel.Activation;
using XRDAnalyzer.ViewModels;

namespace XRDAnalyzer;

/// <summary>
/// Provides application-specific behavior to supplement the default Application class.
/// </summary>
public partial class App : Application
{
    private Window? _window;
    private AppViewModel? _appViewModel;

    /// The single top-level window — pages use this to initialize file
    /// pickers (which need an HWND) without threading it through every
    /// constructor.
    public static Window? MainWindowInstance { get; private set; }

    /// <summary>
    /// Initializes the singleton application object.
    /// </summary>
    public App()
    {
        InitializeComponent();
    }

    /// <summary>
    /// Invoked when the application is launched.
    /// </summary>
    /// <param name="args">Details about the launch request and process.</param>
    protected override void OnLaunched(Microsoft.UI.Xaml.LaunchActivatedEventArgs args)
    {
        // Single-instance: a second launch (e.g. double-clicking another .xy
        // once file association is wired up via Package.appxmanifest, once
        // MSIX-packaged) hands its activation to the first instance and
        // exits, instead of opening a second window — mirrors the macOS
        // AppDelegate "focus the one window, don't spawn" behavior. Works
        // for both packaged and unpackaged launches.
        var activatedArgs = AppInstance.GetCurrent().GetActivatedEventArgs();
        var mainInstance = AppInstance.FindOrRegisterForKey("XRDAnalyzer-main");
        if (!mainInstance.IsCurrent)
        {
            mainInstance.RedirectActivationToAsync(activatedArgs).AsTask().Wait();
            Process.GetCurrentProcess().Kill();
            return;
        }
        mainInstance.Activated += (_, redirectedArgs) =>
            _window?.DispatcherQueue.TryEnqueue(() => HandleActivation(redirectedArgs));

        _appViewModel = new AppViewModel();
        _window = new MainWindow(_appViewModel);
        MainWindowInstance = _window;
        _window.Activate();

        // Files passed on the command line (double-click via a non-MSIX file
        // association, or `XRDAnalyzer.exe file1.xy file2.xy`) — mirrors
        // `AppModel.openLaunchArguments`. `GetCommandLineArgs` already
        // handles quoted paths with spaces correctly.
        var launchPaths = Environment.GetCommandLineArgs().Skip(1)
            .Where(p => File.Exists(p) && Path.GetExtension(p).Equals(".xy", StringComparison.OrdinalIgnoreCase))
            .ToList();
        if (launchPaths.Count > 0) _appViewModel.Open(launchPaths);

        HandleActivation(activatedArgs);
    }

    /// Opens any `.xy` files carried by a (possibly redirected) activation —
    /// the packaged-app equivalent of `openLaunchArguments`, driven by real
    /// file activation instead of argv.
    private void HandleActivation(AppActivationArguments activatedArgs)
    {
        if (activatedArgs is { Kind: ExtendedActivationKind.File, Data: IFileActivatedEventArgs fileArgs })
        {
            var paths = fileArgs.Files.OfType<Windows.Storage.IStorageFile>().Select(f => f.Path).ToList();
            if (paths.Count > 0) _appViewModel?.Open(paths);
        }
    }
}
