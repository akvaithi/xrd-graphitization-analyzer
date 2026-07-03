namespace XRDAnalyzer.Models;

/// The three weighed masses for a run's yield, persisted in the sidecar.
/// Mirrors `YieldInputs` (native/Sources/XRDApp/AppModel.swift).
public sealed class YieldInputs
{
    public double Pellet { get; set; }
    public double PostFurnace { get; set; }
    public double PostAcid { get; set; }
    public double? CWt { get; set; }
    public double? SWt { get; set; }

    public bool IsComputable => Pellet > 0 && PostFurnace > 0;
}
