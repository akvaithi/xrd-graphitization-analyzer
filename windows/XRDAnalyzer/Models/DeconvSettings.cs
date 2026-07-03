using XRDAnalyzer.Engine.Models;

namespace XRDAnalyzer.Models;

/// Per-file deconvolution choices + last AI run, persisted in the sidecar.
/// Mirrors `DeconvSettings` (native/Sources/XRDApp/AppModel.swift) exactly —
/// field names must match so `.xy.xrda.json` sidecars are cross-platform.
public sealed class DeconvSettings : IEquatable<DeconvSettings>
{
    public int PeakCount { get; set; } = 2;
    public bool SubtractBg { get; set; }
    public bool TurboLocked { get; set; }
    public double TurboCenter { get; set; } = 26.2;
    public bool AnchorOn { get; set; }
    public double AnchorTarget { get; set; } = 26.54;
    public string CalStdPhase { get; set; } = "";
    public string? AiNote { get; set; }
    public double? AiConfidence { get; set; }

    /// The subset the engine bridge actually needs to run a fit.
    public FitSettingsDto ToFitSettings() => new()
    {
        PeakCount = PeakCount,
        SubtractBg = SubtractBg,
        TurboLocked = TurboLocked,
        TurboCenter = TurboCenter,
        AnchorOn = AnchorOn,
        AnchorTarget = AnchorTarget,
        CalStdPhase = CalStdPhase,
    };

    public DeconvSettings Clone() => new()
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

    // Matches Swift's auto-synthesized `Equatable` on `DeconvSettings`, which
    // compares every stored property including `aiNote`/`aiConfidence` — a
    // fresh AI suggestion (new note) counts as a settings change for the
    // Reset-button-disabled check and the sidecar's history dedup.
    public bool Equals(DeconvSettings? other) =>
        other is not null
        && PeakCount == other.PeakCount && SubtractBg == other.SubtractBg
        && TurboLocked == other.TurboLocked && TurboCenter.Equals(other.TurboCenter)
        && AnchorOn == other.AnchorOn && AnchorTarget.Equals(other.AnchorTarget)
        && CalStdPhase == other.CalStdPhase && AiNote == other.AiNote
        && AiConfidence.Equals(other.AiConfidence);

    public override bool Equals(object? obj) => Equals(obj as DeconvSettings);
    public override int GetHashCode() =>
        HashCode.Combine(PeakCount, SubtractBg, TurboLocked, TurboCenter, AnchorOn,
            HashCode.Combine(AnchorTarget, CalStdPhase, AiNote, AiConfidence));
}
