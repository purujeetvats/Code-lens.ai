// Copy the Python backend into the extension so the packaged .vsix is
// self-contained. Skips caches, virtualenvs, and any indexed workspace data.
const fs = require("fs");
const path = require("path");

const SKIP = new Set(["__pycache__", ".venv", "venv", ".codelens", ".pytest_cache"]);
const src = path.join(__dirname, "..", "..", "backend");
const dest = path.join(__dirname, "..", "backend");

function copyDir(from, to) {
  fs.mkdirSync(to, { recursive: true });
  for (const entry of fs.readdirSync(from, { withFileTypes: true })) {
    if (SKIP.has(entry.name) || entry.name.endsWith(".pyc")) continue;
    const a = path.join(from, entry.name);
    const b = path.join(to, entry.name);
    entry.isDirectory() ? copyDir(a, b) : fs.copyFileSync(a, b);
  }
}

if (!fs.existsSync(path.join(src, "app", "main.py"))) {
  console.error(`Backend not found at ${src}`);
  process.exit(1);
}
fs.rmSync(dest, { recursive: true, force: true });
copyDir(src, dest);
console.log(`Bundled backend into ${dest}`);
