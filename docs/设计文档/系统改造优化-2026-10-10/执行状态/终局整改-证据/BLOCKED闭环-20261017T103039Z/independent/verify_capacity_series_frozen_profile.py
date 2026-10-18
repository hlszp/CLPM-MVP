"""Original independent nine-condition checker with the pre-frozen resource method.

The original checker remains byte-identical. This adapter replaces only its old
RSS-only accounting, without relabeling cgroup memory.current as process RSS.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from independent_resource_accounting import reconcile
from independent_support import (
    InvalidEvidence,
    MissingEvidence,
    need,
    result_rc,
    verdict,
    write_new,
)
from verify_capacity_series import Auditor


class FrozenProfileAuditor(Auditor):
    def __init__(self, root):
        super().__init__(root)
        self.resource_results = {}

    def resources(self, raw, submitted, observed, batch):
        if raw.get("accountingMethod") != "CgroupCurrentPlusHostPIDRSS-v1":
            raise MissingEvidence(
                "Explicit frozen conservative resource accounting required"
            )
        profile = self.reads.json(
            need(raw.get("profile"), "resource profile reference missing")
        )
        runtime = self.reads.json(
            need(
                raw.get("runtimeProfile"), "resource runtime profile reference missing"
            )
        )
        path = self.reads.path(
            need(
                raw.get("resourcesJsonl"), "original resources JSONL reference missing"
            )
        )
        samples = [
            json.loads(line) for line in path.read_text().splitlines() if line.strip()
        ]
        result = reconcile(
            samples,
            runtime["runtime"]["members"],
            profile,
            submitted.isoformat(),
            observed.isoformat(),
        )
        self.refs(raw.get("rawRefs"), "resource.originals-missing", batch)
        for issue in result["issues"]:
            self.issue(
                issue["level"], "resource." + issue["code"], issue["detail"], batch
            )
        self.resource_results[batch] = result

    def run(self, series):
        result = super().run(series)
        result["resourceAccounting"] = "CgroupCurrentPlusHostPIDRSS-v1"
        result["independentRawResourceResults"] = self.resource_results
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    auditor = FrozenProfileAuditor(args.evidence_root)
    try:
        result = auditor.run(json.loads(args.series.read_text()))
    except (MissingEvidence, KeyError, FileNotFoundError) as exc:
        auditor.issue("BLOCKED", "series.evidence-incomplete", str(exc))
        result = {
            "artifactContractVerdict": verdict(auditor.issues),
            "issues": auditor.issues,
            "readSet": auditor.reads.files,
        }
    except (InvalidEvidence, ValueError, TypeError) as exc:
        auditor.issue("REWORK", "series.evidence-invalid", str(exc))
        result = {
            "artifactContractVerdict": verdict(auditor.issues),
            "issues": auditor.issues,
            "readSet": auditor.reads.files,
        }
    result["networkOrBusinessOperations"] = False
    write_new(args.out, result)
    print(
        json.dumps(
            {
                "verdict": result["artifactContractVerdict"],
                "issues": len(result["issues"]),
                "out": str(args.out),
            },
            ensure_ascii=False,
        )
    )
    return result_rc(result["artifactContractVerdict"])


if __name__ == "__main__":
    raise SystemExit(main())
