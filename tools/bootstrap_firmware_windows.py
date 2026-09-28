"""Download verified portable compilers + pinned Pico SDK inside .tools only."""
from __future__ import annotations

import hashlib
import subprocess
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = [
    ("llvm-mingw.zip", "https://github.com/mstorsjo/llvm-mingw/releases/download/20241217/llvm-mingw-20241217-ucrt-x86_64.zip",
     "f4f3ad8616c4183ce7b0d72df634400945b41ea9816145fc2430df6003455db7"),
    ("arm-gnu-toolchain.zip", "https://developer.arm.com/-/media/Files/downloads/gnu/14.2.rel1/binrel/arm-gnu-toolchain-14.2.rel1-mingw-w64-i686-arm-none-eabi.zip",
     "6facb152ce431ba9a4517e939ea46f057380f8f1e56b62e8712b3f3b87d994e1"),
]


def main():
    destination = ROOT / ".tools"
    destination.mkdir(exist_ok=True)
    for name, url, sha in DOWNLOADS:
        archive = destination / name
        if not archive.exists():
            print(f"Downloading {name}")
            urllib.request.urlretrieve(url, archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != sha:
            raise SystemExit(f"Checksum mismatch: {archive}")
        with zipfile.ZipFile(archive) as zip_file:
            for entry in zip_file.infolist():
                if not (destination / entry.filename).resolve().is_relative_to(destination.resolve()):
                    raise SystemExit("Archive path escapes .tools")
            zip_file.extractall(destination)
    sdk = destination / "pico-sdk"
    if not sdk.exists():
        subprocess.run(["git", "clone", "--depth", "1", "--branch", "2.2.0",
                        "https://github.com/raspberrypi/pico-sdk.git", str(sdk)], check=True)
    head = subprocess.check_output(["git", "-C", str(sdk), "rev-parse", "HEAD"], text=True).strip()
    if head != "a1438dff1d38bd9c65dbd693f0e5db4b9ae91779":
        raise SystemExit("Existing Pico SDK differs from pinned revision; preserve and inspect it")
    subprocess.run(["git", "-C", str(sdk), "submodule", "update", "--init", "--depth", "1", "lib/tinyusb"], check=True)
    print("Portable tools ready; host installation unchanged.")


if __name__ == "__main__":
    main()
