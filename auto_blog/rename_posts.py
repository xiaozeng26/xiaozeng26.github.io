"""将现有文章的目录名从 category-hash 格式改为中文标题"""
import re, json, os, shutil

REPO = r"D:\xiaozeng26.github.io"
BASE = os.path.join(REPO, "2026", "07", "27")

def make_slug(title):
    """与 generate_post.py 的 generate_slug 逻辑一致"""
    slug = title
    for ch in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
        slug = slug.replace(ch, '-')
    return slug.strip(' .')

# 1. 读取每个目录的文章标题
rename_map = {}  # old_dir -> (title, new_dir)
for d in os.listdir(BASE):
    idx_path = os.path.join(BASE, d, "index.html")
    if not os.path.isfile(idx_path):
        continue
    with open(idx_path, "r", encoding="utf-8") as f:
        content = f.read()
    m = re.search(r'<title>([^<]*) \|', content)
    if m:
        title = m.group(1)
        new_dir = make_slug(title)
        if d != new_dir:
            rename_map[d] = (title, new_dir)
            print(f"[FOUND] {d}  ->  {new_dir}")
        else:
            print(f"[KEEP]  {d} (already correct)")

if not rename_map:
    print("Nothing to rename!")
    exit()

# 2. 重命名目录
for old_dir, (title, new_dir) in rename_map.items():
    old_path = os.path.join(BASE, old_dir)
    new_path = os.path.join(BASE, new_dir)
    if os.path.exists(new_path):
        shutil.rmtree(new_path)
    os.rename(old_path, new_path)
    print(f"[RENAME] {old_dir} -> {new_dir}")

# 3. 更新 index.html
idx_path = os.path.join(REPO, "index.html")
with open(idx_path, "r", encoding="utf-8") as f:
    idx_content = f.read()

for old_dir, (title, new_dir) in rename_map.items():
    old_url = f"/2026/07/27/{old_dir}/"
    new_url = f"/2026/07/27/{new_dir}/"
    idx_content = idx_content.replace(old_url, new_url)
    print(f"[INDEX] {old_url} -> {new_url}")

with open(idx_path, "w", encoding="utf-8") as f:
    f.write(idx_content)

# 4. 更新 history.json
hist_path = os.path.join(REPO, "auto_blog", "history.json")
with open(hist_path, "r", encoding="utf-8") as f:
    hist = json.load(f)

for post in hist.get("generated", []):
    url = post.get("url", "")
    for old_dir, (title, new_dir) in rename_map.items():
        if old_dir in url:
            post["url"] = url.replace(old_dir, new_dir)
            print(f"[HIST] {url} -> {post['url']}")

with open(hist_path, "w", encoding="utf-8") as f:
    json.dump(hist, f, ensure_ascii=False, indent=2)

# 5. 验证
print("\nFinal directories:")
for d in sorted(os.listdir(BASE)):
    print(f"  {d}")
print("\nDone!")
