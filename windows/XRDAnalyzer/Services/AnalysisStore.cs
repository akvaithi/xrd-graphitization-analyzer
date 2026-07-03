using System.Reflection;
using System.Text.Json;
using XRDAnalyzer.Engine.Models;
using XRDAnalyzer.Models;

namespace XRDAnalyzer.Services;

/// Reads/writes a sidecar JSON next to each scan: `MyScan.xy.xrda.json`. The
/// raw `.xy` is never modified. Mirrors `AnalysisStore`
/// (native/Sources/XRDApp/AnalysisStore.swift) — same sidecar shape, so files
/// are cross-platform between the macOS and Windows apps.
public static class AnalysisStore
{
    private const int MaxHistory = 20;

    private static readonly string? AppVersion = Assembly.GetExecutingAssembly().GetName().Version?.ToString();

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true,
        Converters = { new Iso8601DateTimeOffsetConverter() },
    };

    public static string SidecarPath(string path) => path + ".xrda.json";

    public static AnalysisRecord? Load(string path)
    {
        var sidecar = SidecarPath(path);
        if (!File.Exists(sidecar)) return null;
        try
        {
            return JsonSerializer.Deserialize<AnalysisRecord>(File.ReadAllText(sidecar), JsonOptions);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    public static bool Save(AnalysisRecord record, string path)
    {
        try
        {
            File.WriteAllText(SidecarPath(path), JsonSerializer.Serialize(record, JsonOptions));
            return true;
        }
        catch (IOException)
        {
            return false;
        }
    }

    /// Build/update the record for a file: set current settings + result
    /// snapshot, append a history entry when the settings differ from the
    /// last one (capped), and persist. `flaggedForRedo` of null preserves the
    /// existing flag.
    public static bool Update(string path, DeconvSettings settings, DGResultDto? result, bool? flaggedForRedo = null)
    {
        var rec = Load(path) ?? new AnalysisRecord { SourceFileName = Path.GetFileName(path), Settings = settings };
        if (rec.History.Count == 0 || !rec.History[^1].Settings.Equals(settings))
        {
            rec.History.Add(new HistoryEntry
            {
                Timestamp = DateTimeOffset.UtcNow,
                Settings = settings.Clone(),
                DgPercent = result?.DgPercent,
                Note = settings.AiNote,
            });
            if (rec.History.Count > MaxHistory) rec.History.RemoveRange(0, rec.History.Count - MaxHistory);
        }
        rec.Settings = settings;
        rec.Result = result is null ? null : DgSnapshot.From(result);
        rec.ShiftOffset = result is { TwoThetaOffset: not 0 } ? result.TwoThetaOffset : null;
        if (flaggedForRedo is { } f) rec.FlaggedForRedo = f;
        rec.UpdatedAt = DateTimeOffset.UtcNow;
        rec.AppVersion = AppVersion;
        return Save(rec, path);
    }

    /// Persist just the yield masses (leaves settings/result/history intact).
    public static bool SaveYield(string path, YieldInputs yield)
    {
        var rec = Load(path) ?? new AnalysisRecord { SourceFileName = Path.GetFileName(path), Settings = new DeconvSettings() };
        rec.YieldInputs = yield;
        rec.UpdatedAt = DateTimeOffset.UtcNow;
        rec.AppVersion = AppVersion;
        return Save(rec, path);
    }
}
