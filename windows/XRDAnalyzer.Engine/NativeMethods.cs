using System.Runtime.InteropServices;

namespace XRDAnalyzer.Engine;

/// P/Invoke surface for the Swift `XRDBridge.dll` (native/Sources/XRDBridge).
/// Every function takes one UTF-8 JSON string and returns one heap-allocated
/// UTF-8 JSON string that must be released with `xrd_free`.
internal static partial class NativeMethods
{
    private const string Lib = "XRDBridge";

    [LibraryImport(Lib, StringMarshalling = StringMarshalling.Utf8)]
    internal static partial nint xrd_fit(string reqJson);

    [LibraryImport(Lib, StringMarshalling = StringMarshalling.Utf8)]
    internal static partial nint xrd_range(string reqJson);

    [LibraryImport(Lib, StringMarshalling = StringMarshalling.Utf8)]
    internal static partial nint xrd_curve(string reqJson);

    [LibraryImport(Lib, StringMarshalling = StringMarshalling.Utf8)]
    internal static partial nint xrd_impurities(string reqJson);

    [LibraryImport(Lib, StringMarshalling = StringMarshalling.Utf8)]
    internal static partial nint xrd_crystallinity(string reqJson);

    [LibraryImport(Lib, StringMarshalling = StringMarshalling.Utf8)]
    internal static partial nint xrd_yield(string reqJson);

    [LibraryImport(Lib, StringMarshalling = StringMarshalling.Utf8)]
    internal static partial nint xrd_parse_run(string reqJson);

    [LibraryImport(Lib, StringMarshalling = StringMarshalling.Utf8)]
    internal static partial nint xrd_manual(string reqJson);

    [LibraryImport(Lib, StringMarshalling = StringMarshalling.Utf8)]
    internal static partial nint xrd_ai_suggest(string reqJson);

    [LibraryImport(Lib)]
    internal static partial void xrd_free(nint ptr);
}
