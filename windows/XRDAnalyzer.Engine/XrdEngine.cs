using System.Runtime.InteropServices;
using System.Text.Json;
using XRDAnalyzer.Engine.Models;

namespace XRDAnalyzer.Engine;

/// Thin JSON (de)serializing wrapper over `NativeMethods` — the only place
/// C# talks to the Swift engine. No DG/crystallinity/yield/calibration math
/// belongs here; it all lives in native/Sources/XRDCore + XRDBridge.
public static class XrdEngine
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    private static TResp Call<TReq, TResp>(Func<string, nint> nativeFn, TReq request)
    {
        var reqJson = JsonSerializer.Serialize(request, JsonOptions);
        nint ptr = nativeFn(reqJson);
        try
        {
            var json = Marshal.PtrToStringUTF8(ptr) ?? "{}";
            return JsonSerializer.Deserialize<TResp>(json, JsonOptions)
                ?? throw new InvalidOperationException($"empty response for {typeof(TResp).Name}");
        }
        finally
        {
            NativeMethods.xrd_free(ptr);
        }
    }

    public static FitResponse Fit(FitRequest request) =>
        Call<FitRequest, FitResponse>(NativeMethods.xrd_fit, request);

    public static RangeResponse Range(RangeRequest request) =>
        Call<RangeRequest, RangeResponse>(NativeMethods.xrd_range, request);

    public static CurveResponse Curve(CurveRequest request) =>
        Call<CurveRequest, CurveResponse>(NativeMethods.xrd_curve, request);

    public static ImpuritiesResponse Impurities(ImpuritiesRequest request) =>
        Call<ImpuritiesRequest, ImpuritiesResponse>(NativeMethods.xrd_impurities, request);

    public static CrystallinityResponse Crystallinity(CrystallinityRequest request) =>
        Call<CrystallinityRequest, CrystallinityResponse>(NativeMethods.xrd_crystallinity, request);

    public static YieldResponse Yield(YieldRequest request) =>
        Call<YieldRequest, YieldResponse>(NativeMethods.xrd_yield, request);

    public static ParseRunResponse ParseRun(ParseRunRequest request) =>
        Call<ParseRunRequest, ParseRunResponse>(NativeMethods.xrd_parse_run, request);

    public static ManualResponse Manual(ManualRequest request) =>
        Call<ManualRequest, ManualResponse>(NativeMethods.xrd_manual, request);

    public static AiSuggestResponse AiSuggest(AiSuggestRequest request) =>
        Call<AiSuggestRequest, AiSuggestResponse>(NativeMethods.xrd_ai_suggest, request);
}
