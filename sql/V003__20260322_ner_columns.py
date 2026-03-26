"""
Migration V003 — 2026-03-22 — NER tracking columns on chunk tables
Adds ner_tags, ner_tagged, ner_tagged_at, ner_version to all existing {collection_id}_chunks tables.
"""

DESCRIPTION = "Add NER columns (ner_tags, ner_tagged, ner_tagged_at, ner_version) to all chunk tables"


def up(db) -> None:
    table_names = db.table_names()
    for name in table_names:
        if not name.endswith("_chunks"):
            continue
        tbl = db.open_table(name)
        schema_names = tbl.schema.names

        if "ner_tags" not in schema_names:
            tbl.add_columns({"ner_tags": "cast('' as string)"})
            print(f"  + ner_tags → {name}")

        if "ner_tagged" not in schema_names:
            tbl.add_columns({"ner_tagged": "cast(false as boolean)"})
            print(f"  + ner_tagged → {name}")

        if "ner_tagged_at" not in schema_names:
            tbl.add_columns({"ner_tagged_at": "cast(0 as bigint)"})
            print(f"  + ner_tagged_at → {name}")

        if "ner_version" not in schema_names:
            tbl.add_columns({"ner_version": "cast(0 as int)"})
            print(f"  + ner_version → {name}")
