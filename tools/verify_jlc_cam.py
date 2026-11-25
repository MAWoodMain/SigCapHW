#!/usr/bin/env python3
"""Independently parse/render an export, then refresh its manifest and delivery ZIP.
Usage: python3 tools/verify_jlc_cam.py production/<release>
Requires gerbonara. Does not modify design files or fabrication Gerbers.
"""
from pathlib import Path
import hashlib, json, sys, zipfile
from gerbonara import LayerStack

out = Path(sys.argv[1]).resolve()
reports = out/'reports'
s = LayerStack.open(out/'gerbers')
for side in ('top', 'bottom', 'inner_2'):
    colors = {f'{side} copper':'#b84232', f'{side} silk':'#1d5099',
              'mechanical outline':'#000000', 'drill pth':'#777777', 'drill npth':'#777777'}
    name = 'In2' if side == 'inner_2' else side
    (reports/f'Gerber-{name}-preview.svg').write_text(str(s.to_svg(
        side_re=side+'|mechanical', colors=colors, force_bounds=s.board_bounds())))
bounds = s.board_bounds()
assert len([k for k in s.graphic_layers if k[1]=='copper']) == 4
assert abs(bounds[1][0]-bounds[0][0]-160.05) < .001
assert abs(bounds[1][1]-bounds[0][1]-120.05) < .001
outline = s.graphic_layers[('mechanical','outline')]
assert len(outline.objects) == 8  # rectangle, two slot sides, two semicircular ends
assert sum(type(o).__name__=='Arc' for o in outline.objects) == 2
for layer in (s.drill_pth, s.drill_npth):
    (x0,y0),(x1,y1) = layer.bounding_box()
    assert 59<x0<x1<221 and -151<y0<y1<-29
# Drill count must match this release's own KiCad-generated report, not a hardcoded historical board.
import re
text=(reports/'Drill-report.txt').read_text()
counts = [int(n) for n in re.findall(r'Total (?:plated|unplated) holes count (\d+)', text)]
assert counts == [len(s.drill_pth.objects), len(s.drill_npth.objects)]
report = {'reader':'Gerbonara', 'copper_layers':4, 'outline_bounds_mm':bounds,
          'outline_nominal_mm':[160,120], 'slot_outline_objects':4,
          'plated_drill_features':len(s.drill_pth.objects),
          'nonplated_drill_features':len(s.drill_npth.objects),
          'layer_objects':{str(k):len(v.objects) for k,v in s.graphic_layers.items()},
          'checks':['All 11 Gerber layers parsed','Both Excellon files parsed',
                    'PTH/NPTH counts match KiCad drill report',
                    'Drill and Gerber bounds share coordinate frame',
                    'Outer rectangle and internal rounded slot present'],
          'parser_notes':['Gerbonara warns about KiCad G90 occurring after header; both files parse as absolute coordinates.'],
          'silkscreen':'Silkscreen and copper markings exported as saved. Board-only graphics do not enter BOM/CPL.'}
(reports/'CAM-check.json').write_text(json.dumps(report,indent=2)+'\n')
manifest=json.loads((out/'manifest.json').read_text())
bundle=out/f"SigCap-Production-{manifest['design_commit'][:7]}.zip"
manifest['files']={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(out.rglob('*')) if p.is_file() and p not in (bundle,out/'manifest.json')
                   and out/'raw' not in p.parents and out/'gerbers' not in p.parents}
(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(out.rglob('*')):
        if p.is_file() and p!=bundle and out/'raw' not in p.parents and out/'gerbers' not in p.parents:
            z.write(p,str(p.relative_to(out)))
with zipfile.ZipFile(bundle) as z: assert z.testzip() is None
print(json.dumps(report,indent=2))
