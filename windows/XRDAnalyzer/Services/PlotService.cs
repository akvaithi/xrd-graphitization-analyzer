using ScottPlot;
using XRDAnalyzer.Engine.Models;

namespace XRDAnalyzer.Services;

/// Renders the (002) fit chart: raw points + graphitic/turbostratic/total
/// curves. Colors and explicit axis domains mirror `FitChartView`
/// (native/Sources/XRDApp/FitChartView.swift) — ScottPlot only plots points
/// computed by the bridge (`xrd_curve`), never re-derives them.
public static class PlotService
{
    private static readonly Color GraphiticColor = Color.FromHex("#FF3B30");
    private static readonly Color TurbostraticColor = Color.FromHex("#30B0C7");
    private static readonly Color FitColor = Color.FromHex("#5E5CE6");
    private static readonly Color RawColor = Colors.Gray;

    public static void Render(Plot plot, DGResultDto result, CurveSeriesDto? curve)
    {
        plot.Clear();

        var raw = plot.Add.ScatterPoints(result.PointsX, result.PointsY);
        raw.Color = RawColor;
        raw.MarkerSize = 4;
        raw.LegendText = "Raw data";

        if (curve is not null)
        {
            if (curve.Graphitic.Length > 0)
            {
                var g = plot.Add.Scatter(curve.Graphitic.Select(p => p.X).ToArray(), curve.Graphitic.Select(p => p.Y).ToArray());
                g.Color = GraphiticColor;
                g.LineWidth = 2;
                g.MarkerSize = 0;
                g.LegendText = "Graphitic";
            }
            if (curve.Turbostratic.Length > 0)
            {
                var t = plot.Add.Scatter(curve.Turbostratic.Select(p => p.X).ToArray(), curve.Turbostratic.Select(p => p.Y).ToArray());
                t.Color = TurbostraticColor;
                t.LineWidth = 2;
                t.MarkerSize = 0;
                t.LegendText = "Turbostratic";
            }
            if (curve.Total.Length > 0)
            {
                var total = plot.Add.Scatter(curve.Total.Select(p => p.X).ToArray(), curve.Total.Select(p => p.Y).ToArray());
                total.Color = FitColor;
                total.LineWidth = 2.5f;
                total.MarkerSize = 0;
                total.LinePattern = LinePattern.Dashed;
                total.LegendText = "Total fit";
            }
        }

        // Explicit domains so the axes match the data (auto-scaling can drift).
        var xs = result.PointsX;
        double xlo = xs.Length > 0 ? xs.Min() : 24, xhi = xs.Length > 0 ? xs.Max() : 28.5;
        var allY = result.PointsY.Concat(curve?.Total.Select(p => p.Y) ?? []).ToArray();
        double ymax = allY.Length > 0 ? allY.Max() : 1;
        double ymin = Math.Min(result.Y0, allY.Length > 0 ? allY.Min() : 0);
        double pad = Math.Max((ymax - ymin) * 0.06, 1e-6);

        plot.Axes.SetLimitsX(xlo, xhi);
        plot.Axes.SetLimitsY(ymin - pad, ymax + pad);
        plot.Axes.Bottom.Label.Text = "2θ  (degrees)";
        plot.Axes.Left.Label.Text = "Intensity  (a.u.)";
        plot.ShowLegend(Alignment.UpperLeft);
    }
}
