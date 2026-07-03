namespace XRDAnalyzer.Services;

/// Filesystem-safe filename stems for exports. Mirrors `String.fileSafe`
/// (native/Sources/XRDApp/ChartExport.swift) — Windows forbids more path
/// characters than macOS, so this strips the same science glyphs plus the
/// extra reserved set (`\ * ? " < > |`) rather than just `/` and `:`.
public static class FileNaming
{
    private static readonly Dictionary<char, string> GlyphMap = new()
    {
        ['°'] = "", ['′'] = "'", ['″'] = "''", ['×'] = "x", ['±'] = "",
        ['₀'] = "0", ['₁'] = "1", ['₂'] = "2", ['₃'] = "3", ['₄'] = "4",
        ['₅'] = "5", ['₆'] = "6", ['₇'] = "7", ['₈'] = "8", ['₉'] = "9",
        ['α'] = "alpha", ['β'] = "beta",
    };
    private static readonly char[] Reserved = ['/', ':', '\\', '*', '?', '"', '<', '>', '|'];

    public static string SafeFileName(string name)
    {
        var sb = new System.Text.StringBuilder();
        foreach (var c in name)
        {
            if (GlyphMap.TryGetValue(c, out var replacement)) sb.Append(replacement);
            else if (Array.IndexOf(Reserved, c) >= 0) sb.Append('-');
            else if (c >= 0x20 && c != 0x7F) sb.Append(c);
        }
        var collapsed = System.Text.RegularExpressions.Regex.Replace(sb.ToString(), " {2,}", " ").Trim();
        return collapsed.Length == 0 ? "export" : collapsed;
    }
}
