using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using ScottPlot;
using Windows.Storage.Pickers;
using XRDAnalyzer.Models;
using XRDAnalyzer.Services;
using XRDAnalyzer.ViewModels;

namespace XRDAnalyzer.Views;

public enum XMetric { Temperature, Caco3, Time, Fe, Carbon }
public enum YMetric { Dg, Crystallinity, Lc, DPrime, GraphiticXc }
public enum Grouping { CarbonType, Form, Wash, None }

/// Comparison scatter of any metric vs a synthesis parameter, colored by
/// group, with per-run include toggles and CSV export. Mirrors `CompareView`
/// (native/Sources/XRDApp/CompareView.swift).
public sealed partial class ComparePage : Page
{
    private static readonly (XMetric Value, string Label)[] XMetrics =
    [
        (XMetric.Temperature, "Temperature (°C)"), (XMetric.Caco3, "CaCO3"),
        (XMetric.Time, "Dwell (h)"), (XMetric.Fe, "Fe"), (XMetric.Carbon, "Carbon"),
    ];
    private static readonly (YMetric Value, string Label)[] YMetrics =
    [
        (YMetric.Dg, "DG %"), (YMetric.Crystallinity, "Crystallinity %"), (YMetric.Lc, "Lc (Å)"),
        (YMetric.DPrime, "d′ (Å)"), (YMetric.GraphiticXc, "Graphitic 2θ (°)"),
    ];
    private static readonly (Grouping Value, string Label)[] Groupings =
    [
        (Grouping.CarbonType, "Carbon type"), (Grouping.Form, "Form"), (Grouping.Wash, "Wash"), (Grouping.None, "(none)"),
    ];
    private static readonly Color[] Palette =
    [
        Color.FromHex("#FF3B30"), Color.FromHex("#30B0C7"), Color.FromHex("#5E5CE6"),
        Color.FromHex("#34C759"), Color.FromHex("#FF9500"), Color.FromHex("#AF52DE"),
    ];

    private readonly AppViewModel _app;
    private readonly HashSet<Guid> _excluded = [];

    public ComparePage(AppViewModel app)
    {
        InitializeComponent();
        _app = app;
        YMetricCombo.ItemsSource = YMetrics.Select(m => m.Label).ToList();
        XMetricCombo.ItemsSource = XMetrics.Select(m => m.Label).ToList();
        GroupCombo.ItemsSource = Groupings.Select(m => m.Label).ToList();
        YMetricCombo.SelectedIndex = 0;
        XMetricCombo.SelectedIndex = 0;
        GroupCombo.SelectedIndex = 0;
        _app.Files.CollectionChanged += (_, _) => Refresh();
    }

    private List<LoadedFile> Valid => [.. _app.Files.Where(f => _app.CurrentResult(f) is not null && f.Info is not null)];

    private void Metric_SelectionChanged(object sender, SelectionChangedEventArgs e) => UpdateChart();

    private void ShowAllButton_Click(object sender, RoutedEventArgs e) { _excluded.Clear(); Refresh(); }
    private void ShowNoneButton_Click(object sender, RoutedEventArgs e)
    {
        _excluded.Clear();
        foreach (var f in Valid) _excluded.Add(f.Id);
        Refresh();
    }

    private void RunCheckBox_Toggled(object sender, RoutedEventArgs e)
    {
        if (sender is not CheckBox { Tag: LoadedFile file } cb) return;
        if (cb.IsChecked == true) _excluded.Remove(file.Id);
        else _excluded.Add(file.Id);
        UpdateChart();
        RunCountText.Text = $"{Valid.Count - _excluded.Count}/{Valid.Count} shown";
    }

    private void Refresh()
    {
        var valid = Valid;
        var hasData = valid.Count > 0;
        EmptyStateText.Visibility = hasData ? Visibility.Collapsed : Visibility.Visible;
        ContentGrid.Visibility = hasData ? Visibility.Visible : Visibility.Collapsed;
        if (!hasData) return;

        RunCountText.Text = $"{valid.Count - _excluded.Count}/{valid.Count} shown";
        RunList.Items.Clear();
        foreach (var f in valid)
        {
            var subtitle = _app.DgText(f) + (f.Crystallinity is { } c ? $" · cryst {c.CrystallineFraction * 100:F0}%" : "");
            var stack = new StackPanel { Margin = new Thickness(0, 2, 0, 2) };
            var cb = new CheckBox { IsChecked = !_excluded.Contains(f.Id), Tag = f };
            cb.Checked += RunCheckBox_Toggled;
            cb.Unchecked += RunCheckBox_Toggled;
            var content = new StackPanel();
            content.Children.Add(new TextBlock { Text = f.DisplayName, FontSize = 11, TextWrapping = TextWrapping.Wrap });
            content.Children.Add(new TextBlock { Text = subtitle, FontSize = 10, Opacity = 0.7 });
            cb.Content = content;
            stack.Children.Add(cb);
            RunList.Items.Add(stack);
        }
        UpdateChart();
    }

    private void UpdateChart()
    {
        var xm = XMetrics[XMetricCombo.SelectedIndex >= 0 ? XMetricCombo.SelectedIndex : 0];
        var ym = YMetrics[YMetricCombo.SelectedIndex >= 0 ? YMetricCombo.SelectedIndex : 0];
        var grp = Groupings[GroupCombo.SelectedIndex >= 0 ? GroupCombo.SelectedIndex : 0];

        var points = Valid
            .Where(f => !_excluded.Contains(f.Id))
            .Select(f => (File: f, X: XValue(f, xm.Value), Y: YValue(f, ym.Value), Group: GroupValue(f, grp.Value)))
            .Where(p => p.X is not null && p.Y is not null)
            .Select(p => (p.File, X: p.X!.Value, Y: p.Y!.Value, p.Group))
            .ToList();

        ChartControl.Plot.Clear();
        NoPointsText.Visibility = points.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        if (points.Count > 0)
        {
            var groups = points.Select(p => p.Group).Distinct().ToList();
            for (var i = 0; i < groups.Count; i++)
            {
                var color = Palette[i % Palette.Length];
                var groupPoints = points.Where(p => p.Group == groups[i]).ToList();

                // faint trend line: mean y per distinct x, sorted
                var trend = groupPoints.GroupBy(p => p.X).OrderBy(g => g.Key)
                    .Select(g => (X: g.Key, Y: g.Average(p => p.Y))).ToList();
                if (trend.Count > 1)
                {
                    var line = ChartControl.Plot.Add.Scatter(trend.Select(t => t.X).ToArray(), trend.Select(t => t.Y).ToArray());
                    line.Color = color.WithAlpha(0.35);
                    line.MarkerSize = 0;
                    line.LineWidth = 1.5f;
                }

                var scatter = ChartControl.Plot.Add.ScatterPoints(
                    groupPoints.Select(p => p.X).ToArray(), groupPoints.Select(p => p.Y).ToArray());
                scatter.Color = color;
                scatter.MarkerSize = 8;
                if (grp.Value != Grouping.None) scatter.LegendText = groups[i];
            }
            ChartControl.Plot.Axes.Bottom.Label.Text = xm.Label;
            ChartControl.Plot.Axes.Left.Label.Text = ym.Label;
            ChartControl.Plot.Axes.AutoScale();
            if (grp.Value != Grouping.None) ChartControl.Plot.ShowLegend(Alignment.UpperLeft);
        }
        ChartControl.Refresh();
    }

    private double? XValue(LoadedFile f, XMetric m)
    {
        var i = f.Info;
        if (i is null) return null;
        return m switch
        {
            XMetric.Temperature => i.TemperatureC,
            XMetric.Caco3 => i.Caco3Ratio,
            XMetric.Time => i.TimeH,
            XMetric.Fe => i.FeRatio,
            XMetric.Carbon => i.CarbonRatio,
            _ => null,
        };
    }

    private double? YValue(LoadedFile f, YMetric m)
    {
        if (m == YMetric.Crystallinity) return f.Crystallinity?.CrystallineFraction * 100;
        var r = _app.CurrentResult(f);
        if (r is null) return null;
        return m switch
        {
            YMetric.Dg => r.DgPercent,
            YMetric.Lc => r.CrystalliteLc,
            YMetric.DPrime => r.DPrimeWeighted,
            YMetric.GraphiticXc => r.Graphitic.Xc,
            _ => null,
        };
    }

    private static string GroupValue(LoadedFile f, Grouping g)
    {
        if (g == Grouping.None || f.Info is not { } i) return "all";
        return g switch
        {
            Grouping.CarbonType => i.CarbonType ?? "—",
            Grouping.Form => i.Form is { } form ? char.ToUpperInvariant(form[0]) + form[1..] : "—",
            Grouping.Wash => i.Wash is { } wash ? char.ToUpperInvariant(wash[0]) + wash[1..] : "—",
            _ => "all",
        };
    }

    private async void ChartPngButton_Click(object sender, RoutedEventArgs e)
    {
        var picker = new FileSavePicker();
        picker.FileTypeChoices.Add("PNG image", [".png"]);
        picker.SuggestedFileName = "xrd_compare";
        InitWithWindow(picker);
        var file = await picker.PickSaveFileAsync();
        if (file is null) return;
        ChartControl.Plot.SavePng(file.Path, 1200, 800);
    }

    private async void CsvButton_Click(object sender, RoutedEventArgs e)
    {
        var picker = new FileSavePicker();
        picker.FileTypeChoices.Add("CSV", [".csv"]);
        picker.SuggestedFileName = "xrd_runs";
        InitWithWindow(picker);
        var file = await picker.PickSaveFileAsync();
        if (file is null) return;

        var sb = new System.Text.StringBuilder(ReportBuilder.ConsolidatedHeader());
        foreach (var f in Valid)
            sb.Append(ReportBuilder.ConsolidatedRow(f.FileName, f.Info, _app.CurrentResult(f), f.Crystallinity));
        await Windows.Storage.FileIO.WriteTextAsync(file, sb.ToString());
    }

    private void InitWithWindow(object picker)
    {
        if (App.MainWindowInstance is not { } window) return;
        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(window);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, hwnd);
    }
}
