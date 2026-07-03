using XRDAnalyzer.Engine;
using XRDAnalyzer.Engine.Models;

namespace XRDAnalyzer.Tests;

/// Milestone-1 gate: P/Invoke through XRDBridge.dll on a synthetic (002)
/// pattern and confirm the DG matches the Python/Swift reference (see
/// tests/test_engine.py::test_python_swift_dg_parity, which generates the
/// same fixture via the same peak parameters and the same NETL two-peak fit).
public class EngineSmokeTests
{
    private const double ReferenceDgPercent = 84.36; // xrd_analyzer.fit_netl on the fixture below

    private static PatternDto LoadFixture() =>
        XyFile.ParseFile(Path.Combine(AppContext.BaseDirectory, "Fixtures", "synthetic_002.xy"));

    [Fact]
    public void Fit_TwoPeak_MatchesPythonSwiftReferenceDG()
    {
        var response = XrdEngine.Fit(new FitRequest
        {
            Pattern = LoadFixture(),
            Settings = new FitSettingsDto { PeakCount = 2 },
        });

        Assert.Null(response.Error);
        Assert.NotNull(response.Result);
        Assert.Equal(ReferenceDgPercent, response.Result!.DgPercent, 1); // 1 decimal place, matches the 0.05-wide parity tolerance
    }

    [Fact]
    public void Fit_ReturnsFittedCurvePoints()
    {
        var response = XrdEngine.Fit(new FitRequest
        {
            Pattern = LoadFixture(),
            Settings = new FitSettingsDto { PeakCount = 2 },
        });

        Assert.NotNull(response.Result);
        Assert.NotEmpty(response.Result!.PointsX);
        Assert.Equal(response.Result.PointsX.Length, response.Result.PointsY.Length);
        Assert.NotNull(response.Result.Graphitic);
        Assert.NotNull(response.Result.Turbostratic);
    }

    [Fact]
    public void ParseRun_ExtractsRecipeFromFileName()
    {
        var response = XrdEngine.ParseRun(new ParseRunRequest { FileName = "2GPC 6Fe 0.8125CaCO3 1200C 5h Puck.xy" });

        Assert.NotNull(response.Info);
        Assert.Equal("GPC", response.Info!.CarbonType);
        Assert.Equal(1200, response.Info.TemperatureC);
    }

    [Fact]
    public void Manual_TwoPeaks_ComputesDG()
    {
        var response = XrdEngine.Manual(new ManualRequest
        {
            Peaks =
            [
                new ManualPeakDto { Xc = 26.6, Area = 100 },
                new ManualPeakDto { Xc = 26.1, Area = 20 },
            ],
        });

        Assert.Null(response.Error);
        Assert.NotNull(response.Result);
        Assert.True(response.Result!.DgPercent is > 0 and <= 100);
    }
}
