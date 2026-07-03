using System.Globalization;
using System.Text;
using XRDAnalyzer.Engine.Models;

namespace XRDAnalyzer.Services;

/// Builds the per-file key/value report CSV and the consolidated runs CSV.
/// Mirrors `ReportBuilder` (native/Sources/XRDApp/ReportBuilder.swift) exactly
/// so exports from either platform have the same shape.
public static class ReportBuilder
{
    private static string F(double v, int decimals) => v.ToString("F" + decimals, CultureInfo.InvariantCulture);
    private static string Signed(double v) => (v >= 0 ? "+" : "") + v.ToString("F3", CultureInfo.InvariantCulture);

    public static string Csv(string displayName, string fileName, DGResultDto r,
        DGRangeDto? span, ImpurityScanDto? quality, CrystallinityDto? crystallinity = null)
    {
        var rows = new List<(string Key, string Value)>
        {
            ("sample", displayName), ("file", fileName),
            ("method", r.MethodName), ("wavelength_angstrom", F(r.Wavelength, 5)),
            ("DG_percent", F(r.DgPercent, 2)),
            ("DG_sigma", r.DgSigma is { } sg ? F(sg, 2) : ""),
        };
        if (span is not null)
        {
            rows.Add(("DG_range_low", F(span.Low, 2)));
            rows.Add(("DG_range_high", F(span.High, 2)));
        }
        rows.Add(("peak_count", r.PeakCount.ToString()));
        rows.Add(("graphitic_2theta", F(r.Graphitic.Xc, 4)));
        rows.Add(("graphitic_FWHM", F(r.Graphitic.W, 4)));
        rows.Add(("graphitic_mu", F(r.Graphitic.Mu, 4)));
        rows.Add(("graphitic_d_nm", F(r.Graphitic.DSpacing, 5)));
        if (r.Turbostratic is { } t)
        {
            rows.Add(("turbostratic_2theta", F(t.Xc, 4)));
            rows.Add(("turbostratic_FWHM", F(t.W, 4)));
            rows.Add(("turbostratic_d_nm", F(t.DSpacing, 5)));
        }
        rows.Add(("X_graphitic", F(r.AreaFractionGraphitic, 4)));
        rows.Add(("X_turbostratic", F(r.AreaFractionTurbostratic, 4)));
        rows.Add(("d_prime_nm", F(r.DPrimeWeighted, 5)));
        rows.Add(("crystallite_Lc_A", F(r.CrystalliteLc, 1)));
        rows.Add(("baseline_y0", F(r.Y0, 3)));
        rows.Add(("two_theta_offset", Signed(r.TwoThetaOffset)));
        rows.Add(("fit_R2", F(r.FitR2, 5)));
        if (crystallinity is { } c)
        {
            rows.Add(("crystalline_fraction_pct", F(c.CrystallineFraction * 100, 1)));
            rows.Add(("disordered_fraction_pct", F(c.DisorderedFraction * 100, 1)));
            rows.Add(("crystallinity_fit_R2", F(c.FitR2, 4)));
        }
        if (quality is not null) rows.Add(("data_quality", quality.Verdict));

        var sb = new StringBuilder("key,value\n");
        foreach (var (key, value) in rows) sb.Append(key).Append(",\"").Append(value).Append("\"\n");
        return sb.ToString();
    }

    public static string ConsolidatedHeader() =>
        "file,carbon_type,carbon_ratio,fe_ratio,caco3_ratio,temperature_C,time_h,form,wash,DG,crystallinity_pct,disordered_pct,Lc,d_prime,graphitic_xc\n";

    public static string ConsolidatedRow(string fileName, RunInfoDto? info, DGResultDto? r, CrystallinityDto? crystallinity = null)
    {
        static string N(double? v) => v?.ToString("G", CultureInfo.InvariantCulture) ?? "";
        static string Q(string s) => s.Contains(',') ? $"\"{s}\"" : s;

        var cols = new List<string>
        {
            Q(fileName), info?.CarbonType ?? "", N(info?.CarbonRatio), N(info?.FeRatio), N(info?.Caco3Ratio),
            info?.TemperatureC?.ToString() ?? "", N(info?.TimeH), info?.Form ?? "", info?.Wash ?? "",
            r is not null ? F(r.DgPercent, 2) : "",
            crystallinity is not null ? F(crystallinity.CrystallineFraction * 100, 1) : "",
            crystallinity is not null ? F(crystallinity.DisorderedFraction * 100, 1) : "",
            r is not null ? F(r.CrystalliteLc, 1) : "",
            r is not null ? F(r.DPrimeWeighted, 5) : "",
            r is not null ? F(r.Graphitic.Xc, 4) : "",
        };
        return string.Join(",", cols) + "\n";
    }
}
