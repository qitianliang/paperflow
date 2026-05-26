"""Check deep read and translation progress."""
from paperflow.cache import cache
from paperflow.config import get_config
import os, json

config = get_config()
topic = config.project.topic_slug

print("=== Deep Read Status ===")
for key in os.listdir(cache.base_dir) if os.path.isdir(cache.base_dir) else []:
    dr = cache.load_json(key, "deep_read.json")
    if dr:
        cq = dr.get("core_question", "?")
        print(f"{key}: deep_read DONE, core_question={cq[:80]}")
    else:
        print(f"{key}: no deep_read yet")

print("\n=== Translation Status ===")
qf = os.path.join(config.translation.paths.staging_dir, topic, "queue.json")
if os.path.exists(qf):
    with open(qf) as f:
        q = json.load(f)
    for item in q:
        zk = item.get("zotero_key", "?")
        st = item.get("status", "?")
        mono = bool(item.get("mono_pdf", ""))
        dual = bool(item.get("dual_pdf", ""))
        print(f"{zk}: status={st}, mono={mono}, dual={dual}")

print("\n=== Translated PDFs ===")
out = os.path.join(config.translation.paths.output_dir, topic)
if os.path.exists(out):
    for root, dirs, files in os.walk(out):
        for f in files:
            if f.endswith(".pdf"):
                sz = os.path.getsize(os.path.join(root, f)) // 1024
                print(f"  {os.path.join(root, f)} ({sz}KB)")
else:
    print("No translated outputs yet")
