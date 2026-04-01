import json

from leopard_gecko.models.config import AppConfig
from leopard_gecko.store.paths import DataPaths, ensure_data_dir


class ConfigRepository:
    def __init__(self, paths: DataPaths) -> None:
        self._paths = paths

    def load(self) -> AppConfig:
        if not self._paths.config_path.exists():
            return AppConfig.default(data_dir=str(self._paths.root_dir))
        raw = json.loads(self._paths.config_path.read_text(encoding="utf-8"))
        return AppConfig.model_validate(raw)

    def save(self, config: AppConfig) -> None:
        ensure_data_dir(self._paths)
        payload = config.model_dump(mode="json")
        self._atomic_write(self._paths.config_path, payload)

    def initialize(self) -> AppConfig:
        config = self.load()
        if config.data_dir is None:
            config = config.model_copy(update={"data_dir": str(self._paths.root_dir)})
        self.save(config)
        return config

    @staticmethod
    def _atomic_write(path, payload: dict) -> None:
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)

