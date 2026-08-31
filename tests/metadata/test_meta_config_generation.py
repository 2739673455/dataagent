"""语义元数据配置生成测试"""

import unittest

import yaml

from app.metadata.config import MetaConfig
from scripts.development import generate_meta_config


class MetaConfigGenerationTest(unittest.TestCase):
    def test_generated_config_matches_checked_in_semantic_config(self) -> None:
        generated = MetaConfig.model_validate(generate_meta_config._build_config())
        checked_in = MetaConfig.model_validate(
            yaml.safe_load(
                generate_meta_config.DEFAULT_OUTPUT_PATH.read_text(encoding="utf-8")
            )
        )

        self.assertEqual(generated, checked_in)
