using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using XRDAnalyzer.Engine;
using XRDAnalyzer.Engine.Models;
using XRDAnalyzer.Models;
using XRDAnalyzer.Services;

namespace XRDAnalyzer.ViewModels;

/// Shared app state across tabs: loaded files, per-file settings/results, and
/// re-scan flags. Mirrors `AppModel` (native/Sources/XRDApp/AppModel.swift).
public sealed partial class AppViewModel : ObservableObject
{
    public ObservableCollection<LoadedFile> Files { get; } = [];

    [ObservableProperty]
    private LoadedFile? selection;

    public Dictionary<Guid, DeconvSettings> Settings { get; } = [];
    public Dictionary<Guid, DGResultDto> Results { get; } = [];
    public Dictionary<Guid, bool> RedoFlags { get; } = [];
    public Dictionary<Guid, YieldInputs> YieldInputsByFile { get; } = [];

    // Batch-operation progress (AI Suggest all / Export all).
    [ObservableProperty] private bool batchBusy;
    [ObservableProperty] private double batchProgress;
    [ObservableProperty] private string? batchNote;

    /// Import defaults for a file (turbostratic seed from the auto-fit).
    public static DeconvSettings Defaults(LoadedFile file)
    {
        var s = new DeconvSettings();
        if (file.AutoResult?.Turbostratic is { } t) s.TurboCenter = t.Xc;
        return s;
    }

    /// The current fit for a file: the refined interactive result if one
    /// exists, else the import auto-fit.
    public DGResultDto? CurrentResult(LoadedFile file) =>
        Results.TryGetValue(file.Id, out var r) ? r : file.AutoResult;

    public string DgText(LoadedFile file) =>
        file.Pattern is null ? "—" : CurrentResult(file) is { } r ? $"{r.DgPercent:F2}%" : "fit failed";

    public bool Failed(LoadedFile file) => file.Pattern is null || CurrentResult(file) is null;

    public bool IsFlaggedForRedo(LoadedFile file) => RedoFlags.GetValueOrDefault(file.Id);

    public void ToggleRedo(LoadedFile file)
    {
        var flagged = !IsFlaggedForRedo(file);
        RedoFlags[file.Id] = flagged;
        AnalysisStore.Update(file.Path, Settings.GetValueOrDefault(file.Id) ?? Defaults(file),
            CurrentResult(file), flagged);
    }

    /// A reason to re-scan, or null — when the graphitic (002) centre falls
    /// outside the acceptable band (default 26.50-26.60 degrees).
    public string? RedoRecommended(LoadedFile file, double lo = 26.50, double hi = 26.60)
    {
        if (CurrentResult(file)?.Graphitic.Xc is not { } xc) return null;
        if (xc < lo) return $"graphitic 2θ {xc:F3}° < {lo:F2}° — consider re-scanning";
        if (xc > hi) return $"graphitic 2θ {xc:F3}° > {hi:F2}° — consider re-scanning";
        return null;
    }

    /// Load `.xy` files: parse, auto-fit, compute crystallinity once, and
    /// restore any saved sidecar. Skips files already open (re-selects them).
    public void Open(IEnumerable<string> paths)
    {
        LoadedFile? firstAdded = null;
        foreach (var path in paths.Where(p => Path.GetExtension(p).Equals(".xy", StringComparison.OrdinalIgnoreCase)))
        {
            var full = Path.GetFullPath(path);
            var existing = Files.FirstOrDefault(f => string.Equals(Path.GetFullPath(f.Path), full, StringComparison.OrdinalIgnoreCase));
            if (existing is not null) { Selection = existing; continue; }

            var info = XrdEngine.ParseRun(new ParseRunRequest { FileName = Path.GetFileName(path) }).Info;
            PatternDto? pattern = null;
            string? parseError = null;
            DGResultDto? autoResult = null;
            CrystallinityDto? crystallinity = null;
            try
            {
                pattern = XyFile.ParseFile(path);
                var fit = XrdEngine.Fit(new FitRequest { Pattern = pattern, Settings = new FitSettingsDto() });
                autoResult = fit.Result;
                var cryst = XrdEngine.Crystallinity(new CrystallinityRequest { Pattern = pattern });
                crystallinity = cryst.Result;
            }
            catch (Exception ex)
            {
                parseError = ex.Message;
            }

            var lf = new LoadedFile
            {
                Path = path,
                DisplayName = info?.DisplayName ?? Path.GetFileName(path),
                Pattern = pattern,
                ParseError = parseError,
                Info = info,
                AutoResult = autoResult,
                Crystallinity = crystallinity,
            };
            Files.Add(lf);
            firstAdded ??= lf;

            var rec = AnalysisStore.Load(path);
            if (rec is not null)
            {
                Settings[lf.Id] = rec.Settings;
                RedoFlags[lf.Id] = rec.FlaggedForRedo;
                if (rec.YieldInputs is { } yi) YieldInputsByFile[lf.Id] = yi;
                if (pattern is not null)
                {
                    var refit = XrdEngine.Fit(new FitRequest { Pattern = pattern, Settings = rec.Settings.ToFitSettings() });
                    if (refit.Result is not null) Results[lf.Id] = refit.Result;
                }
            }
        }
        Selection ??= Files.FirstOrDefault();
        if (firstAdded is not null) Selection = firstAdded;
    }

    public void Remove(LoadedFile file)
    {
        Files.Remove(file);
        Settings.Remove(file.Id);
        Results.Remove(file.Id);
        RedoFlags.Remove(file.Id);
        YieldInputsByFile.Remove(file.Id);
        if (Selection == file) Selection = Files.FirstOrDefault();
    }

    // MARK: - Yield (optional)

    /// The yield result for a file, if its masses have been entered. The
    /// filename recipe → mass derivation and the yield math itself both stay
    /// in the bridge (`xrd_yield`) — mirrors `AppModel.yieldResult`.
    public YieldResponse Yield(LoadedFile file)
    {
        var yi = YieldInputsByFile.GetValueOrDefault(file.Id) ?? new YieldInputs();
        return XrdEngine.Yield(new YieldRequest
        {
            Pellet = yi.Pellet,
            PostFurnace = yi.PostFurnace,
            PostAcid = yi.PostAcid > 0 ? yi.PostAcid : null,
            CarbonRatio = file.Info?.CarbonRatio,
            FeRatio = file.Info?.FeRatio,
            Caco3Ratio = file.Info?.Caco3Ratio,
            CarbonType = file.Info?.CarbonType,
            CWt = yi.CWt,
            SWt = yi.SWt,
            CrystallineFraction = file.Crystallinity?.CrystallineFraction,
        });
    }

    /// Persist the yield masses for a file into its sidecar (raw .xy untouched).
    public void SaveYield(LoadedFile file, YieldInputs yi)
    {
        YieldInputsByFile[file.Id] = yi;
        AnalysisStore.SaveYield(file.Path, yi);
    }

    // MARK: - Batch operations

    /// Run the AI suggester (Ollama) over every loaded file, applying each
    /// result. Mirrors `AppModel.suggestAllAI`.
    public async Task SuggestAllAiAsync()
    {
        if (BatchBusy) return;
        BatchBusy = true; BatchProgress = 0; BatchNote = null;
        var targets = Files.Where(f => f.Pattern is not null).ToList();
        var failures = 0;
        for (var i = 0; i < targets.Count; i++)
        {
            var f = targets[i];
            BatchNote = $"Suggesting {i + 1}/{targets.Count}: {f.DisplayName}";
            if (f.Pattern is { } pattern)
            {
                var response = await Task.Run(() => XrdEngine.AiSuggest(new AiSuggestRequest { Pattern = pattern }));
                if (response.Error is not null)
                {
                    failures++;
                }
                else
                {
                    var applied = response.Settings is { } s
                        ? new DeconvSettings
                        {
                            PeakCount = s.PeakCount, SubtractBg = s.SubtractBg, TurboLocked = s.TurboLocked,
                            TurboCenter = s.TurboCenter, AnchorOn = s.AnchorOn, AnchorTarget = s.AnchorTarget,
                            CalStdPhase = s.CalStdPhase, AiNote = response.AiNote, AiConfidence = response.AiConfidence,
                        }
                        : (Settings.GetValueOrDefault(f.Id) ?? Defaults(f)).Clone();
                    if (response.Settings is null) { applied.AiNote = response.AiNote; applied.AiConfidence = response.AiConfidence; }

                    Settings[f.Id] = applied;
                    var fit = XrdEngine.Fit(new FitRequest { Pattern = pattern, Settings = applied.ToFitSettings() });
                    if (fit.Result is not null) Results[f.Id] = fit.Result;
                    else Results.Remove(f.Id);
                    AnalysisStore.Update(f.Path, applied, fit.Result);
                }
            }
            BatchProgress = (double)(i + 1) / Math.Max(targets.Count, 1);
        }
        BatchNote = failures == 0
            ? $"Suggested {targets.Count} file(s)."
            : $"Suggested {targets.Count - failures}/{targets.Count} ({failures} failed).";
        BatchBusy = false;
    }

    /// Export a report CSV + fit PNG per file, plus one consolidated runs CSV.
    /// Mirrors `AppModel.exportAll`.
    public async Task ExportAllAsync(string folderPath)
    {
        if (BatchBusy) return;
        BatchBusy = true; BatchProgress = 0; BatchNote = null;
        var targets = Files.Where(f => CurrentResult(f) is not null).ToList();
        var consolidated = new System.Text.StringBuilder(ReportBuilder.ConsolidatedHeader());
        var written = 0;
        for (var i = 0; i < targets.Count; i++)
        {
            var f = targets[i];
            BatchNote = $"Exporting {i + 1}/{targets.Count}: {f.DisplayName}";
            var r = CurrentResult(f);
            consolidated.Append(ReportBuilder.ConsolidatedRow(f.FileName, f.Info, r, f.Crystallinity));

            if (r is not null && f.Pattern is { } pattern)
            {
                var stem = FileNaming.SafeFileName(f.DisplayName);
                var rangeSettings = (Settings.GetValueOrDefault(f.Id) ?? Defaults(f)).ToFitSettings();
                rangeSettings.PeakCount = 2;
                var span = XrdEngine.Range(new RangeRequest { Pattern = pattern, Settings = rangeSettings }).Range;
                var quality = XrdEngine.Impurities(new ImpuritiesRequest { Pattern = pattern }).Scan;
                var csv = ReportBuilder.Csv(f.DisplayName, f.FileName, r, span, quality, f.Crystallinity);
                await File.WriteAllTextAsync(Path.Combine(folderPath, $"{stem} — DG report.csv"), csv);

                var xs = r.PointsX;
                var curve = XrdEngine.Curve(new CurveRequest
                {
                    Y0 = r.Y0, Graphitic = r.Graphitic, Turbostratic = r.Turbostratic,
                    XLow = xs.Length > 0 ? xs.Min() : 24.0, XHigh = xs.Length > 0 ? xs.Max() : 28.5,
                }).Series;
                var plot = new ScottPlot.Plot();
                PlotService.Render(plot, r, curve);
                var cryst = f.Crystallinity is { } c ? $" · cryst {c.CrystallineFraction * 100:F0}%" : "";
                plot.Title($"{f.DisplayName}   —   DG {r.DgPercent:F2}%{cryst} · (002) fit");
                plot.SavePng(Path.Combine(folderPath, $"{stem} — 002 fit.png"), 1200, 750);
                written++;
            }
            BatchProgress = (double)(i + 1) / Math.Max(targets.Count, 1);
        }
        await File.WriteAllTextAsync(Path.Combine(folderPath, "XRD runs.csv"), consolidated.ToString());
        BatchNote = $"Exported {written} file(s) + XRD runs.csv to {Path.GetFileName(folderPath.TrimEnd(Path.DirectorySeparatorChar))}.";
        BatchBusy = false;
    }
}
