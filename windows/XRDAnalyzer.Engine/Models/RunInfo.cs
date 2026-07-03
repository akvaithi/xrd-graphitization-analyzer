namespace XRDAnalyzer.Engine.Models;

public sealed class RunInfoDto
{
    public string? CarbonType { get; set; }
    public double? CarbonRatio { get; set; }
    public double? FeRatio { get; set; }
    public bool HasFe { get; set; }
    public double? Caco3Ratio { get; set; }
    public int? TemperatureC { get; set; }
    public double? TimeH { get; set; }
    public string? Form { get; set; }
    public string? Wash { get; set; }
    public string DisplayName { get; set; } = "";
}

public sealed class ParseRunRequest
{
    public required string FileName { get; set; }
}

public sealed class ParseRunResponse
{
    public RunInfoDto? Info { get; set; }
}
