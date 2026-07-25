# NOAA NEXRAD Viewer

A focused local browser for native NOAA NEXRAD Level III
super-resolution base-reflectivity sequences. This is intentionally one
Sigvue `Workspace`, so opening the application goes directly to NEXRAD
sequence discovery instead of showing a workspace catalog.

## Preview

The single-workspace landing page presents every downloaded radar sequence in
one searchable table, including its scan count and time coverage:

![NEXRAD sequence discovery table](figures/main.png)

Opening a sequence provides segmented playback, display controls, and the
native Level III base-reflectivity PPI:

![Interactive 120 km NEXRAD plan-position display](figures/120_ppi.png)

The same workspace can render every native scan as a deterministic,
full-resolution animation:

<p align="center">
  <img
    src="figures/twx-topeka-twx-n0b-ppi-120km-200ms.gif"
    alt="Animated TWX Topeka NEXRAD base-reflectivity sequence"
    width="720"
  >
</p>

The repository is self-contained:

```text
src/nexrad_viewer/
├── formats/nexrad/    exact native Level III models and parser
├── reader.py          chronological sequence discovery
├── workspace.py       one Reader + one view callback + one Workspace
├── view.py            controls, statistics, tabs, tables, and layout
├── analysis.py        exact grids and sequence-wide histogram limits
├── plots.py           PPI and native-gate distribution figures
├── batch.py           durable full-resolution GIF rendering
├── style.py           local Plotly styling
├── cli.py             application-specific Sigvue console command
├── desktop.py         native pywebview window and server lifetime
├── runtime.py         durable data/output roots and generated profile
└── _packaging/        installed PyInstaller build support

scripts/
└── download_data.py   repository-only NOAA archive downloader
```

## Install and download

Install Sigvue and this repository:

```bash
python -m pip install -e ../Scientific-Workspace-Browser
python -m pip install -e .
```

Download ten fixed one-hour cases from TLX, FDR, VNX, ICT, DDC, INX, SGF,
EAX, OAX, and TWX:

```bash
python scripts/download_data.py
```

The script queries NOAA's public Level III bucket for the selected fixed
prefixes. Downloads are atomic and checked against the bucket's object size
and, when supplied as a simple ETag, MD5 digest. No filename manifest is
needed. It is repository test/data tooling and is intentionally absent from
the installed package, wheel, and console entry points. For a small trial:

```bash
python scripts/download_data.py \
  --cases tlx-oklahoma-city \
  --scans-per-case 3
```

See the available scan counts and total download size without downloading:

```bash
python scripts/download_data.py --list
```

## Run

```bash
nexrad-viewer
```

This is an application-specific wrapper around the regular Sigvue CLI. It
uses the checkout's `data/` and `outputs/` directories by default while
retaining the normal server and batch options:

```bash
nexrad-viewer --port 9000
nexrad-viewer --data-root /path/to/radar --output-root /path/to/results
nexrad-viewer batch --list
```

An explicit profile remains supported when needed:

```bash
nexrad-viewer --config browser.toml
```

Open <http://127.0.0.1:8000>. Because the generated profile contains exactly
one workspace, the root page is the NEXRAD sequence browser. Its ten
collection folders are deliberately flattened into ten sequence rows without
changing their identifiers or source locations. Selecting a sequence opens
segmented scan playback with:

- previous, next, press-and-hold, automatic playback, looping, and step size;
- a progressive plan-position reflectivity display;
- the visual colormap picker, including the custom NEXRAD scale;
- fixed native dBZ limits and viewport-aware rasterization;
- exact native-gate distributions, statistics, and metadata.

Each sequence row also has a **Render full-resolution GIF** batch action. The
workspace-level action renders every discovered sequence. Frames use native
range-gate spacing over the configured radius, bypass viewport raster
reduction, loop continuously, and are saved deterministically under
`outputs/`. Existing GIFs are recognized as complete after a restart.

Scientific values remain native. Cartesian resampling and viewport
rasterization are display operations only.

## Native desktop application

Install the optional desktop toolchain:

```bash
python -m pip install -e ".[desktop]"
```

Run the same application in a native pywebview window during development:

```bash
nexrad-viewer-desktop
```

Build the platform-native PyInstaller artifact:

```bash
nexrad-viewer-build
```

On macOS this produces `dist/NEXRAD Viewer.app`; Windows and Linux produce
`dist/nexrad-viewer` with the platform's executable suffix where applicable.
PyInstaller builds are platform-specific, so each target must be built on its
target operating system.

When the checkout has a `data/` directory, the default build embeds it for a
self-contained read-only dataset. A smaller application can use an external
dataset:

```bash
nexrad-viewer-build --without-data
nexrad-viewer-desktop --data-root /path/to/radar
```

The frozen application never writes into its bundle or PyInstaller extraction
directory. GIF results persist under the operating system's application-data
directory:

- macOS: `~/Library/Application Support/NEXRAD Viewer/outputs`
- Windows: `%LOCALAPPDATA%\NEXRAD Viewer\outputs`
- Linux: `$XDG_DATA_HOME/nexrad-viewer/outputs`

pywebview uses the operating system's native web renderer, while the existing
Sigvue HTTP application remains the only UI implementation. Sigvue's existing
fullscreen button also remains the only fullscreen control: in the desktop
application it toggles the native window, while in an ordinary browser it
continues to use browser fullscreen. See the
[pywebview usage guide](https://pywebview.flowrl.com/guide/usage.html) and
[freezing guide](https://pywebview.flowrl.com/guide/freezing).

## Test

```bash
python -m pip install -e ".[test,release]"
python -m pytest -q
python -m build
python -m twine check dist/*
```

The source dataset is NOAA NEXRAD Open Data distributed through the NOAA Open
Data Dissemination program. NOAA requests attribution when the data is used.
