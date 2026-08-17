import hashlib
import os
import pathlib
import shutil
import tarfile
import urllib.request

version = os.environ["NODE_VERSION"]
base_url = os.environ["NODE_DOWNLOAD_BASE"].rstrip("/")
filename = os.environ["NODE_FILENAME"]
release_url = f"{base_url}/v{version}"
archive_path = pathlib.Path("/tmp") / filename

with (
    urllib.request.urlopen(f"{release_url}/{filename}", timeout=60) as response,
    archive_path.open("wb") as archive_file,
):
    shutil.copyfileobj(response, archive_file)

checksums = (
    urllib.request.urlopen(
        f"{release_url}/SHASUMS256.txt",
        timeout=60,
    )
    .read()
    .decode()
)
expected = next(
    line.split()[0] for line in checksums.splitlines() if line.endswith(f"  {filename}")
)
actual = hashlib.sha256(archive_path.read_bytes()).hexdigest()
if actual != expected:
    raise RuntimeError(f"Node.js checksum mismatch: {actual} != {expected}")

with tarfile.open(archive_path, "r:xz") as archive:
    archive.extractall("/tmp/node", filter="data")
source_dir = pathlib.Path("/tmp/node") / filename.removesuffix(".tar.xz")
shutil.copytree(source_dir, "/usr/local", dirs_exist_ok=True, symlinks=True)

for executable in ("node", "npm", "npx"):
    executable_path = pathlib.Path("/usr/local/bin") / executable
    if not executable_path.exists():
        raise RuntimeError(f"Node.js executable is missing: {executable_path}")

archive_path.unlink()
shutil.rmtree("/tmp/node")
