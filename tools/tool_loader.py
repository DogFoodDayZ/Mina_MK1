import os
import importlib.util
import traceback
from typing import Dict, Any, Callable


class ToolLoader:
    """
    MK1 Tool Loader
    - Auto-detects tools in the /tools directory
    - Loads only files containing a callable tool_entry(args)
    - Provides run(name, args) for CORE
    - Tracks tool status and errors
    """

    def __init__(self, tools_dir: str):
        self.tools_dir = tools_dir
        self.tools: Dict[str, Callable] = {}
        self.status: Dict[str, Any] = {}
        self._load_tools()

    # ------------------------------------------------------------
    # INTERNAL: LOAD ALL TOOLS
    # ------------------------------------------------------------
    def _load_tools(self):
        if not os.path.isdir(self.tools_dir):
            raise RuntimeError(f"Tool directory not found: {self.tools_dir}")

        for filename in os.listdir(self.tools_dir):
            # Skip non-Python files and loader itself
            if not filename.endswith(".py"):
                continue
            if filename in ("tool_loader.py", "__init__.py"):
                continue

            tool_path = os.path.join(self.tools_dir, filename)
            tool_name = os.path.splitext(filename)[0]

            try:
                spec = importlib.util.spec_from_file_location(tool_name, tool_path)
                if spec is None or spec.loader is None:
                    continue

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Validate tool_entry
                if not hasattr(module, "tool_entry"):
                    self.status[tool_name] = "invalid: missing tool_entry"
                    continue

                entry = getattr(module, "tool_entry")
                if not callable(entry):
                    self.status[tool_name] = "invalid: tool_entry not callable"
                    continue

                # Register tool
                self.tools[tool_name] = entry
                self.status[tool_name] = "ok"

            except Exception as e:
                self.status[tool_name] = f"load_error: {type(e).__name__}: {e}"

    # ------------------------------------------------------------
    # PUBLIC: RUN TOOL
    # ------------------------------------------------------------
    def run(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a tool by name.
        Returns structured result:
        {
            "ok": bool,
            "result": ...,
            "error": ...
        }
        """
        if name not in self.tools:
            return {
                "ok": False,
                "result": None,
                "error": f"tool_not_found: {name}"
            }

        try:
            result = self.tools[name](args)
            self.status[name] = "ok"
            return result

        except Exception as e:
            tb = traceback.format_exc()
            self.status[name] = f"runtime_error: {type(e).__name__}"
            return {
                "ok": False,
                "result": None,
                "error": f"exception: {type(e).__name__}: {e}",
                "trace": tb
            }

    # ------------------------------------------------------------
    # PUBLIC: GET STATUS
    # ------------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        """
        Returns tool health map:
        {
            "ps_run": "ok",
            "file_read": "ok",
            "file_write": "invalid: missing tool_entry",
            ...
        }
        """
        return dict(self.status)

    # ------------------------------------------------------------
    # PUBLIC: GET TOOL SCHEMAS FOR MODEL
    # ------------------------------------------------------------
    def get_tool_schemas(self) -> list:
        """
        Returns OpenAI-compatible tool schemas for function calling.
        Each tool with a .schema attribute is included.
        """
        schemas = []
        for tool_name, tool_func in self.tools.items():
            if hasattr(tool_func, "schema") and tool_func.schema:
                schema = tool_func.schema
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": schema.get("description", ""),
                        "parameters": schema.get("parameters", {
                            "type": "object",
                            "properties": {},
                        }),
                    }
                })
        return schemas
