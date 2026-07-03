using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using ScottPlot;
using Windows.Storage.Pickers;
using XRDAnalyzer.Engine.Models;
using XRDAnalyzer.Models;
using XRDAnalyzer.ViewModels;

namespace XRDAnalyzer.Views;

/// Overlay/waterfall of raw intensities. Mirrors `StackView`
/// (native/Sources/XRDApp/StackView.swift). Windowing/baseline-subtract/
/// downsampling here are chart-display-only massaging of raw points (the
/// same split as macOS, where this logic lives in the UI layer, not
/// XRDCore) — no DG/crystallinity/yield math is touched.
public sealed partial class StackPage : Page
{
    private static readonly Color[] Palette =
    [
        Color.FromHex("#FF3B30"), Color.FromHex("#30B0C7"), Color.FromHex("#5E5CE6"),
        Color.FromHex("#34C759"), Color.FromHex("#FF9500"), Color.FromHex("#AF52DE"),
        Color.FromHex("#FF2D55"), Color.FromHex("#5AC8FA"),
    ];

    private readonly AppViewModel _app;
    private readonly HashSet<Guid> _selected = [];
    private bool _didInit;

    public StackPage(AppViewModel app)
    {
        InitializeComponent();
        _app = app;
        _app.Files.CollectionChanged += (_, _) => Refresh();
    }

    private List<LoadedFile> WithPattern => [.. _app.Files.Where(f => f.Pattern is not null)];

    private void Control_Changed(object sender, object e)
    {
        OffsetValueText.Text = OffsetSlider.Value.ToString("F2");
        UpdateChart();
    }

    private void SelectAllButton_Click(object sender, RoutedEventArgs e) { _selected.Clear(); foreach (var f in WithPattern) _selected.Add(f.Id); Refresh(); }
    private void SelectNoneButton_Click(object sender, RoutedEventArgs e) { _selected.Clear(); Refresh(); }

    private void FileCheckBox_Toggled(object sender, RoutedEventArgs e)
    {
        if (sender is not CheckBox { Tag: LoadedFile file } cb) return;
        if (cb.IsChecked == true) _selected.Add(file.Id);
        else _selected.Remove(file.Id);
        SelectedCountText.Text = $"{_selected.Count} selected";
        UpdateChart();
    }

    private void Refresh()
    {
        var withPattern = WithPattern;
        var hasData = withPattern.Count > 0;
        EmptyStateText.Visibility = hasData ? Visibility.Collapsed : Visibility.Visible;
        ContentGrid.Visibility = hasData ? Visibility.Visible : Visibility.Collapsed;
        if (!hasData) return;

        if (!_didInit)
        {
            foreach (var f in withPattern.Take(8)) _selected.Add(f.Id);
            _didInit = true;
        }

        SelectedCountText.Text = $"{_selected.Count} selected";
        FilesList.Items.Clear();
        foreach (var f in withPattern)
        {
            var cb = new CheckBox { IsChecked = _selected.Contains(f.Id), Tag = f, Content = f.DisplayName, FontSize = 11 };
            cb.Checked += FileCheckBox_Toggled;
            cb.Unchecked += FileCheckBox_Toggled;
            FilesList.Items.Add(cb);
        }
        UpdateChart();
    }

    private void UpdateChart()
    {
        var series = BuildSeries();
        ChartControl.Plot.Clear();
        NoSeriesText.Visibility = series.Count == 0 ? Visibility.Visible : Visibility.Collapsed;

        var zoom = ZoomToggle.IsOn;
        double xlo = zoom ? 24.0 : 20.0, xhi = zoom ? 30.0 : 60.0;
        double yhi = 1, ylo = 0;

        if (series.Count > 0)
        {
            for (var i = 0; i < series.Count; i++)
            {
                var (name, xs, ys) = series[i];
                var line = ChartControl.Plot.Add.Scatter(xs, ys);
                line.Color = Palette[i % Palette.Length];
                line.MarkerSize = 0;
                line.LineWidth = 1.2f;
                if (series.Count <= 10) line.LegendText = name;
            }
            var allY = series.SelectMany(s => s.Y).ToList();
            yhi = allY.Count > 0 ? allY.Max() : 1;
            ylo = Math.Min(0, allY.Count > 0 ? allY.Min() : 0);
        }

        ChartControl.Plot.Axes.SetLimitsX(xlo, xhi);
        ChartControl.Plot.Axes.SetLimitsY(ylo, yhi * 1.04 + 1e-6);
        ChartControl.Plot.Axes.Bottom.Label.Text = "2θ  (degrees)";
        ChartControl.Plot.Axes.Left.Label.Text = "Intensity  (a.u.)" + (OffsetSlider.Value > 0 ? "  — offset" : "");
        if (series.Count is > 0 and <= 10) ChartControl.Plot.ShowLegend(Alignment.UpperLeft);
        ChartControl.Refresh();
    }

    private List<(string Name, double[] X, double[] Y)> BuildSeries()
    {
        var zoom = ZoomToggle.IsOn;
        var baseline = BaselineToggle.IsOn;
        double lo = zoom ? 24.0 : 20.0, hi = zoom ? 30.0 : 60.0;

        var prepared = new List<(string Name, double[] X, double[] Y)>();
        double gmax = 0;
        foreach (var f in WithPattern.Where(f => _selected.Contains(f.Id)))
        {
            if (f.Pattern is not { } p) continue;
            var (x, y) = Window(p, lo, hi);
            if (x.Count == 0) continue;
            if (baseline && x.Count > 1)
            {
                var b0 = y[0];
                var b1 = y[^1];
                var xSpan = Math.Max(x[^1] - x[0], 1e-9);
                for (var j = 0; j < y.Count; j++)
                {
                    var t = (x[j] - x[0]) / xSpan;
                    y[j] = Math.Max(y[j] - (b0 + (b1 - b0) * t), 0);
                }
            }
            var stride = Math.Max(1, x.Count / 400);
            var xs = stride > 1 ? x.Where((_, idx) => idx % stride == 0).ToArray() : [.. x];
            var ys = stride > 1 ? y.Where((_, idx) => idx % stride == 0).ToArray() : [.. y];
            gmax = Math.Max(gmax, ys.Length > 0 ? ys.Max() : 0);
            prepared.Add((f.DisplayName, xs, ys));
        }

        var step = OffsetSlider.Value * gmax;
        var outSeries = new List<(string, double[], double[])>();
        for (var i = 0; i < prepared.Count; i++)
        {
            var (name, xs, ys) = prepared[i];
            var offsetY = ys.Select(v => v + i * step).ToArray();
            outSeries.Add((name, xs, offsetY));
        }
        return outSeries;
    }

    private static (List<double> X, List<double> Y) Window(PatternDto p, double lo, double hi)
    {
        var xs = new List<double>();
        var ys = new List<double>();
        for (var i = 0; i < p.TwoTheta.Length; i++)
        {
            if (p.TwoTheta[i] < lo || p.TwoTheta[i] > hi) continue;
            xs.Add(p.TwoTheta[i]);
            ys.Add(p.Intensity[i]);
        }
        return (xs, ys);
    }

    private async void ChartPngButton_Click(object sender, RoutedEventArgs e)
    {
        var picker = new FileSavePicker();
        picker.FileTypeChoices.Add("PNG image", [".png"]);
        picker.SuggestedFileName = "xrd_stack";
        if (App.MainWindowInstance is { } window)
        {
            var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(window);
            WinRT.Interop.InitializeWithWindow.Initialize(picker, hwnd);
        }
        var file = await picker.PickSaveFileAsync();
        if (file is null) return;
        ChartControl.Plot.SavePng(file.Path, 1200, 800);
    }
}
