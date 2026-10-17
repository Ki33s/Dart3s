"""CPU-only measurements and matched views for the isolated seed experiment."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'scripts'))
from analyze_mesh_integrity import inspect
from render_mesh_stage_full import render_full
from PIL import Image, ImageDraw, ImageFont

DIRECTORY = ROOT/'logs/seed-remesh-comparison-20261016'
rows = []
report = {}
font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 18)
small = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 14)
labels = {'raw':'Исходная сетка', 'remesh':'После Remesh 256',
          'decimate':'После упрощения', 'final':'С текстурой'}
for seed in (42, 1024):
    path = DIRECTORY/f'seed-{seed}.json'
    if not path.exists():
        continue
    run = json.loads(path.read_text(encoding='utf-8'))
    if run['status']!='completed':
        continue
    items = {}
    row = Image.new('RGB', (4*340, 410), '#eef0f3')
    draw = ImageDraw.Draw(row)
    draw.text((12, 8), f'Seed формы и текстуры: {seed}', fill='#15202d', font=font)
    for column, (stage, source) in enumerate(run['stages'].items()):
        png = DIRECTORY/f'seed-{seed}-{stage}.png'
        rendered = render_full(Path(source), png, size=256)
        measured = inspect(Path(source))
        items[stage] = {'preview':rendered, 'mesh':measured}
        preview = Image.open(png).convert('RGB')
        preview.thumbnail((326, 305), Image.Resampling.LANCZOS)
        row.paste(preview, (column*340+(340-preview.width)//2, 55+(305-preview.height)//2))
        counts = [p['faces'] for p in measured['primitives']]
        boundaries = sum(p['boundary_edges'] for p in measured['primitives'])
        draw.text((column*340+10, 35), labels[stage], fill='#15202d', font=font)
        draw.text((column*340+10, 365), f'{sum(counts):,} треуг. · края: {boundaries:,}', fill='#15202d', font=small)
        draw.text((column*340+10, 385), 'Исходные треугольники; без proxy-сетки', fill='#555555', font=small)
        print(json.dumps({'seed':seed,'stage':stage,'triangles':sum(counts),'boundary_edges':boundaries,
                          'preview':str(png)},ensure_ascii=True),flush=True)
    timings = [json.loads(l) for l in (DIRECTORY/f'seed-{seed}.timings.jsonl').read_text().splitlines() if l]
    durations = []
    starts = [e for e in timings if e['event']=='executing' and e.get('node') is not None]
    terminal = next(e for e in reversed(timings) if e['event']=='execution_success')
    for index, event in enumerate(starts):
        end = starts[index+1]['monotonic'] if index+1<len(starts) else terminal['monotonic']
        durations.append({'node':event['node'], 'seconds':round(end-event['monotonic'],3)})
    report[str(seed)] = {'stages':items, 'node_seconds':durations,
                         'server_seconds':round(terminal['monotonic']-next(e['monotonic'] for e in timings if e['event']=='execution_start'),3),
                         'wall_seconds':run['wall_seconds'], 'prompt_id':run['prompt_id']}
    rows.append(row)
if rows:
    sheet = Image.new('RGB', (1360, len(rows)*410), '#eef0f3')
    for index, row in enumerate(rows):
        sheet.paste(row, (0,index*410))
    sheet.save(DIRECTORY/'comparison.png')
(DIRECTORY/'analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
