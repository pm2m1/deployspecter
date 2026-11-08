"""Compare an image's /app/src/<pkg> tree with archives, file by file (sha256). Prints which archive it matches."""
import hashlib
import io
import json
import subprocess
import sys
import tarfile


def archive_files(path, pkg):
    out = {}
    with tarfile.open(path) as t:
        for m in t.getmembers():
            if m.isfile() and (m.name.startswith(pkg + "/") or f"/{pkg}/" in m.name):
                rel = m.name[m.name.index(pkg + "/"):]
                out[rel] = hashlib.sha256(t.extractfile(m).read()).hexdigest()
    return out

def image_files(image, pkg):
    cid = subprocess.run(["docker", "create", image], capture_output=True, text=True, check=True).stdout.strip()
    try:
        raw = subprocess.run(["docker", "cp", f"{cid}:/app/src/{pkg}", "-"], capture_output=True, check=True).stdout
    finally:
        subprocess.run(["docker", "rm", cid], capture_output=True)
    out = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as t:
        for m in t.getmembers():
            if m.isfile() and not m.name.endswith(".pyc") and "__pycache__" not in m.name:
                out[m.name] = hashlib.sha256(t.extractfile(m).read()).hexdigest()
    return out

pkg, image, *archives = sys.argv[1:]
img = image_files(image, pkg)
res = {"image": image, "files_in_image": len(img), "vs": {}}
for a in archives:
    arc = archive_files(a, pkg)
    common = set(arc) & set(img)
    diff = sorted(f for f in common if arc[f] != img[f])
    res["vs"][a.rsplit("/", 2)[-2] + "/" + a.rsplit("/", 1)[-1]] = {
        "archive_files": len(arc), "missing_in_image": len(set(arc) - set(img)),
        "extra_in_image": len(set(img) - set(arc)), "differing": len(diff), "differing_files": diff[:6]}
print(json.dumps(res))
