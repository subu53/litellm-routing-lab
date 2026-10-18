"""Negative tests: prove tools/check_structure.py actually catches faults.

A checker that only ever reports PASS is worthless. This copies the project into a
scratch directory, injects one known-bad change at a time, and asserts the checker
fails with the expected message. Run: python tools/test_checker.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "tools" / "check_structure.py"
FILES = ["main.tf", "outputs.tf", "variables.tf", "versions.tf",
         "config.yaml", "config.free.yaml", "Dockerfile",
         "terraform.tfvars.example", "benchmark.py"]

results = []


def run_checker(workdir):
    proc = subprocess.run([sys.executable, str(workdir / "tools" / "check_structure.py")],
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def sandbox():
    scratch = ROOT / ".tmp-verify"
    shutil.rmtree(scratch, ignore_errors=True)
    (scratch / "tools").mkdir(parents=True)
    shutil.copy2(CHECKER, scratch / "tools" / "check_structure.py")
    for name in FILES:
        shutil.copy2(ROOT / name, scratch / name)
    return scratch


def case(name, filename, mutate, expect):
    scratch = sandbox()
    try:
        path = scratch / filename
        path.write_text(mutate(path.read_text(encoding="utf-8")),
                        encoding="utf-8", newline="")
        code, output = run_checker(scratch)
        ok = code != 0 and expect in output
        results.append((ok, name, code, expect, output))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def control():
    scratch = sandbox()
    try:
        code, output = run_checker(scratch)
        ok = code == 0 and "PASSED" in output
        results.append((ok, "CONTROL: unmodified project passes", code, "PASSED", output))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


control()
case("unclosed brace", "main.tf",
     lambda t: t + '\nresource "google_x" "y" {\n', "balanced braces")
case("unbalanced bracket", "main.tf",
     lambda t: t.replace('    "sqladmin.googleapis.com",\n  ])',
                         '    "sqladmin.googleapis.com",\n  )', 1),
     "balanced brackets")
case("underterminated string", "main.tf",
     lambda t: t.replace('"litellm-gateway"', '"litellm-gateway', 1),
     "unterminated string")
case("tab in YAML", "config.yaml",
     lambda t: t.replace("        tiers:", "\ttiers:"), "contains a tab")
case("tier points at missing alias", "config.yaml",
     lambda t: t.replace("COMPLEX: gemini-flash", "COMPLEX: gemini-typo"), "is a declared alias")
case("ghost resource reference", "main.tf",
     lambda t: t.replace("google_sql_database_instance.postgres.name",
                         "google_sql_database_instance.ghost.name", 1),
     "no matching resource")
case("dropped router setting", "config.yaml",
     lambda t: t.replace("return_raw_model_name: true", "return_raw_model_name: false"),
     "return_raw_model_name")
case("missing tier", "config.yaml",
     lambda t: t.replace("REASONING: gemini-flash", "CHEAP: gemini-flash"), "all four tiers")
case("missing CMD in Dockerfile", "Dockerfile",
     lambda t: "\n".join(l for l in t.splitlines() if not l.startswith("CMD")), "has a CMD")
case("broken python syntax", "benchmark.py",
     lambda t: t.replace("def main():", "def main(:"), "syntax error")
case("free config keeps a jev block", "config.free.yaml",
     lambda t: t.replace("classifier_type: heuristic",
                         "classifier_type: heuristic\n        jev_classifier_config:\n          model: jev-latest"),
     "no jev block")
case("free config needs no database_url", "config.free.yaml",
     lambda t: t.replace("master_key:", "database_url: os.environ/DATABASE_URL\n  master_key:"),
     "no database_url")
case("invalid classifier type", "config.free.yaml",
     lambda t: t.replace("classifier_type: heuristic", "classifier_type: magic"),
     "classifier_type is valid")
case("two classifiers declared", "config.free.yaml",
     lambda t: t.replace("classifier_fallback:", "classifier_type: jev\n        classifier_fallback:"),
     "exactly one classifier_type")

failed = [r for r in results if not r[0]]
for ok, name, code, expect, output in results:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"      exit={code} expected-to-find={expect!r}")
        print("      output:", output.strip().replace("\n", "\n      "))
print()
if failed:
    print(f"{len(failed)}/{len(results)} negative tests failed")
    sys.exit(1)
print(f"All {len(results)} negative tests passed - the checker detects real faults.")
