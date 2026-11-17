"""Build the installable zip: out/deck-mentor.zip (one top-level folder, as Decky expects)."""
import json
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "deck-mentor"
FILES = ["plugin.json", "package.json", "main.py", "LICENSE", "README.md", "dist/index.js"]


def main():
    version = json.load(open(os.path.join(ROOT, "package.json")))["version"]
    os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)
    target = os.path.join(ROOT, "out", f"{NAME}.zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in FILES:
            zf.write(os.path.join(ROOT, rel), f"{NAME}/{rel}")
        for base, dirs, files in os.walk(os.path.join(ROOT, "py_modules")):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(base, f)
                    zf.write(path, f"{NAME}/{os.path.relpath(path, ROOT)}")
        # Speech recogniser built in CI (Decky drops exec bits; voice.py restores them on a copy).
        whisper = os.path.join(ROOT, "bin", "whisper-cli")
        if os.path.isfile(whisper):
            zf.write(whisper, f"{NAME}/bin/whisper-cli")
        else:
            print("note: bin/whisper-cli not found, building without voice input")
    print(f"{target} (v{version}, {os.path.getsize(target) // 1024} KB)")


if __name__ == "__main__":
    main()
