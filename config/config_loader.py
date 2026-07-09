import json
import os


class MK1Config:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.data = self._load_json(config_path)
        self.personality = self._load_personality()

    # ------------------------------------------------------------
    # Load mk1_config.json
    # ------------------------------------------------------------
    def _load_json(self, path: str):
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ------------------------------------------------------------
    # Load personality.txt
    # ------------------------------------------------------------
    def _load_personality(self):
        p_path = self.data.get("personality", {}).get("path")
        if not p_path or not os.path.isfile(p_path):
            return ""

        with open(p_path, "r", encoding="utf-8") as f:
            return f.read().strip()

    # ------------------------------------------------------------
    # Access helpers
    # ------------------------------------------------------------
    def get(self, section: str, key: str, default=None):
        return self.data.get(section, {}).get(key, default)


# ------------------------------------------------------------
# Public loader function
# ------------------------------------------------------------
def load_config(config_path: str = "config/mk1_config.json") -> MK1Config:
    return MK1Config(config_path)
