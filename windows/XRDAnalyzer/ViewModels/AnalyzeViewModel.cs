using CommunityToolkit.Mvvm.ComponentModel;
using XRDAnalyzer.Engine;
using XRDAnalyzer.Engine.Models;
using XRDAnalyzer.Models;
using XRDAnalyzer.Services;

namespace XRDAnalyzer.ViewModels;

/// The interactive per-file deconvolution pane. Mirrors `DetailView`
/// (native/Sources/XRDApp/DetailView.swift): editable settings live here,
/// bound two-way to the view; any edit re-fits (through the bridge — no
/// DG/calibration math in C#) and persists to the sidecar. `_settingsLoaded`
/// is the same gate `DetailView` uses so a programmatic load (switching
/// files) is never mistaken for a user edit and doesn't clobber the sidecar.
public sealed partial class AnalyzeViewModel : ObservableObject
{
    private readonly AppViewModel _app;
    private LoadedFile? _file;
    private bool _settingsLoaded;
    private bool _applyingSettings;

    public AnalyzeViewModel(AppViewModel app) => _app = app;

    [ObservableProperty] private int peakCount = 2;
    [ObservableProperty] private bool subtractBg;
    [ObservableProperty] private bool turboLocked;
    [ObservableProperty] private double turboCenter = 26.2;
    [ObservableProperty] private bool anchorOn;
    [ObservableProperty] private double anchorTarget = 26.54;
    [ObservableProperty] private string calStdPhase = "";
    [ObservableProperty] private string? aiNote;
    [ObservableProperty] private double? aiConfidence;

    [ObservableProperty] private DGResultDto? result;
    [ObservableProperty] private InternalStandardDto? calibration;
    [ObservableProperty] private string? fitError;
    [ObservableProperty] private ImpurityScanDto? quality;
    [ObservableProperty] private DGRangeDto? dgSpan;
    [ObservableProperty] private CrystallinityDto? crystallinity;
    [ObservableProperty] private CurveSeriesDto? curve;
    [ObservableProperty] private bool aiBusy;

    partial void OnPeakCountChanged(int value) => HandleSettingChanged();
    partial void OnSubtractBgChanged(bool value) => HandleSettingChanged();
    partial void OnTurboLockedChanged(bool value) => HandleSettingChanged();
    partial void OnTurboCenterChanged(double value) => HandleSettingChanged();
    partial void OnAnchorOnChanged(bool value) => HandleSettingChanged();
    partial void OnAnchorTargetChanged(double value) => HandleSettingChanged();
    partial void OnCalStdPhaseChanged(string value) => HandleSettingChanged();

    public bool IsDefaultSettings => _file is not null && CurrentSettings().Equals(AppViewModel.Defaults(_file));

    private DeconvSettings CurrentSettings() => new()
    {
        PeakCount = PeakCount,
        SubtractBg = SubtractBg,
        TurboLocked = TurboLocked,
        TurboCenter = TurboCenter,
        AnchorOn = AnchorOn,
        AnchorTarget = AnchorTarget,
        CalStdPhase = CalStdPhase,
        AiNote = AiNote,
        AiConfidence = AiConfidence,
    };

    /// Applies a full settings snapshot without firing `HandleSettingChanged`
    /// per field — callers decide whether/when to refit+persist afterward.
    private void ApplySettings(DeconvSettings s)
    {
        _applyingSettings = true;
        try
        {
            PeakCount = s.PeakCount;
            SubtractBg = s.SubtractBg;
            TurboLocked = s.TurboLocked;
            TurboCenter = s.TurboCenter;
            AnchorOn = s.AnchorOn;
            AnchorTarget = s.AnchorTarget;
            CalStdPhase = s.CalStdPhase;
            AiNote = s.AiNote;
            AiConfidence = s.AiConfidence;
        }
        finally { _applyingSettings = false; }
    }

    /// Load once per file selection — never treated as a user edit.
    public void LoadFile(LoadedFile file)
    {
        _file = file;
        _settingsLoaded = false;
        Quality = file.Pattern is null ? null : XrdEngine.Impurities(new ImpuritiesRequest { Pattern = file.Pattern }).Scan;
        Crystallinity = file.Crystallinity;
        ApplySettings(_app.Settings.TryGetValue(file.Id, out var saved) ? saved : AppViewModel.Defaults(file));
        _settingsLoaded = true;
        Refit();
    }

    public void ResetToDefaults()
    {
        if (_file is null) return;
        ApplySettings(AppViewModel.Defaults(_file));
        HandleSettingChanged();
    }

    private void HandleSettingChanged()
    {
        if (!_settingsLoaded || _applyingSettings || _file is null) return;
        var s = CurrentSettings();
        _app.Settings[_file.Id] = s;
        Refit();
        AnalysisStore.Update(_file.Path, s, Result);   // save sidecar only on real edits
        OnPropertyChanged(nameof(IsDefaultSettings));
    }

    private void Refit()
    {
        if (_file?.Pattern is not { } pattern)
        {
            Result = null; Calibration = null; FitError = null; Curve = null; DgSpan = null;
            return;
        }
        var settings = CurrentSettings().ToFitSettings();
        var fit = XrdEngine.Fit(new FitRequest { Pattern = pattern, Settings = settings });
        Result = fit.Result;
        Calibration = fit.Calibration;
        FitError = fit.Error;
        if (fit.Result is not null) _app.Results[_file.Id] = fit.Result;
        else _app.Results.Remove(_file.Id);

        if (fit.Result is not null)
        {
            // range shares calibration/background but always spans both peak counts, like DetailView.refit()
            var rangeSettings = CurrentSettings().ToFitSettings();
            rangeSettings.PeakCount = 2;
            DgSpan = XrdEngine.Range(new RangeRequest { Pattern = pattern, Settings = rangeSettings }).Range;

            var xs = fit.Result.PointsX;
            var xLow = xs.Length > 0 ? xs.Min() : 24.0;
            var xHigh = xs.Length > 0 ? xs.Max() : 28.5;
            Curve = XrdEngine.Curve(new CurveRequest
            {
                Y0 = fit.Result.Y0,
                Graphitic = fit.Result.Graphitic,
                Turbostratic = fit.Result.Turbostratic,
                XLow = xLow,
                XHigh = xHigh,
            }).Series;
        }
        else
        {
            DgSpan = null;
            Curve = null;
        }
    }

    /// Ollama AI suggest — mirrors `DetailView.runAI()`.
    public async Task RunAiSuggestAsync(string? model, string? host)
    {
        if (_file?.Pattern is not { } pattern) return;
        AiBusy = true;
        try
        {
            var response = await Task.Run(() =>
                XrdEngine.AiSuggest(new AiSuggestRequest { Pattern = pattern, Model = model, Host = host }));
            if (response.Error is not null)
            {
                AiNote = $"AI error: {response.Error}";
                return;
            }
            if (response.Settings is { } s)
            {
                ApplySettings(new DeconvSettings
                {
                    PeakCount = s.PeakCount,
                    SubtractBg = s.SubtractBg,
                    TurboLocked = s.TurboLocked,
                    TurboCenter = s.TurboCenter,
                    AnchorOn = s.AnchorOn,
                    AnchorTarget = s.AnchorTarget,
                    CalStdPhase = s.CalStdPhase,
                    AiNote = response.AiNote,
                    AiConfidence = response.AiConfidence,
                });
            }
            else
            {
                // amorphous_invalid: note/confidence only, deconvolution settings untouched
                _applyingSettings = true;
                try { AiNote = response.AiNote; AiConfidence = response.AiConfidence; }
                finally { _applyingSettings = false; }
            }
            HandleSettingChanged();
        }
        finally { AiBusy = false; }
    }
}
