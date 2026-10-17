"""Join sampled RAM/VRAM with server execution timestamps; no server mutation."""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import statistics


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory',type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    samples = read_lines(directory/'resources.ndjson')
    events = read_lines(directory/'server-events.ndjson')
    starts = [event for event in events if event['event']=='execution_start']
    results = []

    def summarize(rows):
        if not rows:
            return None
        gpu = [row['devices'][0] for row in rows if row.get('devices')]
        return dict(samples=len(rows),
          max_sampled_working_set=max(r['process']['working_set'] for r in rows),
          max_sampled_private=max(r['process']['private'] for r in rows),
          min_available_ram=min(r['system']['available_physical'] for r in rows),
          min_available_commit=min(r['system']['available_commit'] for r in rows),
          min_free_vram=min(g['vram_free'] for g in gpu) if gpu else None,
          max_sampled_torch_allocated=max(g['torch_vram_total']-g['torch_vram_free'] for g in gpu) if gpu else None,
          median_sampled_torch_allocated=statistics.median(g['torch_vram_total']-g['torch_vram_free'] for g in gpu) if gpu else None,
          median_free_vram=statistics.median(g['vram_free'] for g in gpu) if gpu else None)

    for index,start in enumerate(starts,1):
        prompt = start['prompt_id']
        terminal = next((event for event in events if event['prompt_id']==prompt
                         and event['event'] in ('execution_success','execution_error','execution_interrupted')),None)
        if not terminal:
            continue
        begin,end = timestamp(start['utc']),timestamp(terminal['utc'])
        next_start = timestamp(starts[index]['utc']) if index<len(starts) else end+30
        rows = [row for row in samples if begin<=timestamp(row['utc'])<=end]
        after = [row for row in samples if end<timestamp(row['utc'])<min(next_start,end+30)]
        nodes = [event for event in events if event['prompt_id']==prompt and event['event']=='executing' and event.get('node')]
        structure = next((event for event in nodes if event['node']=='3'),None)
        next_node = next((event for event in nodes if structure and timestamp(event['utc'])>timestamp(structure['utc'])),None)
        stage = [row for row in samples if structure and next_node
                 and timestamp(structure['utc'])<=timestamp(row['utc'])<timestamp(next_node['utc'])]
        results.append(dict(run=index,prompt_id=prompt,server_seconds=end-begin,
                            execution=summarize(rows),structure_sampler=summarize(stage),
                            idle_after=summarize(after)))
    report = dict(sample_interval_seconds=15,units='bytes',runs=results,
      limitation='Sampled peaks are lower bounds; two runs cannot establish an unbounded leak. Idle samples may reflect cached models/results.')
    (directory/'resource-summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    with (directory/'resources.csv').open('w',encoding='utf-8-sig',newline='') as output:
        fields=['utc','working_set','private','available_ram','available_commit','vram_free','torch_reserved','torch_allocated','cpu_percent_system']
        writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader()
        for row in samples:
            gpu=row.get('devices',[{}])[0]
            writer.writerow(dict(utc=row['utc'],working_set=row['process']['working_set'],private=row['process']['private'],
              available_ram=row['system']['available_physical'],available_commit=row['system']['available_commit'],
              vram_free=gpu.get('vram_free'),torch_reserved=gpu.get('torch_vram_total'),
              torch_allocated=gpu.get('torch_vram_total',0)-gpu.get('torch_vram_free',0),
              cpu_percent_system=row.get('cpu_percent_system')))
    print(json.dumps(report))


if __name__ == '__main__':
    main()
