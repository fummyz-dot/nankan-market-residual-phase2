from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit/successor_v1/job004r1"
AUTH = ROOT / "data/manifests/successor_v1/JOB004_RUNTIME_PROVISIONING_FREEZE_V1.json"
PYTHON = ROOT / ".venv-p2-model/bin/python"
CONSTRAINTS = OUT / "runtime_constraints_v1.txt"
WHEELHOUSE = OUT / "wheelhouse"
FREEZE = ROOT / "data/manifests/successor_v1/RUNTIME_FREEZE_V1.json"
PINS = {"numpy": "2.5.2", "scipy": "1.18.0", "pandas": "3.0.5", "catboost": "1.2.10"}
THREAD_ENV = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(args: list[str], *, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, env=env)


def probe() -> dict:
    code = """import importlib,importlib.metadata,json,platform,sys
o={'executable':sys.executable,'python_version':platform.python_version(),'python_implementation':platform.python_implementation(),'sys_prefix':sys.prefix,'platform':platform.platform(),'machine':platform.machine(),'os':platform.system(),'libc':platform.libc_ver(),'packages':{}}
for name in ('numpy','scipy','pandas','catboost'):
 try:
  module=importlib.import_module(name); dist=importlib.metadata.distribution(name)
  o['packages'][name]={'installed':True,'version':getattr(module,'__version__',None),'module_path':getattr(module,'__file__',None),'distribution_path':str(dist.locate_file('')),'metadata_version':dist.version}
 except Exception as exc:o['packages'][name]={'installed':False,'error':repr(exc)}
print(json.dumps(o))"""
    return json.loads(command([str(PYTHON), "-c", code]))


def relevant_environment() -> dict[str, str | None]:
    names = ["OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "BLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"]
    return {name: os.environ.get(name) for name in names}


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def expected_existing(probed: dict) -> None:
    if not PYTHON.is_file():
        raise RuntimeError(f"preferred interpreter missing: {PYTHON}")
    if probed["python_version"] != "3.12.3":
        raise RuntimeError(f"Python version mismatch: {probed['python_version']}")
    for name in ("numpy", "scipy"):
        actual = probed["packages"][name].get("version")
        if actual != PINS[name]:
            raise RuntimeError(f"{name} preinstall version mismatch: {actual}")


def prepare() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    authority = json.loads(AUTH.read_text())
    if authority["constraints"] != [f"{name}=={version}" for name, version in PINS.items()]:
        raise RuntimeError("authority constraints do not exactly match frozen pins")
    before = probe()
    expected_existing(before)
    before["preferred_interpreter"] = str(PYTHON)
    before["authority_sha256"] = sha256(AUTH)
    before["pip_version"] = command([str(PYTHON), "-m", "pip", "--version"]).strip()
    before["pip_freeze"] = command([str(PYTHON), "-m", "pip", "freeze"]).splitlines()
    before["blas_openmp_environment"] = relevant_environment()
    (OUT / "preinstall_runtime.json").write_text(json.dumps(before, indent=2) + "\n")
    CONSTRAINTS.write_text("numpy==2.5.2\nscipy==1.18.0\npandas==3.0.5\ncatboost==1.2.10\n")


def inspect_resolver() -> None:
    report = json.loads((OUT / "pip_resolver_report.json").read_text())
    installs = report.get("install", [])
    seen: dict[str, str] = {}
    source_builds: list[str] = []
    for item in installs:
        meta = item.get("metadata", {})
        name = meta.get("name", "").lower()
        if name:
            seen[name] = meta.get("version", "")
        url = item.get("download_info", {}).get("url", "")
        if url and not url.endswith(".whl"):
            source_builds.append(url)
    for name, version in PINS.items():
        if name in seen and seen[name] != version:
            raise RuntimeError(f"resolver version conflict for {name}: {seen[name]}")
    if source_builds:
        raise RuntimeError(f"resolver proposes source build(s): {source_builds}")


def wheel_inventory() -> list[dict]:
    wheels = sorted(WHEELHOUSE.glob("*.whl"))
    if not wheels:
        raise RuntimeError("wheelhouse is empty")
    rows = [{"filename": p.name, "size_bytes": p.stat().st_size, "sha256": sha256(p)} for p in wheels]
    write_csv(OUT / "wheel_inventory.csv", ["filename", "size_bytes", "sha256"], rows)
    return rows


def postinstall() -> None:
    inspect_resolver()
    wheels = wheel_inventory()
    after = probe()
    expected_existing(after)
    for name, expected in PINS.items():
        actual = after["packages"][name].get("version")
        if actual != expected:
            raise RuntimeError(f"postinstall {name} mismatch: {actual}")
    pip_check = command([str(PYTHON), "-m", "pip", "check"]).strip()
    if "No broken requirements found." not in pip_check:
        raise RuntimeError(f"pip check failed: {pip_check}")
    (OUT / "runtime_postinstall.txt").write_text("\n".join([command([str(PYTHON), "-m", "pip", "--version"]).strip(), pip_check] + [f"{name}=={after['packages'][name]['version']}\nmodule={after['packages'][name]['module_path']}\ndistribution={after['packages'][name]['distribution_path']}" for name in PINS]) + "\n")
    env = os.environ.copy()
    env.update(THREAD_ENV)
    smoke = json.loads(command([str(PYTHON), str(ROOT / "src/audit/p2s_job004r1_runtime_smoke.py")], env=env))
    (OUT / "import_smoke_test.json").write_text(json.dumps(smoke["imports"], indent=2) + "\n")
    (OUT / "catboost_determinism_smoke.json").write_text(json.dumps(smoke["catboost"], indent=2) + "\n")
    (OUT / "scipy_optimizer_smoke.json").write_text(json.dumps(smoke["scipy"], indent=2) + "\n")
    if not (smoke["imports"]["pass"] and smoke["catboost"]["pass"] and smoke["scipy"]["pass"]):
        raise RuntimeError("one or more runtime smoke tests failed")
    final_freeze = {
        "runtime_id": "RUNTIME_FREEZE_V1",
        "authority_sha256": sha256(AUTH),
        "interpreter_absolute_path": str(PYTHON),
        "resolved_python_executable": after["executable"],
        "python_version": after["python_version"],
        "pip_version": command([str(PYTHON), "-m", "pip", "--version"]).strip(),
        "platform": {key: after[key] for key in ("platform", "machine", "os", "libc", "sys_prefix")},
        "packages": after["packages"],
        "resolver_report_sha256": sha256(OUT / "pip_resolver_report.json"),
        "constraints_sha256": sha256(CONSTRAINTS),
        "wheel_inventory": wheels,
        "final_pip_freeze": command([str(PYTHON), "-m", "pip", "freeze"]).splitlines(),
        "thread_environment_contract": THREAD_ENV,
        "smoke_tests": smoke,
        "project_data_used": False,
        "project_model_fit_performed": False,
    }
    FREEZE.write_text(json.dumps(final_freeze, indent=2) + "\n")
    companion = {"runtime_freeze_path": str(FREEZE), "runtime_freeze_sha256": sha256(FREEZE)}
    (OUT / "runtime_freeze_sha256.json").write_text(json.dumps(companion, indent=2) + "\n")
    issues = [{"severity": "WARNING", "issue": "PIP_CACHE_DISABLED_PERMISSION", "evidence": "pip emitted a cache-directory permission warning; resolver, local-only install, and pip check completed."}]
    write_csv(OUT / "issues.csv", ["severity", "issue", "evidence"], issues)
    manifest = {"job_id": "P2S_JOB_004R1_RUNTIME_PROVISIONING", "status": "JOB004R1_PASS_WITH_WARNINGS", "authority_sha256": sha256(AUTH), "constraints_sha256": sha256(CONSTRAINTS), "runtime_freeze_sha256": sha256(FREEZE), "provisioning_script_sha256": sha256(Path(__file__)), "smoke_script_sha256": sha256(ROOT / "src/audit/p2s_job004r1_runtime_smoke.py"), "python_executable": str(PYTHON), "python_version": after["python_version"], "package_versions": {name: after["packages"][name]["version"] for name in PINS}, "network_accessed": True, "network_scope": ["pypi.org", "files.pythonhosted.org"], "project_data_network_accessed": False, "install_performed": True, "project_model_fit_performed": False, "smoke_synthetic_only": True, "commands": ["pip install --dry-run --report ... --only-binary=:all:", "pip download --only-binary=:all: ...", "pip install --no-index --find-links ...", "pip check", "synthetic CatBoost and SciPy smoke tests"]}
    (OUT / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (OUT / "JOB004R1_FINAL_REPORT.md").write_text("# Job004R1 Runtime Provisioning\n\n`JOB004R1_PASS_WITH_WARNINGS`\n\nFrozen versions: Python 3.12.3, NumPy 2.5.2, SciPy 1.18.0, pandas 3.0.5, and CatBoost 1.2.10. The runtime was provisioned from hashed binary wheels, `pip check` passed, and local synthetic smoke tests passed. No project data or Job004 model fitting was used.\n\nWarning: pip emitted a non-fatal cache-directory permission warning.\n")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"prepare", "inspect", "postinstall"}:
        raise SystemExit("usage: p2s_job004r1_runtime_provisioning.py {prepare|inspect|postinstall}")
    {"prepare": prepare, "inspect": inspect_resolver, "postinstall": postinstall}[sys.argv[1]]()
