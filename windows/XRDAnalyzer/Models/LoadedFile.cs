using XRDAnalyzer.Engine.Models;

namespace XRDAnalyzer.Models;

/// One loaded `.xy` file: standardized name, raw pattern (for live re-fitting),
/// parsed run parameters, and the default auto-fit. Mirrors `LoadedFile`
/// (native/Sources/XRDApp/AppModel.swift).
public sealed class LoadedFile
{
    public Guid Id { get; } = Guid.NewGuid();
    public required string Path { get; init; }
    public required string DisplayName { get; init; }
    public PatternDto? Pattern { get; init; }
    public string? ParseError { get; init; }
    public RunInfoDto? Info { get; init; }
    public DGResultDto? AutoResult { get; init; }
    public CrystallinityDto? Crystallinity { get; init; }

    public string FileName => System.IO.Path.GetFileName(Path);

    public string DgText => Pattern is null ? "—" : AutoResult is { } r ? $"{r.DgPercent:F2}%" : "fit failed";
    public bool Failed => Pattern is null || AutoResult is null;
}
