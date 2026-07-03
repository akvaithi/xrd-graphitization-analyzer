using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Windows.Storage.Pickers;
using XRDAnalyzer.ViewModels;
using XRDAnalyzer.Views;

namespace XRDAnalyzer;

public sealed partial class MainWindow : Window
{
    private readonly AppViewModel _app;

    public MainWindow(AppViewModel app)
    {
        InitializeComponent();

        // Extend content into the title bar and use the TabView's
        // drag region so the tab strip acts as the title bar area.
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(CustomDragRegion);
        AppWindow.SetIcon("Assets/AppIcon.ico");

        // Reserve space at the right edge of the tab strip for the system
        // caption buttons, scaled to the current DPI so they don't overlap
        // tabs on high-DPI displays.
        AppWindow.Changed += OnAppWindowChanged;
        UpdateCaptionButtonInset();

        _app = app;
        _app.PropertyChanged += (_, _) => UpdateBatchBanner();
        _app.Files.CollectionChanged += (_, _) => UpdateBatchBanner();
        var analyzePage = new AnalyzePage(_app) { HostWindow = this };
        AnalyzeTab.Content = analyzePage;
        CompareTab.Content = new ComparePage(_app);
        StackTab.Content = new StackPage(_app);
        ManualTab.Content = new ManualPage();
        YieldTab.Content = new YieldPage(_app);
        TabControl.SelectedItem = AnalyzeTab;
        UpdateBatchBanner();
    }

    private async void SuggestAllButton_Click(object sender, RoutedEventArgs e)
    {
        if (_app.BatchBusy || _app.Files.Count == 0) return;
        await _app.SuggestAllAiAsync();
    }

    private async void ExportAllButton_Click(object sender, RoutedEventArgs e)
    {
        if (_app.BatchBusy || _app.Files.Count == 0) return;
        var picker = new FolderPicker();
        picker.FileTypeFilter.Add("*");
        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(this);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, hwnd);
        var folder = await picker.PickSingleFolderAsync();
        if (folder is null) return;
        await _app.ExportAllAsync(folder.Path);
    }

    private void DismissBatchButton_Click(object sender, RoutedEventArgs e) => _app.BatchNote = null;

    private void UpdateBatchBanner()
    {
        SuggestAllButton.IsEnabled = !_app.BatchBusy && _app.Files.Count > 0;
        ExportAllButton.IsEnabled = !_app.BatchBusy && _app.Files.Count > 0;
        BatchBanner.Visibility = _app.BatchBusy || _app.BatchNote is not null ? Visibility.Visible : Visibility.Collapsed;
        BatchProgressBar.Visibility = _app.BatchBusy ? Visibility.Visible : Visibility.Collapsed;
        BatchProgressBar.Value = _app.BatchProgress;
        BatchNoteText.Text = _app.BatchNote ?? "Working…";
        DismissBatchButton.Visibility = _app.BatchBusy ? Visibility.Collapsed : Visibility.Visible;
    }

    private void OnAppWindowChanged(AppWindow sender, AppWindowChangedEventArgs args)
    {
        if (args.DidSizeChange || args.DidPositionChange)
        {
            UpdateCaptionButtonInset();
        }
    }

    private void UpdateCaptionButtonInset()
    {
        // RightInset is in physical pixels; convert to DIPs.
        double scale = (Content as FrameworkElement)?.XamlRoot?.RasterizationScale ?? 1.0;
        if (scale <= 0)
        {
            scale = 1.0;
        }

        CustomDragRegion.MinWidth = AppWindow.TitleBar.RightInset / scale;
    }
}
