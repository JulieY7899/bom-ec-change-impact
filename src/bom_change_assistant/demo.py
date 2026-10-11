from __future__ import annotations

import json

from .repository import Repository


def load_demo_snapshot(repository: Repository) -> dict:
    repository.start_snapshot()
    privacy = repository.privacy_key()

    def material(value: str) -> str:
        return privacy.digest("material", value)

    def change(value: str) -> str:
        return privacy.digest("change", value)

    def alternative(value: str) -> str | None:
        return privacy.digest("alternative", value) or None

    edges = [
        ("FG-A", "FG-A", "ASM-100", 1, 1, None),
        ("FG-A", "ASM-100", "COMP-001", 2, 2, "A1"),
        ("FG-A", "ASM-100", "COMP-002", 2, 1, None),
        ("FG-A", "FG-A", "ASM-110", 1, 1, None),
        ("FG-A", "ASM-110", "COMP-001", 2, 1, None),
        ("FG-B", "FG-B", "ASM-200", 1, 1, None),
        ("FG-B", "ASM-200", "SUB-210", 2, 1, None),
        ("FG-B", "SUB-210", "COMP-001", 3, 4, None),
        ("FG-B", "ASM-200", "COMP-003", 2, 2, None),
    ]
    with repository.connect() as connection:
        connection.execute(
            """
            INSERT INTO sources VALUES
            ('SRC-001','expanded_bom','demo-bom',9,9,0,'[]'),
            ('SRC-002','sales_order_item','demo-sales',2,2,0,'[]'),
            ('SRC-003','production_order','demo-production',2,2,0,'[]'),
            ('SRC-004','change_history','demo-change',1,1,0,'[]'),
            ('SRC-005','material_master_reference+plant_master_reference+source_list_reference',
             'demo-master',3,3,0,'[]')
            """
        )
        connection.executemany(
            """
            INSERT INTO bom_edges(
                source_id, source_row, top_key, parent_key, component_key,
                level, quantity, unit, valid_from, valid_to, date_valid,
                change_key, alternative_key, plant_key
            ) VALUES ('SRC-001', ?, ?, ?, ?, ?, ?, 'EA',
                      '2025-01-01', '2099-12-31', 1, ?, ?, ?)
            """,
            [
                (
                    index + 2,
                    material(top),
                    material(parent),
                    material(component),
                    level,
                    quantity,
                    change("CHANGE-DEMO-ALPHA"),
                    alternative(alt or ""),
                    privacy.digest("plant", "P1"),
                )
                for index, (top, parent, component, level, quantity, alt) in enumerate(
                    edges
                )
            ],
        )

        sales = [
            ("SALES-DEMO-ALPHA", "10", "FG-A", "CFG-DEMO-A"),
            ("SALES-DEMO-BETA", "20", "FG-B", "CFG-DEMO-B"),
        ]
        for index, (order, item, product, config) in enumerate(sales, start=2):
            order_key = privacy.digest("sales_order", order)
            item_key = privacy.digest("sales_item", item)
            sales_item_key = privacy.digest("sales_order_item", f"{order}|{item}")
            connection.execute(
                """
                INSERT INTO sales_items VALUES
                (?, 'SRC-002', ?, ?, ?, ?, ?, '已创建', '2026-08-15')
                """,
                (
                    sales_item_key,
                    index,
                    order_key,
                    item_key,
                    material(product),
                    privacy.digest("configuration", config),
                ),
            )

        production = [
            (
                "PRODUCTION-DEMO-ALPHA",
                "FG-A",
                "SALES-DEMO-ALPHA",
                "10",
                "已释放/执行中",
            ),
            (
                "PRODUCTION-DEMO-BETA",
                "FG-B",
                "SALES-DEMO-BETA",
                "20",
                "已创建",
            ),
        ]
        for index, (order, product, sales_order, item, status) in enumerate(
            production, start=2
        ):
            connection.execute(
                """
                INSERT INTO production_orders VALUES
                (?, 'SRC-003', ?, ?, ?, ?, ?, '2026-07-01', NULL, NULL)
                """,
                (
                    privacy.digest("production_order", order),
                    index,
                    material(product),
                    privacy.digest("sales_order", sales_order),
                    privacy.digest("sales_order_item", f"{sales_order}|{item}"),
                    status,
                ),
            )

        connection.execute(
            """
            INSERT INTO change_history(
                source_id, source_row, change_key, object_key,
                status_category, changed_date
            ) VALUES ('SRC-004', 2, ?, ?, '已完成', '2026-01-10')
            """,
            (
                change("CHANGE-DEMO-ALPHA"),
                privacy.digest("change_object", "FG-A"),
            ),
        )
        connection.executemany(
            """
            INSERT INTO material_evidence(
                source_id, source_row, material_key, evidence_type,
                plant_key, status_category, procurement_type, supplier_key
            ) VALUES ('SRC-005', ?, ?, ?, ?, '可用', ?, ?)
            """,
            [
                (
                    2,
                    material("COMP-NEW"),
                    "material_master_reference",
                    None,
                    None,
                    None,
                ),
                (
                    3,
                    material("COMP-NEW"),
                    "plant_master_reference",
                    privacy.digest("plant", "P1"),
                    "F",
                    None,
                ),
                (
                    4,
                    material("COMP-NEW"),
                    "source_list_reference",
                    privacy.digest("plant", "P1"),
                    None,
                    privacy.digest("supplier", "SUPPLIER-DEMO"),
                ),
            ],
        )
        connection.execute(
            "UPDATE sources SET warnings_json=? WHERE source_id='SRC-001'",
            (json.dumps([], ensure_ascii=False),),
        )
        material_codes = {
            value
            for top, parent, component, _, _, _ in edges
            for value in (top, parent, component)
        } | {"COMP-NEW"}
        for value in sorted(material_codes):
            repository.register_business_identifier(
                connection, "material", material(value), value
            )
        for order, item, _, _ in sales:
            repository.register_business_identifier(
                connection,
                "sales_order",
                privacy.digest("sales_order", order),
                order,
            )
            repository.register_business_identifier(
                connection,
                "sales_item",
                privacy.digest("sales_item", item),
                item,
            )
        for order, _, _, _, _ in production:
            repository.register_business_identifier(
                connection,
                "production_order",
                privacy.digest("production_order", order),
                order,
            )
        repository.register_business_identifier(
            connection,
            "change",
            change("CHANGE-DEMO-ALPHA"),
            "CHANGE-DEMO-ALPHA",
        )
        repository.register_business_identifier(
            connection,
            "alternative",
            alternative("A1"),
            "A1",
        )
        repository.register_business_identifier(
            connection,
            "plant",
            privacy.digest("plant", "P1"),
            "P1",
        )
    return repository.finish_snapshot()
