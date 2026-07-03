namespace XRDAnalyzer.Engine.Models;

public sealed class ImpurityHitDto
{
    public double TwoTheta { get; set; }
    public string Phase { get; set; } = "";
    public string Meaning { get; set; } = "";
    public double RelPct { get; set; }
    public string Level { get; set; } = "";
}

public sealed class ImpurityScanDto
{
    public string Verdict { get; set; } = "";
    public ImpurityHitDto[] Hits { get; set; } = [];
    public bool Clean { get; set; }
    public double WorstPct { get; set; }
}

public sealed class ImpuritiesRequest
{
    public required PatternDto Pattern { get; set; }
}

public sealed class ImpuritiesResponse
{
    public ImpurityScanDto? Scan { get; set; }
    public string? Error { get; set; }
}
