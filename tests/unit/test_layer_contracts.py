"""Unit tests enforcing architecture layering rules via AST inspection."""

import ast
import unittest
from pathlib import Path


def get_all_imports(file_path: Path) -> list[str]:
    """Parse a python file into an AST and return all imported module paths."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    imported_modules: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)

    return imported_modules


class TestLayerContracts(unittest.TestCase):
    """Enforces strict hexagonal/onion layering contracts per ARCHITECTURE §3."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.src_root = Path(__file__).resolve().parents[2] / "src" / "waifubot"
        cls.assertTrue(cls.src_root.exists(), f"Source root {cls.src_root} must exist")

    def test_core_isolation(self) -> None:
        """core must not import adapters, db, services, bot, or I/O frameworks."""
        forbidden_prefixes = (
            "waifubot.adapters",
            "waifubot.db",
            "waifubot.services",
            "waifubot.bot",
            "telegram",
            "sqlalchemy",
            "playwright",
            "httpx",
            "anthropic",
        )
        core_files = (self.src_root / "core").glob("**/*.py")
        for file in core_files:
            imports = get_all_imports(file)
            for imp in imports:
                for forbidden in forbidden_prefixes:
                    self.assertFalse(
                        imp == forbidden or imp.startswith(f"{forbidden}."),
                        f"Layer violation in {file.name}: imported {imp} which violates core isolation rule",
                    )

    def test_services_isolation(self) -> None:
        """services must not import bot or telegram."""
        forbidden_prefixes = (
            "waifubot.bot",
            "telegram",
        )
        service_files = (self.src_root / "services").glob("**/*.py")
        for file in service_files:
            imports = get_all_imports(file)
            for imp in imports:
                for forbidden in forbidden_prefixes:
                    self.assertFalse(
                        imp == forbidden or imp.startswith(f"{forbidden}."),
                        f"Layer violation in {file.name}: imported {imp} which violates services isolation rule",
                    )

    def test_adapters_isolation(self) -> None:
        """adapters must not import bot or services."""
        forbidden_prefixes = (
            "waifubot.bot",
            "waifubot.services",
        )
        adapter_files = (self.src_root / "adapters").glob("**/*.py")
        for file in adapter_files:
            imports = get_all_imports(file)
            for imp in imports:
                for forbidden in forbidden_prefixes:
                    self.assertFalse(
                        imp == forbidden or imp.startswith(f"{forbidden}."),
                        f"Layer violation in {file.name}: imported {imp} which violates adapters isolation rule",
                    )

    def test_db_isolation(self) -> None:
        """db must not import bot or services."""
        forbidden_prefixes = (
            "waifubot.bot",
            "waifubot.services",
        )
        db_files = (self.src_root / "db").glob("**/*.py")
        for file in db_files:
            imports = get_all_imports(file)
            for imp in imports:
                for forbidden in forbidden_prefixes:
                    self.assertFalse(
                        imp == forbidden or imp.startswith(f"{forbidden}."),
                        f"Layer violation in {file.name}: imported {imp} which violates db isolation rule",
                    )

    def test_bot_adapters_isolation_except_composition_root(self) -> None:
        """bot modules (except bot/app.py) must not import adapters directly."""
        bot_files = (self.src_root / "bot").glob("**/*.py")
        for file in bot_files:
            if file.name == "app.py":
                continue  # app.py is the composition root
            imports = get_all_imports(file)
            for imp in imports:
                self.assertFalse(
                    imp == "waifubot.adapters" or imp.startswith("waifubot.adapters."),
                    f"Layer violation in {file.name}: bot module imported adapter {imp} directly instead of using composition root",
                )

    def test_stealth_naming_removed(self) -> None:
        """'stealth' header naming must be completely renamed to standard browser headers."""
        http_file = self.src_root / "adapters" / "http.py"
        content = http_file.read_text(encoding="utf-8").lower()
        self.assertNotIn("stealth", content, "Found 'stealth' in adapters/http.py")


if __name__ == "__main__":
    unittest.main()
