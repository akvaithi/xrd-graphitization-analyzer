import SwiftUI

/// In-app help: what the tool does, how each tab works, and the method behind it.
struct HelpView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                header

                section("Overview", [
                    "This app measures the **Degree of Graphitization (DG%)** of carbon materials from a powder-XRD scan of the carbon (002) reflection. It implements the NETL standard: a PsdVoigt1 deconvolution of the (002) peak, Bragg d-spacings, an area-weighted d′, and the Maire–Mering equation — the same procedure done by hand in OriginLab, validated to reproduce it within ~1%.",
                    "Load one or more `.xy` files (⌘O or the + button). Each file is auto-fit and listed in the sidebar; pick one to analyze it interactively.",
                ])

                section("Analyze tab", [
                    "**DG% readout** — the result with its statistical uncertainty (± σ) on the line below, plus a *range* across the defensible deconvolution choices. A wide range means the answer is sensitive to how the peak is split (see Uncertainty).",
                    "**Peaks (1 / 2)** — one peak for a clean graphitic (002); two peaks to also fit a broad low-angle *turbostratic* shoulder (the usual case for Fe-catalyzed coke).",
                    "**Lock turbostratic 2θ** — pin the turbostratic peak to a value you type, instead of letting the fit place it.",
                    "**Subtract sloped background** — remove a linear background across the window before fitting.",
                    "**Internal-std calib** — correct a 2θ misalignment using a residual phase (Fe₃C / α-Fe / CaO) as a built-in reference. Applied only when the lines agree and the offset clears the noise floor.",
                    "**Anchor (002)** — manually shift the whole pattern so the (002) sits at a known angle (use only with real evidence of sample displacement).",
                    "**Suggest deconvolution** — a bundled on-device model proposes the setup (peak count, turbostratic position, background, displacement) from numeric features; you confirm it. Nothing leaves your machine.",
                    "**Reset** — restore the import defaults. **Chart / Report** — save the fit plot (PNG) or a parameter report (CSV). Your choices persist per-file as you navigate.",
                ])

                section("Compare tab", [
                    "Scatter any result metric (DG, Lc, d′, graphitic 2θ) against a synthesis parameter (temperature, CaCO₃, dwell, Fe, carbon), colored by carbon type / form / wash.",
                    "Tick runs to include or exclude them; export the table as CSV or the chart as PNG.",
                ])

                section("Stack spectra tab", [
                    "Overlay the raw intensities of several runs to compare peak heights. Offset 0 = flat overlay; drag up for a waterfall. Optionally zoom to the (002) window and subtract a baseline. Export the chart as PNG.",
                ])

                section("Manual calc tab", [
                    "Compute DG directly from peak parameters you read off an OriginLab fit (1 or 2 peaks: centre + area) — the same arithmetic as the NETL spreadsheet.",
                ])

                section("The method", [
                    "**Profile** — OriginLab PsdVoigt1 (area-normalized pseudo-Voigt) with a free shared baseline y0: a graphitic peak (free Lorentzian fraction μ) plus a pure-Lorentzian (μ=1) turbostratic peak.",
                    "**d-spacing** — Bragg: d = λ / (2·sin θ), with Cu Kα λ = 1.54187 Å.",
                    "**DG%** — Maire–Mering: DG = (0.3440 − d′)/(0.3440 − 0.3354) × 100, where d′ is the area-weighted mean d-spacing (0.3354 nm = perfect graphite, 0.3440 nm = fully turbostratic).",
                    "**Crystallite height** — Scherrer Lc = 0.89·λ / (B·cos θ) from the graphitic FWHM (reported as *apparent*; no instrumental-broadening correction).",
                ])

                section("Calibration & data quality", [
                    "**Specimen displacement** shifts every peak by ≈ −(2s/R)·cosθ. DG is very sensitive to 2θ (~1.4% per 0.01°), so a small misalignment matters. The internal-standard tool measures the offset from residual-phase peaks; only correct when you have real evidence.",
                    "**Impurity scan** flags residual catalyst/carbonate (Fe, Fe₃C, CaO, calcite) by phase and intensity. These lie outside the (002) window so they don't change DG — they tell you whether acid washing was complete.",
                ])

                section("Uncertainty", [
                    "The **± σ** is the statistical 1σ from the fit covariance — usually small. The **range** is the spread across defensible deconvolution choices (2-peak, 1-peak, broad-low turbostratic) and is the dominant, honest uncertainty: when least-squares and expert judgment disagree, the true value lies within it.",
                ])

                Divider()
                about
            }
            .padding(28)
            .frame(maxWidth: 680, alignment: .leading)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("XRD Graphitization Analyzer").font(.system(size: 22, weight: .bold))
            Text("Help & reference").font(.title3).foregroundStyle(.secondary)
        }
    }

    private func section(_ title: String, _ items: [String]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.system(size: 16, weight: .semibold)).foregroundStyle(.tint)
            ForEach(items, id: \.self) { item in
                HStack(alignment: .top, spacing: 8) {
                    Text("•").foregroundStyle(.secondary)
                    Text(.init(item)).fixedSize(horizontal: false, vertical: true)   // markdown bold
                }
                .font(.system(size: 13))
            }
        }
    }

    private var about: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("About").font(.system(size: 16, weight: .semibold)).foregroundStyle(.tint)
            Text("Created by **Arun Vaithianathan** for the NETL / ARPA-E “graphite from petroleum coke” project at Texas A&M University.")
                .font(.system(size: 13)).fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 16) {
                Link("Portfolio — akvaithi.page", destination: URL(string: "https://akvaithi.page")!)
                Link("Source on GitHub", destination: URL(string: "https://github.com/akvaithi/xrd-graphitization-analyzer")!)
            }
            .font(.system(size: 13))
        }
    }
}
