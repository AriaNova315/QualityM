from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.excel.spike_builder import ExcelSpikeSnapshot, build_excel_linkage_spike
from app.excel.validator import validate_excel_linkage_spike


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the QualityM Step 5 Excel linkage spike")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    artifacts = build_excel_linkage_spike(
        ExcelSpikeSnapshot(),
        output_dir=args.output_dir,
        template_dir=ROOT / "temp",
    )
    validation = validate_excel_linkage_spike(
        artifacts.phase_workbook,
        artifacts.trend_workbook,
    )
    print(f"phase={artifacts.phase_workbook}")
    print(f"trend={artifacts.trend_workbook}")
    print(f"external_links={len(validation.external_link_parts)} charts={len(validation.chart_parts)}")


if __name__ == "__main__":
    main()
