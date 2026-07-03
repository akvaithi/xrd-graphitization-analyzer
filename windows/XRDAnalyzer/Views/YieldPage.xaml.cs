using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using XRDAnalyzer.Engine.Models;
using XRDAnalyzer.Models;
using XRDAnalyzer.ViewModels;

namespace XRDAnalyzer.Views;

/// Row shown in the file list: the file plus its display-only yield tag.
/// Recomputed (not bound live) whenever yield inputs change — see `Refresh()`.
public sealed class YieldFileRow(LoadedFile file, string yieldTag)
{
    public LoadedFile File { get; } = file;
    public string DisplayName { get; } = file.DisplayName;
    public string YieldTag { get; } = yieldTag;
}

/// Optional Yield tab: weighed masses -> carbon mass yield (+ crystalline-graphite
/// yield using the Analyze pane's crystallinity). Mirrors `YieldView`
/// (native/Sources/XRDApp/YieldView.swift). All mass-balance math goes through
/// `xrd_yield`; this view only marshals inputs/outputs.
public sealed partial class YieldPage : Page
{
    private readonly AppViewModel _app;
    private LoadedFile? _selected;
    private bool _loaded;   // gate: don't persist a programmatic load

    public YieldPage(AppViewModel app)
    {
        InitializeComponent();
        _app = app;
        _app.Files.CollectionChanged += (_, _) => Refresh();
        Refresh();
    }

    private void FilesList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        _selected = (FilesList.SelectedItem as YieldFileRow)?.File;
        LoadForm();
        UpdateDetail();
    }

    private void Field_ValueChanged(NumberBox sender, NumberBoxValueChangedEventArgs args) => SaveForm();

    private void CompositionField_ValueChanged(NumberBox sender, NumberBoxValueChangedEventArgs args)
    {
        if (!_loaded) return;
        SaveForm();
    }

    private void CWtResetButton_Click(object sender, RoutedEventArgs e)
    {
        if (_selected is null) return;
        var yi = _app.YieldInputsByFile.GetValueOrDefault(_selected.Id) ?? new YieldInputs();
        yi.CWt = null;
        _app.SaveYield(_selected, yi);
        LoadForm();
        UpdateDetail();
    }

    private void SWtResetButton_Click(object sender, RoutedEventArgs e)
    {
        if (_selected is null) return;
        var yi = _app.YieldInputsByFile.GetValueOrDefault(_selected.Id) ?? new YieldInputs();
        yi.SWt = null;
        _app.SaveYield(_selected, yi);
        LoadForm();
        UpdateDetail();
    }

    private void Refresh()
    {
        var hasFiles = _app.Files.Count > 0;
        EmptyStateText.Visibility = hasFiles ? Visibility.Collapsed : Visibility.Visible;
        ContentGrid.Visibility = hasFiles ? Visibility.Visible : Visibility.Collapsed;
        if (!hasFiles) return;

        var rows = _app.Files.Select(f => new YieldFileRow(f, YieldTag(f))).ToList();
        FilesList.ItemsSource = rows;

        var done = _app.Files.Count(f => _app.Yield(f).Result is not null);
        FilesHeaderText.Text = $"{done}/{_app.Files.Count} with yield";

        _selected ??= _app.Selection ?? _app.Files.FirstOrDefault();
        var selectedRow = rows.FirstOrDefault(r => r.File == _selected);
        if (selectedRow is not null) FilesList.SelectedItem = selectedRow;

        LoadForm();
        UpdateDetail();
        UpdateSummary();
    }

    private string YieldTag(LoadedFile f)
    {
        var response = _app.Yield(f);
        if (response.Result is { } r)
        {
            var cg = r.CrystallineGraphiteYield is { } c ? $" · cryst-g {c * 100:F0}%" : "";
            return $"yield {r.MassYield * 100:F1}%{cg}";
        }
        return _app.YieldInputsByFile.ContainsKey(f.Id) ? "incomplete" : "no data";
    }

    private void LoadForm()
    {
        _loaded = false;
        var yi = _selected is not null ? _app.YieldInputsByFile.GetValueOrDefault(_selected.Id) ?? new YieldInputs() : new YieldInputs();
        PelletBox.Value = yi.Pellet;
        PostFurnaceBox.Value = yi.PostFurnace;
        PostAcidBox.Value = yi.PostAcid;
        CWtBox.Value = yi.CWt ?? double.NaN;
        SWtBox.Value = yi.SWt ?? double.NaN;
        _loaded = true;
    }

    private void SaveForm()
    {
        if (!_loaded || _selected is null) return;
        var yi = new YieldInputs
        {
            Pellet = double.IsNaN(PelletBox.Value) ? 0 : PelletBox.Value,
            PostFurnace = double.IsNaN(PostFurnaceBox.Value) ? 0 : PostFurnaceBox.Value,
            PostAcid = double.IsNaN(PostAcidBox.Value) ? 0 : PostAcidBox.Value,
            CWt = double.IsNaN(CWtBox.Value) ? null : CWtBox.Value,
            SWt = double.IsNaN(SWtBox.Value) ? null : SWtBox.Value,
        };
        _app.SaveYield(_selected, yi);
        UpdateDetail();
        RefreshListTagsOnly();
        UpdateSummary();
    }

    private void RefreshListTagsOnly()
    {
        var rows = _app.Files.Select(f => new YieldFileRow(f, YieldTag(f))).ToList();
        var selectedFile = _selected;
        FilesList.ItemsSource = rows;
        var selectedRow = rows.FirstOrDefault(r => r.File == selectedFile);
        if (selectedRow is not null) FilesList.SelectedItem = selectedRow;
        var done = _app.Files.Count(f => _app.Yield(f).Result is not null);
        FilesHeaderText.Text = $"{done}/{_app.Files.Count} with yield";
    }

    private void UpdateDetail()
    {
        if (_selected is not { } f)
        {
            FileTitleText.Text = "";
            ResultCard.Visibility = Visibility.Collapsed;
            HintText.Visibility = Visibility.Collapsed;
            RecipeInfoCard.Visibility = Visibility.Collapsed;
            RecipeWarnText.Visibility = Visibility.Collapsed;
            return;
        }
        FileTitleText.Text = f.DisplayName;

        var response = _app.Yield(f);
        if (response.Result is { } r)
        {
            ResultCard.Visibility = Visibility.Visible;
            HintText.Visibility = Visibility.Collapsed;
            MassYieldText.Text = $"{r.MassYield * 100:F2} %";
            CrystGraphiteText.Text = r.CrystallineGraphiteYield is { } cg
                ? $"Crystalline-graphite yield (mass × crystallinity {r.CrystallineFraction * 100:F0}%): {cg * 100:F2} %"
                : "crystalline-graphite yield needs the scan's crystallinity (analyze this file first)";

            ResultBreakdownPanel.Children.Clear();
            AddBreakdownRow("Theoretical graphite", $"{r.GraphiteTheoretical:F4} g");
            AddBreakdownRow("Carbon lost beyond Boudouard", $"{r.CarbonLostBeyondBoudouard:F4} g");
            AddBreakdownRow("Boudouard C loss (CaCO3-driven)", $"{r.BoudouardCLoss:F4} g");
            if (r.UnaccountedFurnaceLoss is { } u) AddBreakdownRow("Unaccounted furnace loss", $"{u:F4} g");
            if (r.TrappedMetal is { } tm && r.WashEfficiency is { } we)
            {
                AddBreakdownRow("Wash efficiency", $"{we * 100:F1}%");
                var overNote = r.OverRemoved is true ? "  (!) over-removed (fines lost?)" : "";
                AddBreakdownRow("Trapped metal", $"{tm:F4} g{overNote}");
            }
        }
        else
        {
            ResultCard.Visibility = Visibility.Collapsed;
            HintText.Visibility = Visibility.Visible;
        }

        if (response.DerivedMasses is { } m)
        {
            RecipeInfoCard.Visibility = Visibility.Visible;
            RecipeWarnText.Visibility = Visibility.Collapsed;
            RecipeGradeText.Text = $"Grade: {m.Grade ?? "—"}";
            RecipeMassesText.Text = $"GPC / Fe / CaCO3: {m.Gpc:F4} / {m.Fe:F4} / {m.Caco3:F4} g";
        }
        else
        {
            RecipeInfoCard.Visibility = Visibility.Collapsed;
            var yi = _app.YieldInputsByFile.GetValueOrDefault(f.Id);
            RecipeWarnText.Visibility = (yi?.Pellet ?? 0) > 0 ? Visibility.Visible : Visibility.Collapsed;
            RecipeWarnText.Text = "Couldn't read the GPC/Fe recipe from this filename — the yield needs those ratios. "
                + "Rename the file to the standard pattern, or use the manifest with explicit masses.";
        }

        var grade = f.Info?.CarbonType;
        CWtNoteText.Text = (_app.YieldInputsByFile.GetValueOrDefault(f.Id)?.CWt) is not null
            ? "per-run override" : $"grade default ({DefaultComposition(grade).cWt:F3})";
        SWtNoteText.Text = (_app.YieldInputsByFile.GetValueOrDefault(f.Id)?.SWt) is not null
            ? "per-run override" : $"grade default ({DefaultComposition(grade).sWt:F3})";
    }

    /// Mirrors `YieldCalc.defaultComposition` — display-only (which default
    /// applies), not used in any computation (the bridge applies its own).
    private static (double cWt, double sWt) DefaultComposition(string? grade) => (grade ?? "").ToUpperInvariant() switch
    {
        "CPC" => (0.97, 0.02),
        "LSPC" => (0.95, 0.007),
        _ => (0.88, 0.045),
    };

    private void AddBreakdownRow(string label, string value)
    {
        var row = new Grid { ColumnDefinitions = { new ColumnDefinition(), new ColumnDefinition { Width = GridLength.Auto } } };
        row.Children.Add(new TextBlock { Text = label, FontSize = 11, Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.White), Opacity = 0.85 });
        var valueText = new TextBlock { Text = value, FontSize = 11, Foreground = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.White) };
        Grid.SetColumn(valueText, 1);
        row.Children.Add(valueText);
        ResultBreakdownPanel.Children.Add(row);
    }

    private void UpdateSummary()
    {
        var rows = _app.Files
            .Select(f => (File: f, Response: _app.Yield(f)))
            .Where(x => x.Response.Result is not null)
            .ToList();
        SummaryPanel.Visibility = rows.Count > 1 ? Visibility.Visible : Visibility.Collapsed;
        SummaryList.ItemsSource = rows.Select(x =>
        {
            var r = x.Response.Result!;
            var cg = r.CrystallineGraphiteYield is { } c ? $" · {c * 100:F1}% cryst-g" : "";
            return $"{x.File.DisplayName}   {r.MassYield * 100:F1}%{cg}";
        }).ToList();
    }
}
