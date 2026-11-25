SigCap REV_A
Hardware design snapshot, 25 November 2026
Saved design source:04623dddc8d29e347838b2bcbd46a95dbdfea2b1

Release assets
- SigCap-REV_A-JLC.zip: fabrication Gerbers/drills, JLC BOM/CPL alternatives, manual-fit BOM,
  current parts list, placement audit, manufacturing notes and check reports.
- SigCap-REV_A-Design.zip: native KiCad project, custom footprint library and models,
  three-sheet schematic PDF, eight-layer PCB layout PDF, current BOM and review report.
- SHA256SUMS.txt: SHA-256 checksums of both downloadable archives.

The layout PDF is high-contrast monochrome, one named layer per page, top view, 1:1 scale.
Page order:F.Cu, In1.Cu, In2.Cu, B.Cu, F.SilkS, B.SilkS, F.Mask, B.Mask.
The bottom layers are viewed from above and have not been mirrored. Print at actual size.

The native project uses KiCad10 and its standard libraries. Open SigCap.kicad_pro.
Custom footprints and the two Bulgin models are included. Additional copies of those models
are placed beside the project in the design archive so the board's bare model filenames resolve.
Standard KiCad3D models are not duplicated in the archive.
NativeProject/BOM.csv is the historical allocation table used by the export tool for H1 metadata;
use BOM/Full_BOM.csv for the current complete BOM.

The two BOM/CPL pairs are alternatives, not files to combine:
- JLC_SMT:89 SMT placements, plus12 components to fit separately.
- JLC_Mixed:99 SMT/THT placements if supported by the selected assembly service.
J1 and H1 remain separately sourced/manual-fit parts. NT1 and artwork are not components.
Review assembly orientations and availability during the JLC order process.
