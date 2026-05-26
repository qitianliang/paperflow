"""Check translation progress."""
import os, json
from paperflow.config import get_config

config = get_config()
topic = config.project.topic_slug

# Check queue
qf = os.path.join(config.translation.paths.staging_dir, topic, 'queue.json')
if os.path.exists(qf):
    with open(qf) as f:
        q = json.load(f)
    print(f'Queue items: {len(q)}')
    for item in q:
        zk = item.get('zotero_key', '?')
        st = item.get('status', '?')
        mono = bool(item.get('mono_pdf', ''))
        dual = bool(item.get('dual_pdf', ''))
        err = item.get('error', '')[:50]
        print(f'  {zk}: status={st}, mono={mono}, dual={dual}, err={err}')
else:
    print('Queue file not found')

# Check translations
out_dir = os.path.join(config.translation.paths.output_dir, topic)
if os.path.exists(out_dir):
    for root, dirs, files in os.walk(out_dir):
        for f in files:
            if f.endswith('.pdf'):
                size_kb = os.path.getsize(os.path.join(root, f)) // 1024
                print(f'Output: {os.path.join(root, f)} ({size_kb}KB)')
else:
    print(f'Output dir {out_dir} does not exist')
