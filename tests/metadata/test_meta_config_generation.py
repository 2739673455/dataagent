"""语义元数据配置生成测试。"""

import unittest

import yaml

from app.metadata.config import MetaConfig
from scripts.development import generate_meta_config


class MetaConfigGenerationTest(unittest.TestCase):
    @staticmethod
    def _generated_config() -> MetaConfig:
        return MetaConfig.model_validate(generate_meta_config._build_config())

    def test_generated_config_matches_checked_in_semantic_config(self) -> None:
        generated_data = generate_meta_config._build_config()
        generated = MetaConfig.model_validate(generated_data)
        checked_in_text = generate_meta_config.DEFAULT_OUTPUT_PATH.read_text(
            encoding="utf-8"
        )
        checked_in = MetaConfig.model_validate(yaml.safe_load(checked_in_text))

        self.assertEqual(generated, checked_in)
        self.assertEqual(
            generate_meta_config._render_config(generated_data),
            checked_in_text,
        )

    def test_generated_descriptions_and_aliases_are_normalized(self) -> None:
        config = self._generated_config()
        descriptions: list[str] = []
        for table in config.tables:
            descriptions.append(table.description)
            for column in table.columns:
                descriptions.append(column.description)
                self.assertEqual(len(column.alias), len(set(column.alias)))
                self.assertNotIn(column.name, column.alias)
                self.assertNotIn(column.description.removesuffix("。"), column.alias)
        for metric in config.metrics:
            descriptions.append(metric.description)
            self.assertEqual(len(metric.alias), len(set(metric.alias)))
            self.assertNotIn(metric.name, metric.alias)

        self.assertTrue(all(value.endswith("。") for value in descriptions))
        self.assertTrue(all(":0" not in value for value in descriptions))
        self.assertTrue(all("Type 1一致性维度" not in value for value in descriptions))

        tables = {table.name: table for table in config.tables}
        self.assertEqual(
            tables["dim_channel_info"].description,
            "渠道覆盖型一致性维度（SCD Type 1），每个渠道一行。",
        )
        date_columns = {column.name: column for column in tables["dim_date"].columns}
        self.assertEqual(
            date_columns["is_weekend"].description,
            "是否周末，0 表示否，1 表示是。",
        )

    def test_bridge_roles_and_references_express_real_relationships(self) -> None:
        config = self._generated_config()
        tables = {table.name: table for table in config.tables}
        for table_name in (
            "bridge_user_tag_relation_zip",
            "bridge_promotion_scope",
            "bridge_coupon_scope",
        ):
            self.assertEqual(tables[table_name].role, "fact")

        relation_columns = {
            column.name: column
            for column in tables["bridge_user_tag_relation_zip"].columns
        }
        self.assertEqual(
            (
                relation_columns["user_tag_sk"].reference_t_name,
                relation_columns["user_tag_sk"].reference_c_name,
            ),
            ("dim_user_tag_info", "user_tag_sk"),
        )

        column_keys = {
            (table.name, column.name)
            for table in config.tables
            for column in table.columns
        }
        for table in config.tables:
            for column in table.columns:
                if column.reference_t_name is not None:
                    self.assertIn(
                        (column.reference_t_name, column.reference_c_name),
                        column_keys,
                    )

    def test_high_cardinality_search_terms_are_not_globally_value_indexed(
        self,
    ) -> None:
        config = self._generated_config()
        indexed = {
            (table.name, column.name)
            for table in config.tables
            for column in table.columns
            if column.index_values
        }

        self.assertNotIn(("dwd_traffic_search_di", "search_keyword"), indexed)
        self.assertNotIn(("dwd_traffic_search_di", "normalized_keyword"), indexed)
        self.assertIn(
            ("dwd_trade_order_status_event_di", "after_order_status"),
            indexed,
        )

    def test_metrics_include_executable_time_status_and_join_context(self) -> None:
        config = self._generated_config()
        metrics = {metric.name: metric for metric in config.metrics}
        self.assertNotIn("平均成交单价", metrics)
        self.assertIn("平均成交单价", metrics["件单价"].alias)

        effective_gmv_columns = {
            (reference.t_name, reference.c_name)
            for reference in metrics["有效GMV"].relevant_columns
        }
        self.assertTrue(
            {
                ("dwd_trade_order_detail_di", "biz_date"),
                ("dwd_trade_order_status_event_di", "order_id"),
                ("dwd_trade_order_status_event_di", "event_seq_no"),
                ("dwd_trade_order_status_event_di", "event_time"),
            }
            <= effective_gmv_columns
        )

        review_rate_columns = {
            (reference.t_name, reference.c_name)
            for reference in metrics["评价率"].relevant_columns
        }
        self.assertTrue(
            {
                ("dwd_trade_delivery_item_di", "delivery_id"),
                ("dwd_trade_delivery_status_event_di", "delivery_id"),
                ("dwd_trade_delivery_status_event_di", "event_seq_no"),
                ("dwd_trade_delivery_status_event_di", "event_time"),
            }
            <= review_rate_columns
        )
        self.assertIn(
            "按 sku_id 汇总所有仓库 available_qty",
            metrics["零库存SKU数"].description,
        )
