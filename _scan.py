import pathlib
import re
import subprocess

files = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split()
pats = {
    "abs_path": re.compile(r"[A-Za-z]:\\"),
    "user": re.compile(r"Lenovo|chenyubj|@gmail|@qq\.com", re.I),
    "secret": re.compile(r"sk-[A-Za-z0-9]{16,}|SESSDATA\s*[=:]|Bearer\s+[A-Za-z0-9]{20,}"),
    "localhost": re.compile(r"127\.0\.0\.1:\d{4,5}"),
}
hits = 0
for f in files:
    p = pathlib.Path(f)
    try:
        t = p.read_text(encoding="utf-8")
    except Exception:
        continue
    lines = t.splitlines()
    for name, rx in pats.items():
        for m in rx.finditer(t):
            line = t[: m.start()].count("\n")
            hits += 1
            print(f"{name:10} {f}:{line + 1}  {lines[line][:110]}")
print("---", hits, "hits")
