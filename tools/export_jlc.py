#!/usr/bin/env python3
"""Export the committed SigCap design to JLCPCB formats. Requires kicad-cli and sexpdata.
Usage: python3 tools/export_jlc.py [output-directory]
Does not modify the design; stops if tracked design inputs differ from HEAD.
"""
from pathlib import Path
import collections, csv, datetime, hashlib, json, math, re, subprocess, sys, zipfile
import xml.etree.ElementTree as ET
import sexpdata

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ['SigCap.kicad_pcb', 'SigCap.kicad_pro', 'SigCap.kicad_sch',
          'acquisition.kicad_sch', 'afe.kicad_sch', 'BOM.csv']

def run(*args):
    p = subprocess.run(args, cwd=ROOT, check=True, text=True, capture_output=True)
    return p.stdout.strip()

def children(n, tag):
    return [x for x in n if isinstance(x, list) and x and str(x[0]) == tag]

def child(n, tag):
    return next(iter(children(n, tag)), None)

def natural(s):
    return [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', s)]

def write_csv(path, fields, rows):
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

def fmt(v):
    return f'{v:.6f}'

def main():
    if run('git', 'diff', 'HEAD', '--', *INPUTS):
        raise SystemExit('Commit the saved design inputs before exporting a release.')
    revision = run('git', 'rev-parse', 'HEAD')
    # User-facing release date follows the project timezone.
    from zoneinfo import ZoneInfo
    date = datetime.datetime.now(ZoneInfo('Europe/London')).date().isoformat()
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'production' / f'{date}-{revision[:7]}'
    out = out.resolve()
    if out.exists():
        raise SystemExit(f'Refusing to overwrite existing release: {out}')
    raw, gerbers, reports = out/'raw', out/'gerbers', out/'reports'
    for d in (raw, gerbers, reports): d.mkdir(parents=True)
    board = ROOT/'SigCap.kicad_pcb'
    schematic = ROOT/'SigCap.kicad_sch'
    run('kicad-cli', 'pcb', 'drc', '--refill-zones', '--schematic-parity', '--format', 'json',
        '-o', str(reports/'DRC.json'), str(board))
    drc = json.loads((reports/'DRC.json').read_text())
    if drc['unconnected_items'] or drc['schematic_parity']:
        raise SystemExit('Release has open connections or schematic parity issues; see reports/DRC.json.')
    layers = 'F.Cu,In1.Cu,In2.Cu,B.Cu,F.Mask,B.Mask,F.SilkS,B.SilkS,F.Paste,B.Paste,Edge.Cuts'
    run('kicad-cli', 'pcb', 'export', 'gerbers', '--layers', layers, '--check-zones',
        '--subtract-soldermask', '--precision', '6', '-o', str(gerbers), str(board))
    run('kicad-cli', 'pcb', 'export', 'drill', '--format', 'excellon', '--drill-origin', 'absolute',
        '--excellon-units', 'mm', '--excellon-zeros-format', 'decimal',
        '--excellon-oval-format', 'route', '--excellon-separate-th', '--generate-report',
        '--report-path', str(reports/'Drill-report.txt'), '-o', str(gerbers), str(board))
    run('kicad-cli', 'pcb', 'export', 'pos', '--format', 'csv', '--units', 'mm', '--exclude-dnp',
        '-o', str(raw/'KiCad-positions.csv'), str(board))
    run('kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
        '-o', str(raw/'netlist.xml'), str(schematic))
    run('kicad-cli', 'pcb', 'export', 'svg', '--mode-single', '--layers', 'F.Fab,F.SilkS,Edge.Cuts',
        '--fit-page-to-board', '--exclude-drawing-sheet', '-o', str(reports/'Assembly-top.svg'), str(board))

    drawing = reports/'Assembly-top.svg'
    drawing.write_text('\n'.join(line.rstrip() for line in drawing.read_text().splitlines())+'\n')

    pcb = sexpdata.loads(board.read_text())
    fps = {}
    for f in children(pcb, 'footprint'):
        p = {v[1]: v[2] for v in children(f, 'property')}
        fps[p['Reference']] = (f, p)
    comps = {}
    for c in ET.parse(raw/'netlist.xml').findall('./components/comp'):
        p = {v.get('name'): v.text or '' for v in c.findall('./fields/field')}
        props = {v.get('name') for v in c.findall('property')}
        comps[c.get('ref')] = (c, p, props)
    positions = {p['Ref']: p for p in csv.DictReader((raw/'KiCad-positions.csv').open())}
    rules = list(csv.reader((ROOT/'tools/vendor/fabrication-toolkit/transformations.csv').open()))[1:]
    with (ROOT/'BOM.csv').open() as f:
        historical = list(csv.DictReader(f))
    h1 = next(r for r in historical if r['Designator'] == 'H1')
    all_parts, smt, mixed, manual, distributors, audits = [], [], [], [], [], []
    for ref in sorted(fps, key=natural):
        f, p = fps[ref]
        attrs = {str(v) for v in (child(f, 'attr') or [])[1:]}
        if ref == 'NT1':
            continue  # Copper-only net tie; not an assembly component.
        if 'dnp' in attrs:
            continue
        c, fields, props = comps[ref]
        if 'dnp' in props or 'exclude_from_board' in props:
            continue
        if ('exclude_from_bom' in props or 'exclude_from_bom' in attrs) and ref != 'H1':
            continue
        pn = fields.get('LCSC Part #', '').strip()
        mpn = fields.get('MPN', '').strip()
        source, note = 'JLCPCB', ''
        if ref == 'J1':
            source = 'RS / Farnell / DigiKey'
            note = 'Distributor supplied; Farnell 428784; DigiKey 708-1914-ND; RS 70427399 (US listing).'
        elif ref == 'H1':
            pn, mpn = h1['LCSC Part #'], h1['MPN']
            source = 'LCSC / distributor; fit manually'
            note = 'Physical chassis terminal. Excluded in schematic BOM; retained here from allocated project BOM. Mating crimp not included.'
        elif not re.fullmatch(r'C\d+', pn):
            raise ValueError(f'{ref}: missing/invalid assigned JLC number: {pn!r}')
        is_smd = 'smd' in attrs
        mode = 'SMT' if is_smd else 'Through-hole / manual'
        row = dict(Designator=ref, Comment=c.findtext('value'), Footprint=f[1],
                   **{'LCSC Part #':pn, 'MPN':mpn, 'Quantity':1, 'Source':source,
                      'Assembly':mode, 'Notes':note})
        all_parts.append(row)
        if source == 'JLCPCB':
            mixed.append(row)
            if is_smd: smt.append(row)
        else:
            distributors.append(row)
        if not is_smd or source != 'JLCPCB': manual.append(row)
        if ref not in positions:
            if source == 'JLCPCB': raise ValueError(f'{ref}: no placement record')
            continue
        raw_pos = positions[ref]
        x,y,rot = map(float, (raw_pos['PosX'],raw_pos['PosY'],raw_pos['Rot']))
        base_x,base_y = x,y
        origin = 'KiCad SMD anchor'
        at=child(f,'at'); angle=math.radians(float(at[3]) if len(at)>3 else 0)
        if not is_smd:
            # Toolkit convention for THT: center of pad bounding boxes, including NPTH pads.
            bounds=[]
            for pad in children(f,'pad'):
                pa,sz=child(pad,'at'),child(pad,'size')
                if not pa or not sz: continue
                cx=at[1]+pa[1]*math.cos(angle)+pa[2]*math.sin(angle)
                cy=at[2]-pa[1]*math.sin(angle)+pa[2]*math.cos(angle)
                a=math.radians(float(pa[3]) if len(pa)>3 else 0)
                hx=(abs(math.cos(a))*sz[1]+abs(math.sin(a))*sz[2])/2
                hy=(abs(math.sin(a))*sz[1]+abs(math.cos(a))*sz[2])/2
                bounds.append((cx-hx,cy-hy,cx+hx,cy+hy))
            if bounds:
                x=(min(b[0] for b in bounds)+max(b[2] for b in bounds))/2
                y=-(min(b[1] for b in bounds)+max(b[3] for b in bounds))/2
                origin='Pad bounding-box center (THT)'
        if ref == 'R7':
            # Custom footprint body rectangle is offset from its anchor; do not move the board part.
            fab=next(v for v in children(f,'fp_rect') if child(v,'layer')[1]=='F.Fab')
            a,b=child(fab,'start'),child(fab,'end')
            dx,dy=(a[1]+b[1])/2,(a[2]+b[2])/2
            x+=dx*math.cos(angle)+dy*math.sin(angle)
            y+=dx*math.sin(angle)-dy*math.cos(angle)
            origin='Custom shunt F.Fab body center; export-only correction'
        match=next((r for r in rules if re.search(r[0], str(f[1]) if ':' in r[0] else str(f[1]).split(':')[-1])), None)
        delta=float(match[1] or 0) if match else 0
        dx,dy=(float(match[2] or 0),float(match[3] or 0)) if match else (0,0)
        x+=dx*math.cos(angle)-dy*math.sin(angle)
        y+=dx*math.sin(angle)+dy*math.cos(angle)
        r=(rot+delta)%360
        assert raw_pos['Side']=='top', 'Review bottom-side rotation handling before extending exporter.'
        audits.append(dict(Designator=ref,Footprint=f[1],**{
            'KiCad X':fmt(base_x),'KiCad Y':fmt(base_y),'KiCad Rotation':fmt(rot),
            'Mid X':fmt(x),'Mid Y':fmt(y),'Rotation':fmt(r),'Layer':'top',
            'Origin method':origin,'Rotation rule':match[0] if match else 'No toolkit rule; KiCad rotation retained',
            'Rotation correction':fmt(delta)}))
    by_ref={r['Designator']:r for r in audits}
    bom_fields=['Comment','Designator','Footprint','LCSC Part #']
    cpl_fields=['Designator','Mid X','Mid Y','Rotation','Layer']
    def assembly(name, rows):
        groups=collections.defaultdict(list)
        for r in rows:groups[(r['Comment'],r['Footprint'],r['LCSC Part #'])].append(r['Designator'])
        grouped=[dict(zip(('Comment','Footprint','LCSC Part #'),key),Designator=','.join(refs)) for key,refs in groups.items()]
        write_csv(out/f'{name}_BOM.csv',bom_fields,grouped)
        write_csv(out/f'{name}_CPL.csv',cpl_fields,[{k:by_ref[r['Designator']][k] for k in cpl_fields} for r in rows])
        refs={r['Designator'] for r in rows}
        assert refs=={v for g in grouped for v in g['Designator'].split(',')}
        assert len(refs)==len(rows)
    assembly('JLC_SMT',smt)
    assembly('JLC_Mixed',mixed)
    fields=['Designator','Comment','Footprint','LCSC Part #','MPN','Quantity','Source','Assembly','Notes']
    write_csv(out/'Full_BOM.csv',fields,all_parts)
    write_csv(out/'Manual_after_SMT.csv',fields,manual)
    write_csv(out/'Distributor_parts.csv',fields,distributors)
    write_csv(out/'Placement_audit.csv',list(audits[0]),audits)
    counts=collections.Counter(v['type'] for v in drc['violations'])
    (reports/'DRC-summary.txt').write_text('Open connections: 0\nSchematic parity issues: 0\nRemaining DRC findings (design exported as committed):\n'+''.join(f'{k}: {v}\n' for k,v in sorted(counts.items())))
    extensions={p.suffix.lower() for p in gerbers.iterdir()}
    assert {'.gtl','.g1','.g2','.gbl','.gts','.gbs','.gto','.gbo','.gtp','.gbp','.gm1','.drl'}<=extensions,extensions
    for p in gerbers.iterdir():assert p.stat().st_size>0
    fabzip=out/'SigCap-Gerbers.zip'
    with zipfile.ZipFile(fabzip,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(gerbers.iterdir()):z.write(p,p.name)
    notes=f'''SigCap JLCPCB production package
Design commit: {revision}
Exported: {date}; {run('kicad-cli','version')}

UPLOADS
1. Bare board: SigCap-Gerbers.zip (4 copper, both masks, both silks, both paste layers, outline and separate PTH/NPTH Excellon).
2. SMT assembly: JLC_SMT_BOM.csv + JLC_SMT_CPL.csv ({len(smt)} components, top side only).
   Fit the {len(manual)} components in Manual_after_SMT.csv separately.
3. Alternatively, if ordering mixed SMT/THT assembly: JLC_Mixed_BOM.csv + JLC_Mixed_CPL.csv ({len(mixed)} components).
   Confirm through-hole/manual insertion support and fees with JLCPCB. Distributor_parts.csv remains separate.
Do not mix the SMT BOM with the Mixed CPL, or vice versa.

BOARD SETTINGS
FR4, 4 layers, 1.6 mm, 160 x 120 mm nominal outer rectangle.
Layer order: F.Cu / In1.Cu / In2.Cu / B.Cu. Internal isolation slot is in Edge.Cuts.
Design uses 35 um copper layers and nominal 0.1 / 1.24 / 0.1 mm dielectrics.
Select and verify a compatible JLC stackup; do not assume the uploader infers dielectric spacing from Gerbers.
Gerbers and drills use the absolute origin. CPL uses that same origin with Y increasing upwards
(KiCad exports negative Y for this board); no mirroring, scaling or origin shifting applied.
Silkscreen has mask openings subtracted in the export; design text was not edited.

BOM AND PLACEMENT
All current part assignments come from the exported schematic, not the older root BOM.csv.
Exception: physical H1 is excluded from the schematic BOM; Manual_after_SMT.csv retains its previously allocated C86469 / 63849-1 from root BOM.csv.
J1 is distributor supplied (RS/Farnell/DigiKey). NT1 is copper only and omitted from assembly.
U3 is labelled optional on the drawing but is NOT DNP in the design: included in the Mixed BOM and manual-fit list.
C43/C44 are included. No other component substitutions have been made.
Stock/prices were not revalidated; JLC's order-time matching is authoritative.
Placement_audit.csv records every coordinate and rotation adjustment. Standard rotation corrections
come from Fabrication-Toolkit revision 7f627b6be76081b962161b9eb9fe3a326a18c915 (data and license in tools/vendor).
THT placements use pad bounding-box centers; SMT uses footprint anchors except custom R7,
whose F.Fab body center is used because its anchor is offset from the physical body.
Review JLC's component preview against reports/Assembly-top.svg, especially pin 1/polarity and
centering for U1/U2/U4/U5/U6/U7/U8/U9/U10, polarized C36, custom R7, and hybrid USB J4.
Generic correction rules do not prove a match to every individual JLC component model.

KNOWN AS-IS REVIEW ITEMS
This is an export of the committed design, not a claim of a clean DRC or an electrical safety certification.
Open connections: 0. Schematic/PCB parity issues: 0. Remaining findings are in reports/DRC.json.
Silkscreen is intentionally unfinished and is not assessed or a release blocker for this export.
Other findings include USB J4 single-spoke ground thermals and hole-to-copper
clearance (~0.1944 mm versus the project's 0.25 mm rule), R25 single-spoke thermal,
shunt R7 pad clearances versus the generic mains rule, intentional NT1 net overlap,
two unused reset test vias, and U6 library-footprint mismatch.
Confirm USB hole clearance with CAM before release; assembly artwork/placement preview still needs approval.
Enclosure, harness/crimp hardware, fuses fitted in the inlet, and mounting hardware are not automatically covered by this PCB BOM.

REFERENCES
https://jlcpcb.com/help/article/how-to-generate-gerber-and-drill-files-in-kicad-9
https://jlcpcb.com/help/article/how-to-generate-the-bom-and-centroid-file-from-kicad
https://github.com/bennymeg/Fabrication-Toolkit
'''
    (out/'Production_notes.txt').write_text(notes)
    manifest={'design_commit':revision,'date':date,'kicad':run('kicad-cli','version'),
              'inputs':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in INPUTS},
              'counts':{'smt':len(smt),'mixed':len(mixed),'manual_after_smt':len(manual),'physical':len(all_parts)},
              'drc':dict(counts),'files':{}}
    for p in sorted(out.rglob('*')):
        if p.is_file() and raw not in p.parents and gerbers not in p.parents:
            manifest['files'][str(p.relative_to(out))]=hashlib.sha256(p.read_bytes()).hexdigest()
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    bundle=out/f'SigCap-Production-{revision[:7]}.zip'
    with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out.rglob('*')):
            if p.is_file() and p!=bundle and raw not in p.parents and gerbers not in p.parents:
                z.write(p,str(p.relative_to(out)))
    for p in (fabzip,bundle):
        with zipfile.ZipFile(p) as z:assert z.testzip() is None
    for n,digest in manifest['inputs'].items():assert hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==digest
    print(json.dumps({'output':str(out),'commit':revision,'counts':manifest['counts'],'fabrication_zip':str(fabzip),'bundle':str(bundle)},indent=2))

if __name__=='__main__':main()
