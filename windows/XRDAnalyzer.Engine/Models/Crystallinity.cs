namespace XRDAnalyzer.Engine.Models;

public sealed class CrystallinityDto
{
    public double CrystallineFraction { get; set; }
    public double DisorderedFraction { get; set; }
    public double SharpArea { get; set; }
    public double BroadArea { get; set; }
    public double GraphiticCenter { get; set; }
    public double GraphiticFWHM { get; set; }
    public double FitR2 { get; set; }
    public double WindowLow { get; set; }
    public double WindowHigh { get; set; }
}

public sealed class CrystallinityRequest
{
    public required PatternDto Pattern { get; set; }
    public double? WindowLow { get; set; }
    public double? WindowHigh { get; set; }
}

public sealed class CrystallinityResponse
{
    public CrystallinityDto? Result { get; set; }
    public string? Error { get; set; }
}
