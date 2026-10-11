from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .normalization import safe_text
from .privacy import PrivacyKey

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_hash TEXT NOT NULL UNIQUE,
    rows_seen INTEGER NOT NULL DEFAULT 0,
    rows_imported INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    warnings_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS bom_edges (
    edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    source_row INTEGER NOT NULL,
    top_key TEXT NOT NULL,
    parent_key TEXT NOT NULL,
    component_key TEXT NOT NULL,
    level INTEGER,
    quantity REAL,
    unit TEXT,
    valid_from TEXT,
    valid_to TEXT,
    date_valid INTEGER NOT NULL DEFAULT 1,
    change_key TEXT,
    alternative_key TEXT,
    plant_key TEXT
);

CREATE INDEX IF NOT EXISTS idx_bom_component ON bom_edges(component_key);
CREATE INDEX IF NOT EXISTS idx_bom_parent ON bom_edges(parent_key);
CREATE INDEX IF NOT EXISTS idx_bom_top ON bom_edges(top_key);
CREATE INDEX IF NOT EXISTS idx_bom_effectivity ON bom_edges(valid_from, valid_to);

CREATE TABLE IF NOT EXISTS sales_headers (
    sales_order_key TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    source_row INTEGER NOT NULL,
    status_category TEXT,
    requested_date TEXT
);

CREATE TABLE IF NOT EXISTS sales_items (
    sales_item_key TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    source_row INTEGER NOT NULL,
    sales_order_key TEXT NOT NULL,
    item_key TEXT NOT NULL,
    material_key TEXT NOT NULL,
    configuration_key TEXT,
    status_category TEXT,
    requested_date TEXT
);

CREATE INDEX IF NOT EXISTS idx_sales_material ON sales_items(material_key);
CREATE INDEX IF NOT EXISTS idx_sales_order_item
    ON sales_items(sales_order_key, item_key);

CREATE TABLE IF NOT EXISTS sales_item_metrics (
    sales_item_key TEXT PRIMARY KEY REFERENCES sales_items(sales_item_key),
    plant_key TEXT,
    order_quantity REAL
);

CREATE TABLE IF NOT EXISTS production_orders (
    production_order_key TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    source_row INTEGER NOT NULL,
    material_key TEXT NOT NULL,
    sales_order_key TEXT,
    sales_item_key TEXT,
    status_category TEXT NOT NULL,
    created_date TEXT,
    actual_start TEXT,
    actual_finish TEXT
);

CREATE INDEX IF NOT EXISTS idx_production_material
    ON production_orders(material_key);
CREATE INDEX IF NOT EXISTS idx_production_sales
    ON production_orders(sales_order_key, sales_item_key);

CREATE TABLE IF NOT EXISTS production_order_metrics (
    production_order_key TEXT PRIMARY KEY
        REFERENCES production_orders(production_order_key),
    plant_key TEXT,
    order_quantity REAL,
    delivered_quantity REAL,
    confirmed_quantity REAL
);

CREATE TABLE IF NOT EXISTS routing_assignments (
    assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    source_row INTEGER NOT NULL,
    material_key TEXT NOT NULL,
    plant_key TEXT,
    task_list_type TEXT,
    routing_group_key TEXT,
    group_counter TEXT,
    valid_from TEXT,
    valid_to TEXT,
    routing_version TEXT,
    UNIQUE(source_id, source_row)
);

CREATE INDEX IF NOT EXISTS idx_routing_material
    ON routing_assignments(material_key, plant_key);

CREATE TABLE IF NOT EXISTS change_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    source_row INTEGER NOT NULL,
    change_key TEXT NOT NULL,
    object_key TEXT,
    status_category TEXT,
    changed_date TEXT
);

CREATE INDEX IF NOT EXISTS idx_change_history_change
    ON change_history(change_key);

CREATE TABLE IF NOT EXISTS material_evidence (
    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    source_row INTEGER NOT NULL,
    material_key TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    plant_key TEXT,
    status_category TEXT NOT NULL DEFAULT '未提供',
    procurement_type TEXT,
    supplier_key TEXT
);

CREATE INDEX IF NOT EXISTS idx_material_evidence_material
    ON material_evidence(material_key);

CREATE TABLE IF NOT EXISTS planning_supply_scenarios (
    scenario_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    source_row INTEGER NOT NULL,
    data_attribute TEXT NOT NULL,
    old_material_key TEXT NOT NULL,
    new_material_key TEXT NOT NULL,
    plant_key TEXT NOT NULL,
    storage_location TEXT NOT NULL,
    data_date TEXT,
    effective_date TEXT,
    old_on_hand REAL,
    old_in_transit REAL,
    old_open_po REAL,
    old_consumable_demand REAL,
    new_on_hand REAL,
    new_in_transit REAL,
    new_open_po REAL,
    new_required_demand REAL,
    expected_recommendation TEXT
);

CREATE INDEX IF NOT EXISTS idx_planning_supply_match
    ON planning_supply_scenarios(
        old_material_key, new_material_key, plant_key, storage_location
    );

CREATE TABLE IF NOT EXISTS aliases (
    kind TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    alias TEXT NOT NULL,
    PRIMARY KEY(kind, entity_key),
    UNIQUE(kind, alias)
);

CREATE TABLE IF NOT EXISTS business_identifiers (
    kind TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    display_value TEXT NOT NULL,
    PRIMARY KEY(kind, entity_key)
);

CREATE TABLE IF NOT EXISTS analysis_runs (
    analysis_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    result_json TEXT NOT NULL,
    report_markdown TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS change_log (
    log_id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL,
    analysis_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    change_type TEXT NOT NULL,
    change_type_label TEXT NOT NULL,
    ecn TEXT NOT NULL,
    bom_version TEXT NOT NULL,
    target_material TEXT NOT NULL,
    effective_date TEXT,
    old_effective_date TEXT,
    new_effective_date TEXT,
    risk_level TEXT NOT NULL,
    upstream_paths INTEGER NOT NULL DEFAULT 0,
    downstream_paths INTEGER NOT NULL DEFAULT 0,
    top_materials INTEGER NOT NULL DEFAULT 0,
    material_validation_status TEXT NOT NULL DEFAULT '不适用',
    summary TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_change_log_created
    ON change_log(created_at DESC);
"""

DATA_TABLES = (
    "analysis_runs",
    "planning_supply_scenarios",
    "aliases",
    "business_identifiers",
    "material_evidence",
    "change_history",
    "routing_assignments",
    "production_order_metrics",
    "production_orders",
    "sales_item_metrics",
    "sales_items",
    "sales_headers",
    "bom_edges",
    "sources",
)


class Repository:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            if not connection.execute(
                "SELECT 1 FROM meta WHERE key='privacy_salt'"
            ).fetchone():
                connection.execute(
                    "INSERT INTO meta(key, value) VALUES('privacy_salt', ?)",
                    (PrivacyKey.create().to_text(),),
                )

    def privacy_key(self) -> PrivacyKey:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key='privacy_salt'"
            ).fetchone()
        if row is None:
            raise RuntimeError("内部数据关联密钥不存在")
        return PrivacyKey.from_text(row["value"])

    def start_snapshot(self) -> None:
        with self.connect() as connection:
            for table in DATA_TABLES:
                connection.execute(f"DELETE FROM {table}")
            connection.execute(
                "INSERT INTO meta(key, value) VALUES('privacy_salt', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (PrivacyKey.create().to_text(),),
            )
            connection.execute(
                "INSERT INTO meta(key, value) VALUES('snapshot_started_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (datetime.now(timezone.utc).isoformat(),),
            )
            connection.execute(
                "DELETE FROM meta WHERE key IN ('snapshot_ready_at', 'last_analysis_id')"
            )

    def next_source_id(self) -> str:
        with self.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        return f"SRC-{count + 1:03d}"

    def source_exists(self, source_hash: str) -> bool:
        with self.connect() as connection:
            return bool(
                connection.execute(
                    "SELECT 1 FROM sources WHERE source_hash=?", (source_hash,)
                ).fetchone()
            )

    def add_source(
        self,
        source_id: str,
        source_type: str,
        source_hash: str,
        rows_seen: int,
        rows_imported: int,
        warnings: list[str],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO sources(
                    source_id, source_type, source_hash, rows_seen,
                    rows_imported, warning_count, warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    source_type,
                    source_hash,
                    rows_seen,
                    rows_imported,
                    len(warnings),
                    json.dumps(warnings, ensure_ascii=False),
                ),
            )

    @staticmethod
    def register_business_identifier(
        connection: sqlite3.Connection,
        kind: str,
        entity_key: str | None,
        raw_value: Any,
    ) -> None:
        """Persist only approved business codes; never descriptions or names."""
        display_value = safe_text(raw_value, 120)
        if not entity_key or not display_value:
            return
        connection.execute(
            """
            INSERT INTO business_identifiers(kind, entity_key, display_value)
            VALUES (?, ?, ?)
            ON CONFLICT(kind, entity_key) DO UPDATE SET
                display_value=excluded.display_value
            """,
            (kind, entity_key, display_value),
        )

    def finish_snapshot(self) -> dict[str, Any]:
        self.assign_aliases()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO meta(key, value) VALUES('snapshot_ready_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (datetime.now(timezone.utc).isoformat(),),
            )
        return self.stats()

    @staticmethod
    def _alpha_label(index: int) -> str:
        value = index + 1
        letters = ""
        while value:
            value, remainder = divmod(value - 1, 26)
            letters = chr(65 + remainder) + letters
        return letters

    def assign_aliases(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM aliases")

            def keys(sql: str) -> list[str]:
                return [
                    row[0]
                    for row in connection.execute(sql).fetchall()
                    if row[0]
                ]

            top_keys = keys("SELECT DISTINCT top_key FROM bom_edges ORDER BY top_key")
            parent_keys = keys(
                "SELECT DISTINCT parent_key FROM bom_edges ORDER BY parent_key"
            )
            component_keys = keys(
                "SELECT DISTINCT component_key FROM bom_edges ORDER BY component_key"
            )
            material_keys = sorted(
                set(top_keys)
                | set(parent_keys)
                | set(component_keys)
                | set(keys("SELECT DISTINCT material_key FROM sales_items"))
                | set(keys("SELECT DISTINCT material_key FROM production_orders"))
                | set(keys("SELECT DISTINCT material_key FROM material_evidence"))
                | set(keys("SELECT DISTINCT old_material_key FROM planning_supply_scenarios"))
                | set(keys("SELECT DISTINCT new_material_key FROM planning_supply_scenarios"))
            )

            rows: list[tuple[str, str, str]] = []
            rows.extend(
                ("top", key, f"FG-{self._alpha_label(index)}")
                for index, key in enumerate(top_keys)
            )
            rows.extend(
                ("assembly", key, f"ASM-{index + 1:04d}")
                for index, key in enumerate(parent_keys)
            )
            rows.extend(
                ("component", key, f"COMP-{index + 1:04d}")
                for index, key in enumerate(component_keys)
            )
            rows.extend(
                ("material", key, f"MAT-{index + 1:04d}")
                for index, key in enumerate(material_keys)
            )
            rows.extend(
                ("sales_order", key, f"SO-{index + 1:03d}")
                for index, key in enumerate(
                    keys(
                        "SELECT sales_order_key FROM sales_headers "
                        "UNION SELECT sales_order_key FROM sales_items "
                        "WHERE sales_order_key IS NOT NULL ORDER BY 1"
                    )
                )
            )
            rows.extend(
                ("production_order", key, f"MO-{index + 1:03d}")
                for index, key in enumerate(
                    keys(
                        "SELECT DISTINCT production_order_key "
                        "FROM production_orders ORDER BY production_order_key"
                    )
                )
            )
            rows.extend(
                ("change", key, f"EC-{index + 1:03d}")
                for index, key in enumerate(
                    keys(
                        "SELECT change_key FROM change_history "
                        "UNION SELECT change_key FROM bom_edges "
                        "WHERE change_key IS NOT NULL ORDER BY 1"
                    )
                )
            )
            rows.extend(
                ("alternative", key, f"ALT-{index + 1:03d}")
                for index, key in enumerate(
                    keys(
                        "SELECT DISTINCT alternative_key FROM bom_edges "
                        "WHERE alternative_key IS NOT NULL ORDER BY alternative_key"
                    )
                )
            )
            connection.executemany(
                "INSERT INTO aliases(kind, entity_key, alias) VALUES (?, ?, ?)", rows
            )

    def stats(self) -> dict[str, Any]:
        with self.connect() as connection:
            source_rows = connection.execute(
                "SELECT source_id, source_type, rows_seen, rows_imported, "
                "warning_count FROM sources ORDER BY source_id"
            ).fetchall()
            top_rows = connection.execute(
                """
                SELECT top_key,
                       COUNT(DISTINCT source_id) AS version_count,
                       COUNT(*) AS imported_rows,
                       SUM(CASE WHEN date_valid=1 THEN 1 ELSE 0 END) AS valid_rows,
                       MAX(level) AS max_level,
                       GROUP_CONCAT(DISTINCT source_id) AS source_ids
                FROM bom_edges
                GROUP BY top_key
                ORDER BY top_key
                """
            ).fetchall()
            top_aliases = {
                row["entity_key"]: row["alias"]
                for row in connection.execute(
                    "SELECT entity_key, alias FROM aliases WHERE kind='top'"
                )
            }
            material_aliases = {
                row["entity_key"]: row["alias"]
                for row in connection.execute(
                    "SELECT entity_key, alias FROM aliases WHERE kind='material'"
                )
            }
            business_materials = {
                row["entity_key"]: row["display_value"]
                for row in connection.execute(
                    "SELECT entity_key, display_value "
                    "FROM business_identifiers WHERE kind='material'"
                )
            }

            def scalar(sql: str) -> int:
                return int(connection.execute(sql).fetchone()[0] or 0)

            top_bom_details = [
                {
                    "bom": business_materials.get(
                        row["top_key"],
                        top_aliases.get(row["top_key"], "BOM-UNKNOWN"),
                    ),
                    "material": business_materials.get(
                        row["top_key"],
                        material_aliases.get(row["top_key"], "MAT-UNKNOWN"),
                    ),
                    "version_count": int(row["version_count"] or 0),
                    "imported_rows": int(row["imported_rows"] or 0),
                    "valid_rows": int(row["valid_rows"] or 0),
                    "max_level": int(row["max_level"] or 0),
                    "sources": sorted(
                        value
                        for value in (row["source_ids"] or "").split(",")
                        if value
                    ),
                }
                for row in top_rows
            ]
            return {
                "ready": bool(
                    connection.execute(
                        "SELECT 1 FROM meta WHERE key='snapshot_ready_at'"
                    ).fetchone()
                ),
                "sources": [dict(row) for row in source_rows],
                "source_count": len(source_rows),
                "bom_edges": scalar("SELECT COUNT(*) FROM bom_edges"),
                "valid_bom_rows": scalar(
                    "SELECT COUNT(*) FROM bom_edges WHERE date_valid=1"
                ),
                "excluded_bom_rows": scalar(
                    "SELECT COUNT(*) FROM bom_edges WHERE date_valid=0"
                ),
                "top_materials": scalar(
                    "SELECT COUNT(DISTINCT top_key) FROM bom_edges"
                ),
                "bom_versions": sum(
                    detail["version_count"] for detail in top_bom_details
                ),
                "multi_version_boms": sum(
                    1 for detail in top_bom_details if detail["version_count"] > 1
                ),
                "top_bom_details": top_bom_details,
                "parents": scalar("SELECT COUNT(DISTINCT parent_key) FROM bom_edges"),
                "components": scalar(
                    "SELECT COUNT(DISTINCT component_key) FROM bom_edges"
                ),
                "max_level": scalar("SELECT MAX(level) FROM bom_edges"),
                "sales_items": scalar("SELECT COUNT(*) FROM sales_items"),
                "production_orders": scalar("SELECT COUNT(*) FROM production_orders"),
                "routing_assignments": scalar(
                    "SELECT COUNT(*) FROM routing_assignments"
                ),
                "change_history_rows": scalar("SELECT COUNT(*) FROM change_history"),
                "material_evidence_rows": scalar(
                    "SELECT COUNT(*) FROM material_evidence"
                ),
                "planning_supply_scenarios": scalar(
                    "SELECT COUNT(*) FROM planning_supply_scenarios"
                ),
                "material_master_materials": scalar(
                    "SELECT COUNT(DISTINCT material_key) FROM material_evidence "
                    "WHERE evidence_type IN "
                    "('material_master_reference','plant_master_reference',"
                    "'sales_master_reference')"
                ),
                "sourcing_materials": scalar(
                    "SELECT COUNT(DISTINCT material_key) FROM material_evidence "
                    "WHERE evidence_type='source_list_reference'"
                ),
                "change_log_count": scalar("SELECT COUNT(*) FROM change_log"),
            }

    def routing_assignments(
        self, materials: list[str] | set[str]
    ) -> list[dict[str, Any]]:
        material_keys = [
            key for value in materials if (key := self.resolve_material(value))
        ]
        if not material_keys:
            return []
        placeholders = ",".join("?" for _ in material_keys)
        aliases = self.alias_maps()
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM routing_assignments
                WHERE material_key IN ({placeholders})
                ORDER BY material_key, plant_key, valid_from, source_id, source_row
                """,
                tuple(material_keys),
            ).fetchall()
        return [
            {
                "material": aliases.get("material", {}).get(
                    row["material_key"], "MAT-UNKNOWN"
                ),
                "plant": aliases.get("plant", {}).get(row["plant_key"], "—"),
                "task_list_type": row["task_list_type"] or "—",
                "routing_group": aliases.get("routing_group", {}).get(
                    row["routing_group_key"], "—"
                ),
                "group_counter": row["group_counter"] or "—",
                "valid_from": row["valid_from"] or "—",
                "valid_to": row["valid_to"] or "—",
                "routing_version": row["routing_version"] or "未提供",
                "source_id": row["source_id"],
                "source_row": row["source_row"],
            }
            for row in rows
        ]

    def resolve_material(self, value: str) -> str | None:
        candidate = value.strip().upper()
        if not candidate:
            return None
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT entity_key FROM business_identifiers
                WHERE kind='material' AND UPPER(display_value)=?
                LIMIT 1
                """,
                (candidate,),
            ).fetchone()
            if row:
                return row["entity_key"]

            row = connection.execute(
                """
                SELECT entity_key FROM aliases
                WHERE UPPER(alias)=? AND kind IN ('top','assembly','component','material')
                ORDER BY CASE kind
                    WHEN 'top' THEN 1 WHEN 'component' THEN 2
                    WHEN 'assembly' THEN 3 ELSE 4 END
                LIMIT 1
                """,
                (candidate,),
            ).fetchone()
            if row:
                return row["entity_key"]

            key = self.privacy_key().digest("material", value)
            found = connection.execute(
                """
                SELECT 1 FROM (
                    SELECT top_key AS key FROM bom_edges
                    UNION SELECT parent_key FROM bom_edges
                    UNION SELECT component_key FROM bom_edges
                    UNION SELECT material_key FROM sales_items
                    UNION SELECT material_key FROM production_orders
                    UNION SELECT material_key FROM material_evidence
                    UNION SELECT old_material_key FROM planning_supply_scenarios
                    UNION SELECT new_material_key FROM planning_supply_scenarios
                ) WHERE key=? LIMIT 1
                """,
                (key,),
            ).fetchone()
            return key if found else None

    def planning_supply_scenario(
        self,
        old_material: str,
        new_material: str,
        plant: str = "",
        storage_location: str = "",
    ) -> dict[str, Any] | None:
        """Return one unambiguous supply snapshot bound to material/plant/location."""
        old_key = self.resolve_material(old_material)
        new_key = self.resolve_material(new_material)
        if not old_key or not new_key:
            return None
        clauses = ["old_material_key=?", "new_material_key=?"]
        params: list[Any] = [old_key, new_key]
        if plant.strip():
            plant_key = self.privacy_key().digest("plant", plant)
            clauses.append("plant_key=?")
            params.append(plant_key)
        if storage_location.strip():
            clauses.append("UPPER(storage_location)=UPPER(?)")
            params.append(storage_location.strip())
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM planning_supply_scenarios
                WHERE {' AND '.join(clauses)}
                ORDER BY data_date DESC, source_id DESC, source_row DESC
                """,
                params,
            ).fetchall()
        if len(rows) != 1:
            return None
        row = dict(rows[0])
        aliases = self.alias_maps()
        row["old_material"] = aliases.get("material", {}).get(old_key, old_material)
        row["new_material"] = aliases.get("material", {}).get(new_key, new_material)
        row["plant"] = aliases.get("plant", {}).get(row["plant_key"], plant or "—")
        return row

    def alias_maps(self) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        with self.connect() as connection:
            for row in connection.execute(
                "SELECT kind, entity_key, alias FROM aliases"
            ):
                result.setdefault(row["kind"], {})[row["entity_key"]] = row["alias"]
            business_rows = connection.execute(
                "SELECT kind, entity_key, display_value FROM business_identifiers"
            ).fetchall()
            for row in business_rows:
                result.setdefault(row["kind"], {})[
                    row["entity_key"]
                ] = row["display_value"]
                if row["kind"] == "material":
                    for role in ("top", "assembly", "component"):
                        if row["entity_key"] in result.get(role, {}):
                            result[role][row["entity_key"]] = row["display_value"]
        return result

    def material_catalog(self, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        query = query.strip().upper()
        params: list[Any] = []
        where = "WHERE m.kind='material'"
        if query:
            where += " AND UPPER(COALESCE(b.display_value, m.alias)) LIKE ?"
            params.append(f"%{query}%")
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT COALESCE(b.display_value, m.alias) AS display_value,
                       m.entity_key,
                       (
                         SELECT COUNT(DISTINCT b.parent_key)
                         FROM bom_edges b
                         WHERE b.component_key=m.entity_key
                       ) AS parent_count,
                       (
                         SELECT COUNT(DISTINCT b.component_key)
                         FROM bom_edges b
                         WHERE b.parent_key=m.entity_key
                       ) AS child_count,
                       (
                         SELECT COUNT(DISTINCT b.top_key)
                         FROM bom_edges b
                         WHERE b.component_key=m.entity_key
                            OR b.parent_key=m.entity_key
                            OR b.top_key=m.entity_key
                       ) AS top_count
                FROM aliases m
                LEFT JOIN business_identifiers b
                  ON b.kind='material' AND b.entity_key=m.entity_key
                {where}
                ORDER BY parent_count DESC, child_count DESC, display_value
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            {
                "alias": row["display_value"],
                "parent_count": row["parent_count"],
                "child_count": row["child_count"],
                "top_count": row["top_count"],
            }
            for row in rows
        ]

    def material_effectivity(self, value: str) -> dict[str, Any]:
        material_key = self.resolve_material(value)
        if not material_key:
            raise ValueError("未在当前 BOM 快照中找到该组件物料号")
        alias_maps = self.alias_maps()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT valid_from,
                       COUNT(*) AS usage_count,
                       COUNT(DISTINCT top_key) AS top_count
                FROM bom_edges
                WHERE component_key=?
                  AND date_valid=1
                  AND valid_from IS NOT NULL
                GROUP BY valid_from
                ORDER BY usage_count DESC, valid_from DESC
                """,
                (material_key,),
            ).fetchall()
            total_uses = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM bom_edges
                    WHERE component_key=? AND date_valid=1
                    """,
                    (material_key,),
                ).fetchone()[0]
                or 0
            )
        dates = [
            {
                "date": row["valid_from"],
                "usage_count": int(row["usage_count"] or 0),
                "top_count": int(row["top_count"] or 0),
            }
            for row in rows
        ]
        return {
            "material": alias_maps.get("material", {}).get(
                material_key, safe_text(value, 120)
            ),
            "default_date": dates[0]["date"] if dates else "",
            "dates": dates,
            "total_uses": total_uses,
            "ambiguous": len(dates) > 1,
        }

    def save_analysis(self, analysis_id: str, result: dict[str, Any], report: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO analysis_runs VALUES (?, ?, ?, ?)",
                (
                    analysis_id,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(result, ensure_ascii=False),
                    report,
                ),
            )
            connection.execute(
                "INSERT INTO meta(key, value) VALUES('last_analysis_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (analysis_id,),
            )

    def analysis_by_id(self, analysis_id: str) -> tuple[dict[str, Any], str] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT result_json, report_markdown
                FROM analysis_runs
                WHERE analysis_id=?
                """,
                (analysis_id,),
            ).fetchone()
        if not row:
            return None
        return json.loads(row["result_json"]), row["report_markdown"]

    def analysis_results_for_bom_version(
        self, bom_version: str
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.result_json
                FROM change_log c
                JOIN analysis_runs a ON a.analysis_id=c.analysis_id
                WHERE c.bom_version=?
                ORDER BY c.created_at, c.log_id
                """,
                (bom_version,),
            ).fetchall()
        return [json.loads(row["result_json"]) for row in rows]

    def material_readiness(self, value: str) -> dict[str, Any]:
        material_key = self.privacy_key().digest("material", value)
        if not material_key:
            return {
                "applicable": True,
                "material": safe_text(value, 120) or "待填写物料",
                "status": "待补证",
                "checks": [],
                "evidence_sources": [],
            }
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT evidence_type, status_category, procurement_type,
                       supplier_key, source_id
                FROM material_evidence
                WHERE material_key=?
                ORDER BY evidence_type, source_id
                """,
                (material_key,),
            ).fetchall()
            alias_row = connection.execute(
                """
                SELECT COALESCE(b.display_value, a.alias) AS display_value
                FROM aliases a
                LEFT JOIN business_identifiers b
                  ON b.kind='material' AND b.entity_key=a.entity_key
                WHERE a.kind='material' AND a.entity_key=?
                """,
                (material_key,),
            ).fetchone()

        by_type: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            by_type.setdefault(row["evidence_type"], []).append(row)

        def check(
            code: str,
            label: str,
            evidence_type: str,
            *,
            require_procurement: bool = False,
            require_supplier: bool = False,
        ) -> dict[str, Any]:
            evidence = by_type.get(evidence_type, [])
            blocked = any(
                row["status_category"] == "受限或停用" for row in evidence
            )
            if blocked:
                status = "不通过"
                detail = "证据中存在受限、冻结或停用状态"
            elif require_procurement:
                available = any(row["procurement_type"] for row in evidence)
                status = "通过" if available else "待补证"
                detail = (
                    "已识别工厂级采购类型"
                    if available
                    else "未找到工厂级采购类型证据"
                )
            elif require_supplier:
                available = any(row["supplier_key"] for row in evidence)
                status = "通过" if available else "待补证"
                detail = (
                    "已识别供应来源（供应商名称显示为 XXX）"
                    if available
                    else "未导入供应商来源清单或 AVL/AML 证据"
                )
            else:
                status = "通过" if evidence else "待补证"
                detail = (
                    f"已识别 {len(evidence)} 条脱敏证据"
                    if evidence
                    else "当前快照未覆盖"
                )
            return {"code": code, "label": label, "status": status, "detail": detail}

        checks = [
            check(
                "GENERAL_MASTER",
                "通用物料主数据",
                "material_master_reference",
            ),
            check(
                "PLANT_MASTER",
                "工厂级物料视图",
                "plant_master_reference",
            ),
            check(
                "PROCUREMENT_ROUTE",
                "采购类型与补货路径",
                "plant_master_reference",
                require_procurement=True,
            ),
            check(
                "SOURCE_LIST",
                "供应来源 / AVL·AML",
                "source_list_reference",
                require_supplier=True,
            ),
            {
                "code": "QUALITY_CERTIFICATION",
                "label": "质量与认证",
                "status": "待补证",
                "detail": "当前未接入质量、认证和承认状态数据",
            },
        ]
        statuses = {item["status"] for item in checks}
        overall = (
            "不通过"
            if "不通过" in statuses
            else "待补证"
            if "待补证" in statuses
            else "通过"
        )
        return {
            "applicable": True,
            "material": alias_row["display_value"] if alias_row else safe_text(value, 120),
            "status": overall,
            "checks": checks,
            "evidence_sources": sorted({row["source_id"] for row in rows}),
        }

    def save_change_log(self, entry: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO change_log(
                    log_id, draft_id, analysis_id, created_at,
                    change_type, change_type_label, ecn, bom_version,
                    target_material, effective_date, old_effective_date,
                    new_effective_date, risk_level, upstream_paths,
                    downstream_paths, top_materials,
                    material_validation_status, summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry["log_id"],
                    entry["draft_id"],
                    entry["analysis_id"],
                    entry["created_at"],
                    entry["change_type"],
                    entry["change_type_label"],
                    entry["ecn"],
                    entry["bom_version"],
                    entry["target_material"],
                    entry.get("effective_date"),
                    entry.get("old_effective_date"),
                    entry.get("new_effective_date"),
                    entry["risk_level"],
                    entry["upstream_paths"],
                    entry["downstream_paths"],
                    entry["top_materials"],
                    entry["material_validation_status"],
                    entry["summary"],
                ),
            )

    def change_logs(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM change_log
                ORDER BY created_at DESC, log_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def draft_defaults(self) -> dict[str, str]:
        with self.connect() as connection:
            ecn_rows = connection.execute(
                "SELECT ecn FROM change_log"
            ).fetchall()
            version_rows = connection.execute(
                "SELECT bom_version FROM change_log"
            ).fetchall()
            counter_row = connection.execute(
                "SELECT value FROM meta WHERE key='ecn_sequence'"
            ).fetchone()
        ecn_numbers = [
            int(match.group(1))
            for row in ecn_rows
            if (match := re.fullmatch(r"ECN-(\d{6})", row["ecn"] or ""))
        ]
        version_numbers = [
            int(match.group(1))
            for row in version_rows
            if (match := re.fullmatch(r"BOM-(\d{6})", row["bom_version"] or ""))
        ]
        counter = int(counter_row["value"]) if counter_row else 0
        next_ecn = max([3, counter, *ecn_numbers]) + 1
        default_version = max([1, *version_numbers])
        return {
            "next_ecn": f"ECN-{next_ecn:06d}",
            "default_bom_version": f"BOM-{default_version:06d}",
        }

    def reserve_ecns(self, count: int) -> list[str]:
        if count <= 0:
            return []
        with self.connect() as connection:
            rows = connection.execute("SELECT ecn FROM change_log").fetchall()
            counter_row = connection.execute(
                "SELECT value FROM meta WHERE key='ecn_sequence'"
            ).fetchone()
            numbers = [
                int(match.group(1))
                for row in rows
                if (match := re.fullmatch(r"ECN-(\d{6})", row["ecn"] or ""))
            ]
            counter = int(counter_row["value"]) if counter_row else 0
            start = max([3, counter, *numbers]) + 1
            end = start + count - 1
            connection.execute(
                """
                INSERT INTO meta(key, value) VALUES('ecn_sequence', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(end),),
            )
        return [f"ECN-{number:06d}" for number in range(start, end + 1)]

    def last_analysis(self) -> tuple[dict[str, Any], str] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT result_json, report_markdown FROM analysis_runs
                WHERE analysis_id=(
                    SELECT value FROM meta WHERE key='last_analysis_id'
                )
                """
            ).fetchone()
        if not row:
            return None
        return json.loads(row["result_json"]), row["report_markdown"]
