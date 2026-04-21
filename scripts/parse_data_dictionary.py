"""Parse deid_uf_data_dictionary.xlsx into schema_reference.json"""

import json
import sys
from pathlib import Path

import openpyxl

def parse_data_dictionary(xlsx_path: str, output_path: str):
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)

    # Parse Tables sheet
    tables_sheet = wb["Tables"]
    tables_rows = list(tables_sheet.iter_rows(values_only=True))
    tables_header = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(tables_rows[0])]
    tables = {}
    for row in tables_rows[1:]:
        record = dict(zip(tables_header, row))
        name = record.get("table_name")
        if name:
            tables[name] = {
                "description": record.get("table_description", ""),
                "has_patient_data": record.get("has_pat_specific_data") == "Y",
                "has_phi": record.get("has_PHI") == "Y",
                "has_encounter_data": record.get("has_enc_specific_data") == "Y",
                "patient_key_column": record.get("PatientKey_Col"),
                "encounter_key_column": record.get("EncounterKey_Col"),
            }

    # Parse Columns sheet
    cols_sheet = wb["Columns"]
    cols_rows = list(cols_sheet.iter_rows(values_only=True))
    cols_header = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(cols_rows[0])]

    columns_by_table = {}
    for row in cols_rows[1:]:
        record = dict(zip(cols_header, row))
        table_name = record.get("table_name")
        if not table_name:
            continue
        if table_name not in columns_by_table:
            columns_by_table[table_name] = []
        col_name = record.get("column_name", "")
        col_info = {
            "name": col_name,
            "description": record.get("column_description", ""),
            "data_type": record.get("data_type"),
            "ordinal_position": record.get("ordinal_position"),
        }
        # Flag columns that may not be directly queryable in SQL
        # *KeyValue columns are computed/denormalized and often don't exist in views
        if col_name and col_name.endswith("KeyValue"):
            col_info["queryable"] = False
            col_info["note"] = "Computed column — may not exist in SQL view. Use the corresponding *Key column (integer YYYYMMDD format) instead."
        lookup = record.get("lookupTableName")
        if lookup:
            col_info["lookup_table"] = lookup
            col_info["lookup_type"] = record.get("lookupType")
        columns_by_table[table_name].append(col_info)

    # Merge
    schema = {}
    all_table_names = set(tables.keys()) | set(columns_by_table.keys())
    for name in sorted(all_table_names):
        entry = tables.get(name, {"description": "", "has_patient_data": False, "has_phi": False})
        entry["columns"] = columns_by_table.get(name, [])
        schema[name] = entry

    wb.close()

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(schema, f, indent=2, default=str)

    print(f"Wrote {len(schema)} tables to {output_path}")


if __name__ == "__main__":
    project_root = Path(__file__).parent.parent
    default_xlsx = project_root / "deid_uf_data_dictionary.xlsx"
    default_output = project_root / "data" / "schema_reference.json"

    xlsx = Path(sys.argv[1]) if len(sys.argv) > 1 else default_xlsx
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else default_output

    if not xlsx.exists():
        sys.stderr.write(
            f"Data dictionary xlsx not found at: {xlsx}\n"
            "\n"
            "The data dictionary is NOT bundled with this repository.\n"
            "To regenerate data/schema_reference.json, obtain the Epic Caboodle\n"
            "data dictionary xlsx through your institution's CDW governance\n"
            "channel and pass its path:\n"
            "\n"
            "  uv run python scripts/parse_data_dictionary.py /path/to/dict.xlsx\n"
            "\n"
            "The runtime does not need the xlsx — only the parsed JSON, which\n"
            "is already committed.\n"
        )
        sys.exit(1)

    parse_data_dictionary(str(xlsx), str(output))
