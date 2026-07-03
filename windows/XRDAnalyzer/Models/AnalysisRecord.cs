using XRDAnalyzer.Engine.Models;

namespace XRDAnalyzer.Models;

/// Snapshot of the engine `DGResult` for persistence/history. Mirrors
/// `DGSnapshot` (native/Sources/XRDApp/AnalysisStore.swift) field-for-field.
public sealed class DgSnapshot
{
    public double DgPercent { get; set; }
    public double? DgSigma { get; set; }
    public int PeakCount { get; set; }
    public double GraphiticXc { get; set; }
    public double GraphiticW { get; set; }
    public double GraphiticMu { get; set; }
    public double GraphiticD { get; set; }
    public double? TurboXc { get; set; }
    public double? TurboW { get; set; }
    public double? TurboD { get; set; }
    public double AreaGraphitic { get; set; }
    public double AreaTurbostratic { get; set; }
    public double DPrime { get; set; }
    public double Lc { get; set; }
    public double Y0 { get; set; }
    public double TwoThetaOffset { get; set; }
    public double FitR2 { get; set; }
    public string MethodName { get; set; } = "";
    public double Wavelength { get; set; }

    public static DgSnapshot From(DGResultDto r) => new()
    {
        DgPercent = r.DgPercent,
        DgSigma = r.DgSigma,
        PeakCount = r.PeakCount,
        GraphiticXc = r.Graphitic.Xc,
        GraphiticW = r.Graphitic.W,
        GraphiticMu = r.Graphitic.Mu,
        GraphiticD = r.Graphitic.DSpacing,
        TurboXc = r.Turbostratic?.Xc,
        TurboW = r.Turbostratic?.W,
        TurboD = r.Turbostratic?.DSpacing,
        AreaGraphitic = r.AreaFractionGraphitic,
        AreaTurbostratic = r.AreaFractionTurbostratic,
        DPrime = r.DPrimeWeighted,
        Lc = r.CrystalliteLc,
        Y0 = r.Y0,
        TwoThetaOffset = r.TwoThetaOffset,
        FitR2 = r.FitR2,
        MethodName = r.MethodName,
        Wavelength = r.Wavelength,
    };
}

/// One past analysis, kept so prior deconvolutions aren't lost when settings
/// change. Mirrors `HistoryEntry`.
public sealed class HistoryEntry
{
    public DateTimeOffset Timestamp { get; set; }
    public required DeconvSettings Settings { get; set; }
    public double? DgPercent { get; set; }
    public string? Note { get; set; }
}

/// The persisted per-file analysis record — the sidecar payload
/// (`MyScan.xy.xrda.json`). Mirrors `AnalysisRecord`; unknown/missing fields
/// decode tolerantly (System.Text.Json defaults missing properties), so
/// sidecars written by either platform stay readable by the other.
public sealed class AnalysisRecord
{
    public int SchemaVersion { get; set; } = 1;
    public required string SourceFileName { get; set; }
    public required DeconvSettings Settings { get; set; }
    public DgSnapshot? Result { get; set; }
    public double? ShiftOffset { get; set; }
    public bool FlaggedForRedo { get; set; }
    public List<HistoryEntry> History { get; set; } = [];
    public YieldInputs? YieldInputs { get; set; }
    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.UtcNow;
    public string? AppVersion { get; set; }
}
