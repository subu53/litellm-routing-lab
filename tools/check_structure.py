"""Structural checks for repo files that no offline tool can validate.

Not a substitute for `terraform validate` (which needs the real binary) or a
real YAML parser. It catches the errors typical of hand-assembled config:
unbalanced braces, unterminated strings, dangling references, bad indentation.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
failures = []
checks = 0


def fail(message):
    failures.append(message)


def check(label, condition, detail=""):
    global checks
    checks += 1
    if not condition:
        fail(f"{label}{': ' + detail if detail else ''}")


# ---------------------------------------------------------------- HCL (main.tf)
def strip_hcl(text):
    """Remove comments and string literals so brace counting is reliable."""
    text = re.sub(r'#[^\n]*', '', text)
    text = re.sub(r'"[^"\n]*"', '""', text)
    return text


def check_hcl(path):
    text = path.read_text(encoding="utf-8")
    clean = strip_hcl(text)

    # Balanced delimiters.
    for open_ch, close_ch, name in (("{", "}", "braces"), ("[", "]", "brackets"), ("(", ")", "parens")):
        check(f"{path.name} balanced {name}",
              clean.count(open_ch) == clean.count(close_ch),
              f"{clean.count(open_ch)} '{open_ch}' vs {clean.count(close_ch)} '{close_ch}'")

    # No unterminated string literals on any line.
    for i, line in enumerate(text.splitlines(), 1):
        if line.count('"') % 2 != 0:
            fail(f"{path.name}:{i} odd number of double quotes (unterminated string?)")

    # Every block header ends with '{'.
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.split("#")[0].strip()
        if re.match(r'^(resource|data|variable|output|provider|terraform|locals)\b', stripped):
            check(f"{path.name}:{i} block header opens a brace",
                  stripped.endswith("{"), f"got: {stripped!r}")

    # Declared resource addresses, reported for the project-wide reference check.
    declared = set(re.findall(r'^(?:resource|data)\s+"([^"]+)"\s+"([^"]+)"', text, re.M))

    print(f"  {path.name}: {len(text.splitlines())} lines, {len(declared)} blocks")
    return declared


def check_references(declared, hcl_files):
    """Validate resource references project-wide: outputs.tf may reference
    resources declared in main.tf, so per-file checking produces false positives."""
    for path in hcl_files:
        clean = strip_hcl(path.read_text(encoding="utf-8"))
        for ref_type, ref_name in sorted(set(re.findall(
                r'\b((?:google|random)_[a-z0-9_]+)\.([a-z0-9_]+)', clean))):
            if ref_type.endswith("_") and len(ref_name) < 2:
                continue
            check(f"{path.name} reference {ref_type}.{ref_name} is declared",
                  (ref_type, ref_name) in declared,
                  "no matching resource or data block in any .tf file")


# ---------------------------------------------------------------- YAML (config.yaml)
def check_yaml(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    stack = []  # (indent, is_list)
    keys_seen = []
    for i, raw in enumerate(lines, 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" in raw:
            fail(f"{path.name}:{i} contains a tab (YAML requires spaces)")
        indent = len(raw) - len(raw.lstrip())
        check(f"{path.name}:{i} indentation is a multiple of 2", indent % 2 == 0,
              f"indent={indent}")
        body = raw.strip()
        is_list = body.startswith("- ")
        # A mapping entry must have a colon outside of quotes.
        content = body[2:] if is_list else body
        if not is_list:
            check(f"{path.name}:{i} is a mapping entry with a colon",
                  ":" in content, f"got: {content!r}")
            key = content.split(":", 1)[0].strip()
            keys_seen.append((indent, key))
            check(f"{path.name}:{i} key is non-empty", bool(key), f"got: {content!r}")
        while stack and indent <= stack[-1]:
            stack.pop()
        if content.rstrip().endswith(":"):
            stack.append(indent)

    # Top-level structure must be exactly model_list + general_settings.
    top = [k for indent, k in keys_seen if indent == 0]
    check(f"{path.name} top-level keys", top == ["model_list", "general_settings"],
          f"got {top}")

    text = path.read_text(encoding="utf-8")
    # Both configs share the router contract; they differ in backend and classifier.
    for required in ("model_name: jev-router", "return_raw_model_name: true",
                     "master_key:", "complexity_router_default_model:"):
        check(f"{path.name} contains {required!r}", required in text)

    # Exactly one classifier type, and it must be a recognized one.
    classifiers = re.findall(r'classifier_type:\s*(\S+)', text)
    check(f"{path.name} declares exactly one classifier_type",
          len(classifiers) == 1, f"got {classifiers}")
    if classifiers:
        check(f"{path.name} classifier_type is valid",
              classifiers[0] in {"jev", "heuristic", "llm", "custom"},
              f"got {classifiers[0]}")
        # A jev classifier needs its config block and the key the container injects.
        if classifiers[0] == "jev":
            for required in ("jev_classifier_config:", "classifier_type: jev"):
                check(f"{path.name} jev config contains {required!r}", required in text)
        else:
            check(f"{path.name} non-jev config has no jev block",
                  "jev_classifier_config:" not in text,
                  "stale jev settings would be ignored but mislead readers")
            check(f"{path.name} non-jev config needs no database_url",
                  "database_url:" not in text,
                  "no Cloud SQL in the free path")

    # Every tier must point at a declared alias. The negative lookbehind stops
    # `return_raw_model_name: true` from being read as an alias declaration.
    aliases = set(re.findall(r'(?<![A-Za-z0-9_])model_name:\s*([A-Za-z0-9_-]+)', text))
    tiers = dict(re.findall(r'^\s{10}(SIMPLE|MEDIUM|COMPLEX|REASONING):\s*(\S+)$',
                            text, re.M))
    check(f"{path.name} has all four tiers", len(tiers) == 4, f"got {sorted(tiers)}")
    for tier, target in tiers.items():
        check(f"{path.name} tier {tier} -> {target} is a declared alias",
              target in aliases, f"declared aliases: {sorted(aliases)}")

    default = re.search(r'complexity_router_default_model:\s*(\S+)', text)
    if default:
        check(f"{path.name} default model {default.group(1)} is a declared alias",
              default.group(1) in aliases)

    print(f"  {path.name}: {len(lines)} lines, aliases={sorted(aliases)}, tiers={tiers}")


# ---------------------------------------------------------------- Dockerfile / tf files
def check_dockerfile(path):
    text = path.read_text(encoding="utf-8")
    check("Dockerfile has FROM", text.lstrip().upper().startswith("FROM"))
    check("Dockerfile has a CMD", "\nCMD " in text)
    check("Dockerfile copies config.yaml", "config.yaml" in text)
    check("Dockerfile ports 8080", "8080" in text)
    if not text.endswith("\n"):
        fail("Dockerfile does not end with a newline")
    print(f"  {path.name}: {len(text.splitlines())} lines")


def check_tfvars_example(path):
    text = path.read_text(encoding="utf-8")
    check("terraform.tfvars.example sets project_id", text.startswith("project_id ="))
    print(f"  {path.name}: {text.strip()!r}")


def check_python(path):
    import ast
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        fail(f"{path.name} syntax error: {error}")
        return
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for required in ("run_case", "main"):
        check(f"{path.name} defines {required}()", required in funcs)
    consts = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name)}
    for required in ("CASES", "MODELS"):
        check(f"{path.name} defines {required}", required in consts)
    check(f"{path.name} has __main__ guard", 'if __name__ == "__main__":' in source)
    check(f"{path.name} writes CSVs", "benchmark-{mode}.csv" in source)
    check(f"{path.name} reads classifier cost header",
          "x-litellm-classifier-cost" in source)
    check(f"{path.name} sets 120s timeout", "timeout=120" in source)
    print(f"  {path.name}: {len(source.splitlines())} lines, functions={sorted(funcs)}")


print("Structural checks")
print("=================")
hcl_files, declared = [], set()
for name in ("main.tf", "outputs.tf", "variables.tf", "versions.tf"):
    path = ROOT / name
    if not path.exists():
        fail(f"{name} is missing")
        continue
    hcl_files.append(path)
    declared |= check_hcl(path)
check_references(declared, hcl_files)
for config_name in ("config.yaml", "config.free.yaml"):
    config_path = ROOT / config_name
    if config_path.exists():
        check_yaml(config_path)
    else:
        fail(f"{config_name} is missing")
check_dockerfile(ROOT / "Dockerfile")
check_tfvars_example(ROOT / "terraform.tfvars.example")
check_python(ROOT / "benchmark.py")

print()
if failures:
    print(f"FAILED {len(failures)} of {checks} checks:")
    for item in failures:
        print("  -", item)
    sys.exit(1)
print(f"PASSED all {checks} structural checks")
print("NOTE: this is not terraform validate. Schema/provider errors are still possible.")
