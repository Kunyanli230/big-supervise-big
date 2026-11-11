"""Keep missing scores unknown, and C aliases out of actual total spending."""

import json
from pathlib import Path

from .storage import verify_patch


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(root: Path):
    cells, costs, partial_costs = {}, [], []
    for result_path in sorted(root.rglob("result.json")):
        result = read(result_path)
        if "arm" not in result:
            continue
        key = (result["instance_id"], result["arm"])
        if key in cells:
            raise ValueError(f"Duplicate experiment cell {key}; do not select the best rerun")
        grade_path = result_path.parent / "grading" / "grade.json"
        cells[key] = None
        if grade_path.exists():
            verify_patch(result_path.parent)
            grade = read(grade_path)
            if grade["patch_sha256"] != read(result_path.parent / "patch-seal.json")["sha256"]:
                raise ValueError("Grade does not belong to sealed patch")
            cells[key] = grade["resolved"]
        usage = result.get("usage") or {}
        costs.append(usage.get("cost_usd"))
        partial_costs.append(usage.get("known_actual_cost_usd", usage.get("cost_usd")) or 0)
    ids = sorted({task for task, _ in cells})
    matrix = []
    for task in ids:
        runs = [cells.get((task, f"A_r{r}")) for r in (1, 2, 3)]
        oracle = True if True in runs else (False if all(v is False for v in runs) else None)
        matrix.append({"instance_id": task, "A_r1": runs[0], "A_r2": runs[1], "A_r3": runs[2],
                       "A": oracle, "B": cells.get((task, "B")),
                       "C": cells.get((task, "C")) if (task, "C") in cells else cells.get((task, "A_r1"))})
    groups = {}
    for arm in ("A", "B", "C"):
        values = [row[arm] for row in matrix]
        groups[arm] = {"tasks": len(ids), "known": sum(v is not None for v in values),
                       "successes": sum(v is True for v in values), "unknown": sum(v is None for v in values)}
    paired = {}
    for other in ("A", "C"):
        pairs = [(row["B"], row[other]) for row in matrix if row["B"] is not None and row[other] is not None]
        paired[f"B_minus_{other}"] = {
            "common_tasks": len(pairs), "wins": sum(b and not a for b, a in pairs),
            "losses": sum(a and not b for b, a in pairs),
            "difference": sum(int(b) - int(a) for b, a in pairs) / len(pairs) if pairs else None,
        }
    return {"groups": groups, "paired": paired, "matrix": matrix,
            "actual_total_cost_usd": sum(costs) if all(c is not None for c in costs) else None,
            "known_actual_cost_usd": sum(partial_costs)}
