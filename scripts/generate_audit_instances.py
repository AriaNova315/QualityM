from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain.audit_expansion import expand_audit_instances
from app.importers.project_workbook import parse_project_workbook


DEFAULT_SOURCE = ROOT / "项目相关信息表.xlsx"
DEFAULT_JSON = ROOT / "tests" / "fixtures" / "audit-instances.golden.json"


def build_audit_instance_payload(source: Path) -> dict:
    project_payload = parse_project_workbook(source)
    expansion = expand_audit_instances(project_payload)
    return {
        "project": project_payload["project"],
        "summary": {
            "instance_count": len(expansion.instances),
            "product_count": sum(
                instance["audit_kind"] == "product" for instance in expansion.instances
            ),
            "process_count": sum(
                instance["audit_kind"] == "process" for instance in expansion.instances
            ),
            "company_count": sum(
                instance["report_scope"] == "company" for instance in expansion.instances
            ),
        },
        "diagnostics": [diagnostic.to_dict() for diagnostic in expansion.diagnostics],
        "instances": expansion.instances,
    }


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate QualityM deterministic audit-instance golden artifact"
    )
    parser.add_argument("source", nargs="?", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args()

    payload = build_audit_instance_payload(args.source)
    blockers = [
        diagnostic
        for diagnostic in payload["diagnostics"]
        if diagnostic["severity"] == "blocker"
    ]
    if blockers:
        raise SystemExit(
            "Cannot generate audit instances because blockers remain: "
            + ", ".join(diagnostic["code"] for diagnostic in blockers)
        )

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(canonical_json(payload), encoding="utf-8")
    summary = payload["summary"]
    print(
        "Generated audit instances: "
        f"{summary['instance_count']} total, "
        f"{summary['product_count']} product, "
        f"{summary['process_count']} process, "
        f"{summary['company_count']} company"
    )


if __name__ == "__main__":
    main()
