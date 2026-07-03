using System.Text.Json.Serialization;

namespace XRDAnalyzer.Engine.Models;

/// Mirrors `DeconvSettings` (native/Sources/XRDApp/AppModel.swift) and
/// `FitSettingsDTO` (native/Sources/XRDBridge/Models.swift).
public sealed class FitSettingsDto
{
    public int PeakCount { get; set; } = 2;
    public bool SubtractBg { get; set; }
    public bool TurboLocked { get; set; }
    public double TurboCenter { get; set; } = 26.2;
    public bool AnchorOn { get; set; }
    public double AnchorTarget { get; set; } = 26.54;
    public string CalStdPhase { get; set; } = "";
}

public sealed class FitRequest
{
    public required PatternDto Pattern { get; set; }
    public required FitSettingsDto Settings { get; set; }
}

/// Mirrors `PeakDTO`. Swift's `A` field starts uppercase (physics notation for
/// peak amplitude), which the naive first-char camelCase policy can't derive
/// from a PascalCase name, hence the explicit override.
public sealed class PeakDto
{
    [JsonPropertyName("A")]
    public double A { get; set; }
    public double Xc { get; set; }
    public double W { get; set; }
    public double Mu { get; set; }
    public double DSpacing { get; set; }
}

public sealed class DGResultDto
{
    public string MethodName { get; set; } = "";
    public double Wavelength { get; set; }
    public double Y0 { get; set; }
    public int PeakCount { get; set; }
    public bool BackgroundSubtracted { get; set; }
    public required PeakDto Graphitic { get; set; }
    public PeakDto? Turbostratic { get; set; }
    public double AreaFractionGraphitic { get; set; }
    public double AreaFractionTurbostratic { get; set; }
    public double DPrimeWeighted { get; set; }
    public double CrystalliteLc { get; set; }
    public double FitR2 { get; set; }
    public double DgPercent { get; set; }
    public double? DgSigma { get; set; }
    public double TwoThetaOffset { get; set; }
    public double[] PointsX { get; set; } = [];
    public double[] PointsY { get; set; } = [];
}

public sealed class FitResponse
{
    public DGResultDto? Result { get; set; }
    public InternalStandardDto? Calibration { get; set; }
    public string? Error { get; set; }
}

public sealed class InternalStandardMatchDto
{
    public double Line { get; set; }
    public double Observed { get; set; }
    public double Delta { get; set; }
}

public sealed class InternalStandardDto
{
    public string? Phase { get; set; }
    public string? PhaseLabel { get; set; }
    public double Offset { get; set; }
    public double? Spread { get; set; }
    public int NLines { get; set; }
    public InternalStandardMatchDto[] Matches { get; set; } = [];
    public bool Reliable { get; set; }
    public bool Significant { get; set; }
}

public sealed class DgRangeMethodDto
{
    public string Name { get; set; } = "";
    public double Dg { get; set; }
}

public sealed class DGRangeDto
{
    public double Primary { get; set; }
    public double Low { get; set; }
    public double High { get; set; }
    public DgRangeMethodDto[] ByMethod { get; set; } = [];
}

public sealed class RangeRequest
{
    public required PatternDto Pattern { get; set; }
    public required FitSettingsDto Settings { get; set; }
    public double TurbostraticLow { get; set; } = 26.10;
}

public sealed class RangeResponse
{
    public DGRangeDto? Range { get; set; }
    public InternalStandardDto? Calibration { get; set; }
    public string? Error { get; set; }
}

public sealed class CurveRequest
{
    public double Y0 { get; set; }
    public required PeakDto Graphitic { get; set; }
    public PeakDto? Turbostratic { get; set; }
    public double XLow { get; set; }
    public double XHigh { get; set; }
    public int Points { get; set; } = 320;
}

public sealed class CurvePointDto
{
    public double X { get; set; }
    public double Y { get; set; }
}

public sealed class CurveSeriesDto
{
    public CurvePointDto[] Graphitic { get; set; } = [];
    public CurvePointDto[] Turbostratic { get; set; } = [];
    public CurvePointDto[] Total { get; set; } = [];
}

public sealed class CurveResponse
{
    public CurveSeriesDto? Series { get; set; }
    public string? Error { get; set; }
}
