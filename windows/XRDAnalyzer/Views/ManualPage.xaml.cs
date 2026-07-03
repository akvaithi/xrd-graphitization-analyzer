using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using XRDAnalyzer.Engine;
using XRDAnalyzer.Engine.Models;

namespace XRDAnalyzer.Views;

/// Manual DG calculator — hand-entered Origin fit peaks. Mirrors `ManualView`
/// (native/Sources/XRDApp/ManualView.swift); all math goes through `xrd_manual`.
public sealed partial class ManualPage : Page
{
    public ManualPage() => InitializeComponent();

    private void TwoPeaksToggle_Toggled(object sender, RoutedEventArgs e) =>
        TurboPanel.Visibility = TwoPeaksToggle.IsOn ? Visibility.Visible : Visibility.Collapsed;

    private void CalculateButton_Click(object sender, RoutedEventArgs e)
    {
        ErrorText.Visibility = Visibility.Collapsed;
        if (double.IsNaN(Xc1Box.Value) || double.IsNaN(A1Box.Value))
        {
            ShowError("Enter numeric xc and area.");
            return;
        }
        var peaks = new List<ManualPeakDto> { new() { Xc = Xc1Box.Value, Area = A1Box.Value } };
        if (TwoPeaksToggle.IsOn)
        {
            if (double.IsNaN(Xc2Box.Value) || double.IsNaN(A2Box.Value))
            {
                ShowError("Enter numeric xc and area for both peaks.");
                return;
            }
            peaks.Add(new ManualPeakDto { Xc = Xc2Box.Value, Area = A2Box.Value });
        }

        var response = XrdEngine.Manual(new ManualRequest { Peaks = [.. peaks] });
        if (response.Error is not null || response.Result is not { } r)
        {
            ShowError(response.Error ?? "calculation failed");
            return;
        }
        ShowResult(r);
    }

    private void ShowError(string message)
    {
        ErrorText.Text = message;
        ErrorText.Visibility = Visibility.Visible;
    }

    private void ShowResult(ManualResultDto r)
    {
        EmptyStateText.Visibility = Visibility.Collapsed;
        ResultPanel.Visibility = Visibility.Visible;
        DgValueText.Text = $"{r.DgPercent:F2} %";
        MethodText.Text = r.NPeaks == 1 ? "single peak · Maire–Mering" : "area-weighted · Maire–Mering";

        ResultRowsPanel.Children.Clear();
        ResultRowsPanel.Children.Add(BuildSection("Graphitic",
        [
            ("2θ", $"{r.GraphiticXc:F4}°"), ("area", $"{r.GraphiticArea:F4}"), ("d-spacing", $"{r.GraphiticD:F6} Å"),
        ]));
        if (r.TurbostraticXc is { } txc && r.TurbostraticArea is { } ta && r.TurbostraticD is { } td)
        {
            ResultRowsPanel.Children.Add(BuildSection("Turbostratic",
            [
                ("2θ", $"{txc:F4}°"), ("area", $"{ta:F4}"), ("d-spacing", $"{td:F6} Å"),
            ]));
            ResultRowsPanel.Children.Add(BuildSection("Weighted",
            [
                ("Xg / Xt", $"{(r.AreaFractionGraphitic ?? 0) * 100:F1}% / {(r.AreaFractionTurbostratic ?? 0) * 100:F1}%"),
                ("d′", $"{r.DPrime:F6} Å"),
            ]));
        }
    }

    private static UIElement BuildSection(string title, (string Label, string Value)[] items)
    {
        var stack = new StackPanel { Spacing = 4 };
        stack.Children.Add(new TextBlock
        {
            Text = title.ToUpperInvariant(),
            FontSize = 11,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            Opacity = 0.7,
        });
        foreach (var (label, value) in items)
        {
            var row = new Grid { ColumnDefinitions = { new ColumnDefinition(), new ColumnDefinition { Width = GridLength.Auto } } };
            row.Children.Add(new TextBlock { Text = label, Opacity = 0.7, FontSize = 13 });
            var valueText = new TextBlock { Text = value, FontSize = 13, FontWeight = Microsoft.UI.Text.FontWeights.Medium };
            Grid.SetColumn(valueText, 1);
            row.Children.Add(valueText);
            stack.Children.Add(row);
        }
        return stack;
    }
}
