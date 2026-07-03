using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage.Pickers;
using XRDAnalyzer.Models;
using XRDAnalyzer.Services;
using XRDAnalyzer.ViewModels;

namespace XRDAnalyzer.Views;

/// Code-behind for the Analyze tab. Mirrors `ContentView` (file sidebar) +
/// `DetailView` (controls + chart) from native/Sources/XRDApp. WinUI's
/// `x:Bind` doesn't cover every case here cleanly (control visibility
/// toggling, dynamic result rows), so the view refreshes imperatively from
/// `AnalyzeViewModel.PropertyChanged` — simple and easy to reason about
/// at this scale, versus wiring a converter per case.
public sealed partial class AnalyzePage : Page
{
    public AppViewModel App { get; }
    public AnalyzeViewModel Vm { get; }
    public Window? HostWindow { get; set; }

    private bool _isUpdatingFromVm;

    public AnalyzePage(AppViewModel app)
    {
        InitializeComponent();
        App = app;
        Vm = new AnalyzeViewModel(app);
        FilesList.ItemsSource = App.Files;
        Vm.PropertyChanged += (_, _) => UpdateUI();
        UpdateUI();
    }

    private async void OpenButton_Click(object sender, RoutedEventArgs e)
    {
        var picker = new FileOpenPicker { ViewMode = PickerViewMode.List };
        picker.FileTypeFilter.Add(".xy");
        if (HostWindow is not null)
        {
            var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(HostWindow);
            WinRT.Interop.InitializeWithWindow.Initialize(picker, hwnd);
        }
        var files = await picker.PickMultipleFilesAsync();
        if (files.Count == 0) return;
        App.Open(files.Select(f => f.Path));
        if (App.Selection is { } sel) FilesList.SelectedItem = sel;
    }

    private void FilesList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (FilesList.SelectedItem is not LoadedFile file)
        {
            App.Selection = null;
            DetailGrid.Visibility = Visibility.Collapsed;
            EmptyState.Visibility = Visibility.Visible;
            return;
        }
        App.Selection = file;
        DetailGrid.Visibility = Visibility.Visible;
        EmptyState.Visibility = Visibility.Collapsed;

        if (file.Pattern is null)
        {
            FitErrorText.Text = file.ParseError ?? "Couldn't read file.";
            FitErrorText.Visibility = Visibility.Visible;
            return;
        }
        Vm.LoadFile(file);
    }

    private void Page_DragOver(object sender, DragEventArgs e)
    {
        e.AcceptedOperation = e.DataView.Contains(Windows.ApplicationModel.DataTransfer.StandardDataFormats.StorageItems)
            ? Windows.ApplicationModel.DataTransfer.DataPackageOperation.Copy
            : Windows.ApplicationModel.DataTransfer.DataPackageOperation.None;
    }

    private async void Page_Drop(object sender, DragEventArgs e)
    {
        if (!e.DataView.Contains(Windows.ApplicationModel.DataTransfer.StandardDataFormats.StorageItems)) return;
        var items = await e.DataView.GetStorageItemsAsync();
        var paths = items.OfType<Windows.Storage.StorageFile>()
            .Where(f => f.FileType.Equals(".xy", StringComparison.OrdinalIgnoreCase))
            .Select(f => f.Path);
        App.Open(paths);
        if (App.Selection is { } sel) FilesList.SelectedItem = sel;
    }

    private void ResetButton_Click(object sender, RoutedEventArgs e) => Vm.ResetToDefaults();

    private void PeakCountRadio_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        // RadioButtons briefly reports SelectedIndex == -1 while swapping the
        // selected item (deselect-old then select-new as two events) — ignore
        // that transient state so it never gets refit/persisted as PeakCount 0.
        if (_isUpdatingFromVm || PeakCountRadio.SelectedIndex < 0) return;
        Vm.PeakCount = PeakCountRadio.SelectedIndex + 1;
    }

    private void TurboLockedToggle_Toggled(object sender, RoutedEventArgs e)
    {
        if (_isUpdatingFromVm) return;
        Vm.TurboLocked = TurboLockedToggle.IsOn;
    }

    private void TurboCenterBox_ValueChanged(NumberBox sender, NumberBoxValueChangedEventArgs args)
    {
        if (_isUpdatingFromVm || double.IsNaN(args.NewValue)) return;
        Vm.TurboCenter = args.NewValue;
    }

    private void SubtractBgToggle_Toggled(object sender, RoutedEventArgs e)
    {
        if (_isUpdatingFromVm) return;
        Vm.SubtractBg = SubtractBgToggle.IsOn;
    }

    private void CalStdPhaseCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_isUpdatingFromVm) return;
        var phase = CalStdPhaseCombo.SelectedItem as string ?? "off";
        Vm.CalStdPhase = phase == "off" ? "" : phase;
    }

    private void AnchorOnToggle_Toggled(object sender, RoutedEventArgs e)
    {
        if (_isUpdatingFromVm) return;
        Vm.AnchorOn = AnchorOnToggle.IsOn;
    }

    private void AnchorTargetBox_ValueChanged(NumberBox sender, NumberBoxValueChangedEventArgs args)
    {
        if (_isUpdatingFromVm || double.IsNaN(args.NewValue)) return;
        Vm.AnchorTarget = args.NewValue;
    }

    private async void SuggestButton_Click(object sender, RoutedEventArgs e) => await Vm.RunAiSuggestAsync(null, null);

    private async void ChartPngButton_Click(object sender, RoutedEventArgs e)
    {
        if (Vm.Result is not { } r) return;
        var picker = new FileSavePicker();
        picker.FileTypeChoices.Add("PNG image", [".png"]);
        picker.SuggestedFileName = FileNaming.SafeFileName((App.Selection?.DisplayName ?? "export") + " — 002 fit");
        InitWithWindow(picker);
        var file = await picker.PickSaveFileAsync();
        if (file is null) return;

        var cryst = Vm.Crystallinity is { } c ? $" · cryst {c.CrystallineFraction * 100:F0}%" : "";
        ChartControl.Plot.Title($"{App.Selection?.DisplayName}   —   DG {r.DgPercent:F2}%{cryst} · (002) fit");
        ChartControl.Plot.SavePng(file.Path, 1200, 750);
        ChartControl.Plot.Title("");   // restore the on-screen chart (no title)
        ChartControl.Refresh();
    }

    private async void ReportCsvButton_Click(object sender, RoutedEventArgs e)
    {
        if (Vm.Result is not { } r || App.Selection is not { } file) return;
        var picker = new FileSavePicker();
        picker.FileTypeChoices.Add("CSV", [".csv"]);
        picker.SuggestedFileName = FileNaming.SafeFileName(file.DisplayName + " — DG report");
        InitWithWindow(picker);
        var saveFile = await picker.PickSaveFileAsync();
        if (saveFile is null) return;
        var csv = ReportBuilder.Csv(file.DisplayName, file.FileName, r, Vm.DgSpan, Vm.Quality, Vm.Crystallinity);
        await Windows.Storage.FileIO.WriteTextAsync(saveFile, csv);
    }

    private async void ShiftedXyButton_Click(object sender, RoutedEventArgs e)
    {
        if (Vm.Result is not { TwoThetaOffset: not 0 } r || App.Selection is not { Pattern: { } pattern } file) return;
        var picker = new FileSavePicker();
        picker.FileTypeChoices.Add(".xy scan", [".xy"]);
        picker.SuggestedFileName = FileNaming.SafeFileName(file.DisplayName + " — shifted");
        InitWithWindow(picker);
        var saveFile = await picker.PickSaveFileAsync();
        if (saveFile is null) return;

        var sb = new System.Text.StringBuilder();
        sb.Append($"# shifted {r.TwoThetaOffset:+0.0000;-0.0000}° from {file.FileName} on {DateTime.Now:d MMM yyyy, h:mm tt}\n");
        sb.Append("# 2theta\tintensity\n");
        for (var i = 0; i < pattern.TwoTheta.Length; i++)
            sb.Append($"{pattern.TwoTheta[i] + r.TwoThetaOffset:F5}\t{pattern.Intensity[i]:G}\n");
        await Windows.Storage.FileIO.WriteTextAsync(saveFile, sb.ToString());
    }

    private void InitWithWindow(object picker)
    {
        if (HostWindow is null) return;
        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(HostWindow);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, hwnd);
    }

    private void UpdateUI()
    {
        _isUpdatingFromVm = true;
        try
        {
            PeakCountRadio.SelectedIndex = Vm.PeakCount - 1;
            TurboLockedToggle.IsOn = Vm.TurboLocked;
            TurboCenterBox.Value = Vm.TurboCenter;
            TurboCenterBox.Visibility = Vm.PeakCount == 2 && Vm.TurboLocked ? Visibility.Visible : Visibility.Collapsed;
            SubtractBgToggle.IsOn = Vm.SubtractBg;
            CalStdPhaseCombo.SelectedItem = string.IsNullOrEmpty(Vm.CalStdPhase) ? "off" : Vm.CalStdPhase;
            AnchorOnToggle.IsOn = Vm.AnchorOn;
            AnchorTargetBox.Value = Vm.AnchorTarget;
            AnchorTargetBox.Visibility = Vm.AnchorOn ? Visibility.Visible : Visibility.Collapsed;
            ResetButton.IsEnabled = !Vm.IsDefaultSettings;
        }
        finally { _isUpdatingFromVm = false; }

        CalibrationText.Text = Vm is { CalStdPhase.Length: > 0, Calibration.Phase: { } phaseLabel }
            ? Vm.Calibration!.Significant
                ? $"{phaseLabel} {Vm.Calibration.Offset:+0.000;-0.000}° ✓"
                : $"{phaseLabel} {Vm.Calibration.Offset:+0.000;-0.000}° (noise)"
            : "";

        OffsetText.Text = Vm.AnchorOn && Vm.Result is { TwoThetaOffset: not 0 } r0
            ? $"Δ2θ {r0.TwoThetaOffset:+0.000;-0.000}°" : "";

        FitErrorText.Visibility = Vm.FitError is not null ? Visibility.Visible : Visibility.Collapsed;
        FitErrorText.Text = Vm.FitError ?? "";
        FitFailedOverlay.Visibility = Vm.Result is null ? Visibility.Visible : Visibility.Collapsed;

        SuggestButton.IsEnabled = !Vm.AiBusy && App.Selection?.Pattern is not null;
        AiProgressRing.IsActive = Vm.AiBusy;
        AiConfidenceText.Visibility = Vm.AiConfidence is not null ? Visibility.Visible : Visibility.Collapsed;
        AiConfidenceText.Text = Vm.AiConfidence is { } conf ? $"conf {conf * 100:F0}%" : "";
        AiNoteText.Visibility = Vm.AiNote is not null ? Visibility.Visible : Visibility.Collapsed;
        AiNoteText.Text = Vm.AiNote ?? "";

        ChartPngButton.IsEnabled = Vm.Result is not null;
        ReportCsvButton.IsEnabled = Vm.Result is not null;
        ShiftedXyButton.IsEnabled = Vm.Result is { TwoThetaOffset: not 0 };

        UpdateDgCallout();
        UpdateCrystallinityCard();
        UpdateQualityCard();
        UpdateResultRows();
        UpdateChart();
    }

    private void UpdateDgCallout()
    {
        if (Vm.Result is not { } r)
        {
            DgValueText.Text = "—";
            DgSigmaText.Text = "";
            DgMethodText.Text = "";
            DgRangeText.Text = "";
            return;
        }
        DgValueText.Text = $"{r.DgPercent:F2} %";
        DgSigmaText.Text = r.DgSigma is { } sg ? $"± {sg:F2}%" : "";
        DgMethodText.Text = $"{(r.PeakCount == 1 ? "single peak" : "area-weighted")} · R² {r.FitR2:F4}";
        DgRangeText.Text = Vm.DgSpan is { } s && s.High - s.Low > 0.5
            ? $"range {s.Low:F1}–{s.High:F1}% across deconvolution choices" : "";
    }

    private void UpdateCrystallinityCard()
    {
        if (Vm.Crystallinity is not { } c)
        {
            CrystallinityCard.Visibility = Visibility.Collapsed;
            return;
        }
        CrystallinityCard.Visibility = Visibility.Visible;
        var cryst = c.CrystallineFraction * 100;
        var disord = c.DisorderedFraction * 100;
        CrystallinityValueText.Text = $"{cryst:F1}%";
        CrystallinityDetailText.Text =
            $"crystalline graphite {cryst:F1}%  ·  disordered (amorphous + turbostratic) {disord:F1}%  ·  R² {c.FitR2:F4}";
        var diverges = (Vm.Result?.DgPercent ?? 0) >= 90 && c.CrystallineFraction < 0.85;
        CrystallinityWarnText.Visibility = diverges ? Visibility.Visible : Visibility.Collapsed;
        CrystallinityWarnText.Text = diverges
            ? "High DG% but a notable disordered fraction remains — ordering is good, the amount of crystalline carbon is not yet complete."
            : "";
    }

    private void UpdateQualityCard()
    {
        if (Vm.Quality is not { Hits.Length: > 0 } q)
        {
            QualityCard.Visibility = Visibility.Collapsed;
            return;
        }
        QualityCard.Visibility = Visibility.Visible;
        QualityVerdictText.Text = q.Verdict;
        QualityHitsList.ItemsSource = q.Hits
            .Select(h => $"{h.TwoTheta:F2}°  {h.Phase}  {h.RelPct:F1}%  ({h.Level})")
            .ToList();
    }

    private void UpdateResultRows()
    {
        ResultRowsPanel.Children.Clear();
        if (Vm.Result is not { } r) return;

        var graphiticItems = new (string, string)[]
        {
            ("2θ centre", $"{r.Graphitic.Xc:F4}°"), ("FWHM", $"{r.Graphitic.W:F4}°"),
            ("μ", $"{r.Graphitic.Mu:F4}"), ("Area", $"{r.Graphitic.A:F2}"),
            ("d-spacing", $"{r.Graphitic.DSpacing:F6} Å"),
        };
        ResultRowsPanel.Children.Add(BuildSection("Graphitic peak", graphiticItems));

        if (r.Turbostratic is { } t)
        {
            var turboItems = new (string, string)[]
            {
                ("2θ centre", $"{t.Xc:F4}°"), ("FWHM", $"{t.W:F4}°"),
                ("Area", $"{t.A:F2}"), ("d-spacing", $"{t.DSpacing:F6} Å"),
            };
            ResultRowsPanel.Children.Add(BuildSection("Turbostratic peak (Lorentzian)", turboItems));
        }

        var resultItems = new List<(string, string)>
        {
            ("Xg / Xt", $"{r.AreaFractionGraphitic * 100:F1}% / {r.AreaFractionTurbostratic * 100:F1}%"),
            ("d′ weighted", $"{r.DPrimeWeighted:F6} Å"),
            ("Crystallite Lc", $"{r.CrystalliteLc:F1} Å (apparent)"),
            ("Baseline y0", $"{r.Y0:F3}"),
        };
        if (r.TwoThetaOffset != 0) resultItems.Add(("2θ displacement corr.", $"{r.TwoThetaOffset:+0.000;-0.000}°"));
        resultItems.Add(("Wavelength λ", $"{r.Wavelength:F5} Å"));
        ResultRowsPanel.Children.Add(BuildSection("Result", resultItems));

        if (Vm.Crystallinity is { } c)
        {
            var crystItems = new (string, string)[]
            {
                ("Crystalline fraction", $"{c.CrystallineFraction * 100:F1}%"),
                ("Disordered fraction", $"{c.DisorderedFraction * 100:F1}%"),
                ("Graphitic 2θ / FWHM", $"{c.GraphiticCenter:F3}° / {c.GraphiticFWHM:F3}°"),
                ("Decomp R²", $"{c.FitR2:F4}"),
            };
            ResultRowsPanel.Children.Add(BuildSection("Crystallinity (002 amount)", crystItems));
        }
    }

    private static UIElement BuildSection(string title, IReadOnlyList<(string Label, string Value)> items)
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

    private void UpdateChart()
    {
        if (Vm.Result is { } r) PlotService.Render(ChartControl.Plot, r, Vm.Curve);
        else ChartControl.Plot.Clear();
        ChartControl.Refresh();
    }
}
