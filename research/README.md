# `research/` — amorphous accounting & calibration

Exploratory tooling around one open question in the catalytic-graphitization
project:

> **Our DG% is high, but is the product *actually* mostly graphite?**

The short answer is that **DG% can't tell you** — and this module explains why,
quantifies the gap on our own scans, and lays out how to close it. It is kept
separate from the shipping engine (`xrd_analyzer.py` / the Swift core) on
purpose: nothing here changes the validated DG method.

---

## 1. What XRD actually measures (and what DG% throws away)

A single (002) reflection carries **three independent** pieces of information:

| Observable | Physical meaning | Engine status |
|---|---|---|
| Peak **position** (d₀₀₂) | ordering *quality* → **DG%** (Maire–Mering) | ✅ `fit_netl` |
| Peak **width** (FWHM) | crystallite *size* Lc (Scherrer) | ✅ `fit_netl` |
| Integrated **area / intensity** | *amount* of crystalline graphite | ❌ was missing |

DG% is computed from **position only**. Amorphous carbon produces a broad
*diffuse* band with **no sharp peak**, so it never enters the d-spacing average —
it is literally "drowned out" beneath the graphitic + turbostratic peaks. **A
sample can read DG ≈ 97% while a large mass fraction is still amorphous.**

> **What does the (002) intensity "mean"?** Integrated (002) area ∝ the amount of
> coherently-stacked graphite in the beam (× structure factor, LP, etc.). But raw
> intensity is **not** comparable between samples — sample height, packing, mass
> and detector auto-scale all move it (our scans show Imax ≈ 150–600 for
> chemically similar material). So "taller peak ⇒ more graphite" is a trap.
> Intensity becomes meaningful only after **normalization** (to total scatter or
> to an internal standard) or via a **calibration** — which is what §3 is for.

So: **position = quality, width = size, normalized area = amount.** DG% is one of
the three. This module adds the third.

---

## 2. How the field handles the amorphous problem

- **Known-mixture calibration** (coal/biochar): scan physical blends of pure
  amorphous + pure crystalline at known ratios; regress an XRD metric vs wt%.
- **Internal-standard / RIR spiking** (the Rietveld QPA gold standard): add a
  known wt% of Si or corundum; crystalline phases are quantified absolutely and
  **amorphous = 100 − Σ crystalline**.
- **XRD crystallinity index** (Ruland / Franklin): sharp-peak area ÷ (sharp +
  amorphous halo). Model-dependent from a single pattern — needs a standard to
  anchor it to true wt%.
- **TGA burn-off**: amorphous carbon oxidizes early (~400–550 °C), graphitic late
  (~600–800 °C); deconvolve the DTG mass-loss-rate curve. Independent, mass-based.
- **Raman** `I_D/I_G` and the 2D band: orthogonal probe of disorder/defects.

**Recommended path for us** (we currently have *plans*, not standards):

1. **Primary — physical PC↔synthetic-graphite mixtures.** Cheapest, uses the raw
   PC + the MTI 20 µm synthetic graphite we already benchmark against. Scan
   0/25/50/75/100 wt%. *Caveat:* assumes our product's crystalline/amorphous
   phases resemble the end-members.
2. **Cross-check — internal-standard spiking (Si or corundum).** Most rigorous;
   absolute wt% + amorphous-by-difference, free of the mixture assumption.
   Validate the mixture curve against a few spiked samples.
3. **Independent confirmation — TGA burn-off.** Orthogonal (mass, not
   diffraction); also calibrates the simulation's burn sub-model.

The tooling below works on existing scans **now** and activates the absolute
calibration the moment those standards are scanned.

---

## 3. The modules

| File | What it does | Run |
|---|---|---|
| `amorphous.py` | Decomposes the (002) into a sharp crystalline peak vs a broad disordered band → **crystallinity index** (amount). Optional 3-way amorphous/turbostratic split, flagged uncertain. | `python research/amorphous.py <file.xy>` |
| `calibration.py` | Turns the index into **absolute crystalline-graphite wt%** via mixture or internal-standard calibration. Self-test validates the math on synthetic blends. | `python research/calibration.py --selftest` / `--manifest cal.csv` |
| `yield_calc.py` | **Carbon/graphite mass yield** from weighed masses. Ports the group's yield spreadsheet (CaCO₃→CaO+CO₂, Boudouard, CaO+S→CaS, wash/trapped-metal), reconciles the furnace loss against the measured mass, and reports **crystalline-graphite yield = mass yield × crystallinity index**. Only the **post-furnace** mass is required (post-acid is optional → wash QC). `--plots` draws yield vs CaCO₃/Fe/temperature. Mirrored in Swift (`XRDCore/YieldCalc.swift`) and surfaced as an optional **Yield tab** in the macOS app (masses persist in each scan's sidecar). | `python research/yield_calc.py --selftest` / `--manifest runs.csv --plots out/` |
| `trends.py` | Batch EDA over the scan folder: every metric vs temperature / time / Fe% / CaCO₃ / grade; emits a CSV + plots, incl. the DG%-vs-crystalline divergence. | `python research/trends.py --csv out.csv --plots plots/` |
| `_shared.py` | Bridge to the engine + the cross-sample **normalization** primitives. | — |

### Crystallinity index (`amorphous.py`)
Primary metric is the **robust 2-component** split (one sharp peak, one broad
band): `crystalline_fraction = A_sharp / (A_sharp + A_broad)`. The amorphous↔
turbostratic sub-split (`subdivide=True`) is **diagnostic only** — from one
pattern it is genuinely non-unique, so it's reported with a `broad_split_reliable`
flag and should be confirmed by Raman/TGA. Every fraction is a model-dependent
**index, not wt%** — convert via `calibration.py`.

### Calibration (`calibration.py`)
Manifest CSV: `file, graphite_wt_pct[, standard_wt_pct]`. The default metric
`norm_002_area` = sharp-(002) area ÷ total scatter (the comparable yardstick).
The fit auto-selects linear vs quadratic (the metric↔wt% map is mildly curved
because the normalizer also moves with composition). `--selftest` builds known
synthetic blends and confirms recovery (held-out MAE < 5 wt%).

> The mass-balance + kinetics process simulation (`simulate.py`) has moved to the
> sibling [coke-graphitization-sim](https://github.com/akvaithi/coke-graphitization-sim)
> repo (private), which is also where ReaxFF/atomistic simulation work will live.
> This repo stays scoped to XRD analysis and yield calculations over real data.

---

## 4. What the data already shows

Running `trends.py` over a scan set typically shows samples with **high DG%** that
nonetheless span a **wide range of crystalline fraction** — i.e. a meaningful part
of the carbon is disordered/amorphous yet invisible to DG%. The crystalline fraction
also tracks treatment temperature (rising and saturating), which is exactly the
quantity DG% saturates on. (Run it on your own scans for the actual numbers — that
folder is gitignored.)

**Bottom line:** report DG% (quality) **and** a normalized crystalline metric
(amount) together, and anchor the amount to physical standards before quoting an
absolute "% graphite". The amorphous fraction is real, it moves with the recipe,
and it is the missing axis in the current method.

---

---

## 5. Sources & assumptions

The crystallinity index is an **integrated-area ratio computed per scan** (sharp
graphitic peak ÷ total resolved (002)-region scattering of that same scan — *not*
peak height, and *not* normalized against other samples). The method class is
standard carbon-XRD practice; the exact window/bounds are our engineering,
validated only by internal consistency (monotonic with temperature; unstable
amorphous↔turbostratic sub-split, which is why it's flagged).

- **Warren**, *Phys. Rev.* 59 (1941) 693 — turbostratic random-layer scattering.
  [DOI](https://link.aps.org/doi/10.1103/PhysRev.59.693)
- **Franklin**, *Proc. R. Soc. A* 209 (1951) 196 — Lc / d₀₀₂ crystallite parameters.
  [DOI](https://royalsocietypublishing.org/doi/10.1098/rspa.1951.0197)
- **Maire & Méring**, *Chem. Phys. Carbon* 6 (1970) 125–190 — degree-of-graphitization
  (DG%). Book chapter (Marcel Dekker), no article DOI; volume record:
  [OSTI](https://www.osti.gov/biblio/4445451)
- **Iwashita et al.**, *Carbon* 42 (2004) 701–714 — standard XRD procedure for carbons.
  [DOI](https://doi.org/10.1016/j.carbon.2004.02.008)
- **Lu, Sahajwalla et al.**, *Carbon* 39 (2001) 1821–1833 — (002) crystalline+amorphous
  deconvolution & area ratios (closest published analogue to our index).
  [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0008622300003183)
- **Ruland & Smarsly**, *J. Appl. Cryst.* 35 (2002) 624 — why an area ratio is an
  *index*, not a mass fraction (rigorous fractions need total coherent scattering).
  [DOI](https://doi.org/10.1107/S0021889802011007)

**Central caveat (Ruland & Smarsly):** amorphous and crystalline carbon don't
scatter equally per gram, so the index *ranks* samples reliably but is not wt%
until calibrated against physical standards (§3).

### Process-chemistry references (yield / simulation)

Background for the iron-catalyzed graphitization mechanism and the yield chemistry
(CaCO₃ decomposition, Boudouard etching, sulfur trapping) modeled in `yield_calc.py`
(and, for the process mass-balance, in the sibling `coke-graphitization-sim` repo):

- **Iron-catalyzed graphitization mechanism** — "Elucidating the Mechanism of
  Iron-Catalyzed Graphitization: The First Observation of Homogeneous Solid-State
  Catalysis." [ResearchGate](https://www.researchgate.net/publication/382296563_Elucidating_the_Mechanism_of_Iron-Catalyzed_Graphitization_The_First_Observation_of_Homogeneous_Solid-State_Catalysis)
- **Sulfur trapping in pet coke** — Majumder et al., *Environmental Protection
  Research* 3(2) (2023) 341–348, "In-situ Formation of Sulphur-Trapped Petroleum
  Coke via Thermal Cracking of Vacuum Residue." [WiserPub](https://ojs.wiserpub.com/index.php/EPR/article/view/2992)
- **Pet coke gasification / carbon loss** — *Fuel* (Elsevier, 2021).
  [ScienceDirect](https://www.sciencedirect.com/science/article/pii/S0016236121006244)
- **Explainer video** — [youtu.be/8gncxR-fP7w](https://youtu.be/8gncxR-fP7w)

(See also the primary paper — Banavath et al., *npj Mater. Sustain.* 4:23 (2026),
"Low-temperature catalytic upcycling of petroleum coke into battery-grade
graphite," the low-temperature Fe-catalytic route this project builds on.
[Nature](https://www.nature.com/articles/s44296-026-00115-w))

---

## Notes
- Pure Python; deps already in `requirements.txt` (numpy/scipy/matplotlib).
- Reads `DATA/xrd scans/` by default; that folder is gitignored (proprietary).
- Tests: `pytest tests/test_research.py` (skips data-dependent checks cleanly).
- A Swift port of the index lives in `native/Sources/XRDCore/Crystallinity.swift`
  (mirrors `decompose`), surfaced in the macOS app's Analyze pane and checked by a
  Python↔Swift parity test — so the app and these scripts report one number.
