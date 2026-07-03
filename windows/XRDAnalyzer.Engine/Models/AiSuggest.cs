namespace XRDAnalyzer.Engine.Models;

public sealed class SuggestionDto
{
    public int PeakCount { get; set; }
    public double? TurbostraticCenter { get; set; }
    public bool SubtractBackground { get; set; }
    public bool AmorphousInvalid { get; set; }
    public bool DisplacementSuspected { get; set; }
    public double Suggested002Anchor { get; set; }
    public double Confidence { get; set; }
    public string Rationale { get; set; } = "";
}

public sealed class FeaturesDto
{
    public double SinglePeakR2 { get; set; }
    public double TwoPeakR2 { get; set; }
    public double DR2 { get; set; }
    public double SinglePeakCenter { get; set; }
    public double SinglePeakFWHM { get; set; }
    public double LowAngleResidual2theta { get; set; }
    public double LowAngleResidualFraction { get; set; }
    public double? AutomaticTwoPeakTurbostratic2theta { get; set; }
    public double Snr { get; set; }
}

public sealed class AiSuggestRequest
{
    public required PatternDto Pattern { get; set; }
    public string? Model { get; set; }
    public string? Host { get; set; }
}

/// `Settings` carries the derived settings delta (same shape as
/// `FitSettingsDto`) computed by the decision logic ported into
/// `xrd_ai_suggest` — the caller merges it into its own state, never
/// re-deriving it.
public sealed class AiSuggestResponse
{
    public SuggestionDto? Suggestion { get; set; }
    public FeaturesDto? Features { get; set; }
    public InternalStandardDto? Calibration { get; set; }
    public FitSettingsDto? Settings { get; set; }
    public double? AiConfidence { get; set; }
    public string? AiNote { get; set; }
    public string? Error { get; set; }
}
