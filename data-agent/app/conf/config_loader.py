from pathlib import Path
from typing import Type, TypeVar

from omegaconf import OmegaConf


def load_config[T](config_file: Path, schema_cls: Type[T]) -> T:
    context = OmegaConf.load(config_file)
    schema = OmegaConf.structured(schema_cls)
    config: T = OmegaConf.to_object(OmegaConf.merge(schema, context))
    return config
