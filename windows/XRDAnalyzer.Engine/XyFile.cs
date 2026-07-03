using System.Globalization;
using XRDAnalyzer.Engine.Models;

namespace XRDAnalyzer.Engine;

/// Reads a two-column `.xy` scan into a `PatternDto`. Mirrors
/// `XRDPattern.parse` (native/Sources/XRDCore/XRDPattern.swift) — this is
/// file I/O, not analysis math, so it stays in C# rather than round-tripping
/// through the bridge just to read a text file.
public static class XyFile
{
    public static PatternDto Parse(IEnumerable<string> lines)
    {
        var twoTheta = new List<double>();
        var intensity = new List<double>();
        foreach (var raw in lines)
        {
            var line = raw.Trim();
            if (line.Length == 0) continue;
            var first = line[0];
            if (first == '#' || first == '!' || first == '\'' || char.IsLetter(first)) continue;
            var parts = line.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length < 2) continue;
            if (!double.TryParse(parts[0], NumberStyles.Float, CultureInfo.InvariantCulture, out var a)) continue;
            if (!double.TryParse(parts[1], NumberStyles.Float, CultureInfo.InvariantCulture, out var b)) continue;
            twoTheta.Add(a);
            intensity.Add(b);
        }
        if (twoTheta.Count == 0) throw new InvalidOperationException("No numeric (2theta, intensity) data found in input.");
        return new PatternDto { TwoTheta = [.. twoTheta], Intensity = [.. intensity] };
    }

    public static PatternDto ParseFile(string path) => Parse(File.ReadLines(path));
}
