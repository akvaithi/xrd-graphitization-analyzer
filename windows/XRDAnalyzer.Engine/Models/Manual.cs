namespace XRDAnalyzer.Engine.Models;

public sealed class ManualPeakDto
{
    public double Xc { get; set; }
    public double Area { get; set; }
}

public sealed class ManualResultDto
{
    public int NPeaks { get; set; }
    public double Wavelength { get; set; }
    public double GraphiticXc { get; set; }
    public double GraphiticArea { get; set; }
    public double GraphiticD { get; set; }
    public double? TurbostraticXc { get; set; }
    public double? TurbostraticArea { get; set; }
    public double? TurbostraticD { get; set; }
    public double? AreaFractionGraphitic { get; set; }
    public double? AreaFractionTurbostratic { get; set; }
    public double DPrime { get; set; }
    public double DgPercent { get; set; }
}

public sealed class ManualRequest
{
    public required ManualPeakDto[] Peaks { get; set; }
    public double? Wavelength { get; set; }
}

public sealed class ManualResponse
{
    public ManualResultDto? Result { get; set; }
    public string? Error { get; set; }
}
