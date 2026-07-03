namespace XRDAnalyzer.Engine.Models;

/// Recipe ratios (from `RunInfo`) + pellet mass; the mass-balance derivation
/// and yield math both live in the Swift bridge (`xrd_yield`), never here.
public sealed class YieldRequest
{
    public double Pellet { get; set; }
    public double PostFurnace { get; set; }
    public double? PostAcid { get; set; }
    public double? CarbonRatio { get; set; }
    public double? FeRatio { get; set; }
    public double? Caco3Ratio { get; set; }
    public string? CarbonType { get; set; }
    public double? CWt { get; set; }
    public double? SWt { get; set; }
    public double? CrystallineFraction { get; set; }
}

public sealed class YieldResultDto
{
    public double ActualC { get; set; }
    public double ActualS { get; set; }
    public double BoudouardCLoss { get; set; }
    public double RemainingC { get; set; }
    public double CaResidues { get; set; }
    public double GraphiteTheoretical { get; set; }
    public double TargetWash { get; set; }
    public double MeasuredCAfterFurnace { get; set; }
    public double MassYield { get; set; }
    public double CarbonLostBeyondBoudouard { get; set; }
    public double? UnaccountedFurnaceLoss { get; set; }
    public double? TrappedMetal { get; set; }
    public double? WashEfficiency { get; set; }
    public bool? OverRemoved { get; set; }
    public double? CrystallineFraction { get; set; }
    public double? CrystallineGraphiteYield { get; set; }
}

public sealed class DerivedMassesDto
{
    public double Gpc { get; set; }
    public double Fe { get; set; }
    public double Caco3 { get; set; }
    public string? Grade { get; set; }
}

public sealed class YieldResponse
{
    public YieldResultDto? Result { get; set; }
    public DerivedMassesDto? DerivedMasses { get; set; }
    public string? Error { get; set; }
}
