"""Template gallery — loads and indexes YAML extraction templates."""

import threading
import yaml
import logging
from pathlib import Path
from typing import Dict, List, Optional

from app.models.template import TemplateConfig

logger = logging.getLogger(__name__)

_PRESETS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "templates" / "presets"

_lock = threading.Lock()


class TemplateGallery:
    """Singleton that loads all .yaml templates from templates/presets/."""

    _instance: Optional["TemplateGallery"] = None

    def __init__(self, presets_dir: Optional[str] = None):
        self.presets_dir = Path(presets_dir) if presets_dir else _PRESETS_DIR
        self._templates: Dict[str, TemplateConfig] = {}
        self._load_all()

    @classmethod
    def get_instance(cls) -> "TemplateGallery":
        if cls._instance is None:
            with _lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def _load_all(self) -> None:
        if not self.presets_dir.exists():
            logger.warning("Template presets directory not found: %s", self.presets_dir)
            return

        for yaml_file in sorted(self.presets_dir.rglob("*.yaml")):
            try:
                config = self._load_file(yaml_file)
                key = f"{config.domain}/{config.name}"
                self._templates[key] = config
                logger.debug("Loaded template: %s", key)
            except Exception as e:
                logger.error("Failed to load template %s: %s", yaml_file, e)

    def _load_file(self, path: Path) -> TemplateConfig:
        with open(path) as f:
            raw = yaml.safe_load(f)
        if raw is None:
            raise ValueError(f"Empty YAML file: {path}")
        return TemplateConfig(**raw)

    def get(self, path: str) -> Optional[TemplateConfig]:
        if "/" not in path:
            path = f"general/{path}"
        return self._templates.get(path)

    def list(
        self,
        domain: Optional[str] = None,
        type_filter: Optional[str] = None,
    ) -> List[TemplateConfig]:
        results = list(self._templates.values())
        if domain:
            results = [t for t in results if t.domain == domain]
        if type_filter:
            results = [t for t in results if t.type.value == type_filter]
        return results