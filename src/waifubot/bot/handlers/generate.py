"""Special Character Studio wizard handler: click-only preset composition (R16B, Vision Lab sync() contract)."""

from pathlib import Path
from typing import Any

import yaml

from waifubot.bot.callbacks import (
    GenActionCallback,
    GenCountCallback,
    NavCallback,
    StudioCharCallback,
)
from waifubot.bot.panel import PanelEngine
from waifubot.bot.telegram_html import bold, code, italic
from waifubot.bot.wizard import WizardState, WizardStore


class SpecialCharacterStudioHandler:
    """Handles click-only Special Character Studio generation flow (R16B)."""

    def __init__(
        self,
        panel_engine: PanelEngine,
        wizard_store: WizardStore | None = None,
        config_path: Path | str = "config/vision_lab_presets.yaml",
    ) -> None:
        self.panel_engine = panel_engine
        self.wizard_store = wizard_store or WizardStore()
        self.config_path = Path(config_path)
        self._catalog: dict[str, Any] = {}
        self._load_catalog()

    def _load_catalog(self) -> None:
        if self.config_path.exists():
            content = self.config_path.read_text(encoding="utf-8")
            self._catalog = yaml.safe_load(content) or {}
        else:
            self._catalog = {"characters": [], "categories": {}}

    @property
    def characters(self) -> list[dict[str, str]]:
        return self._catalog.get("characters", [])

    @property
    def categories(self) -> dict[str, list[dict[str, str]]]:
        return self._catalog.get("categories", {})

    def get_character_by_index(self, index: int) -> dict[str, str] | None:
        chars = self.characters
        if 0 <= index < len(chars):
            return chars[index]
        return None

    def start_studio_wizard(self, user_id: int, chat_id: int) -> WizardState:
        """Initializes a fresh click-only Special Character Studio session."""
        state = WizardState(
            user_id=user_id,
            chat_id=chat_id,
            mode="special_studio",
            step="character_select",
        )
        self.wizard_store.set(state)
        return state

    def handle_typed_text(self, state: WizardState, text: str) -> tuple[bool, str]:
        """Strictly disallows manual typed prompts in Special Character Studio (R16B).

        Returns (accepted: bool, feedback_message: str).
        """
        if state.mode == "special_studio":
            return (
                False,
                "⚠️ در بخش کاراکترهای ویژه، پرامپت فقط با انتخاب دکمه‌ها ساخته می‌شود. متن تایپ‌شده پذیرفته نیست.",
            )
        return True, ""

    def select_character(self, state: WizardState, index: int) -> WizardState:
        """Selects a character by index and sets the base prompt."""
        char_data = self.get_character_by_index(index)
        if char_data:
            state.selected_character = char_data.get("name", "")
            state.character_prompt = char_data.get("prompt", "")
            state.step = "options_select"
            self.wizard_store.set(state)
        return state

    def toggle_option(self, state: WizardState, category_key: str, option_val: str) -> WizardState:
        """Sets or toggles an option for a given category (outfit, pose, location, etc.)."""
        if state.selected_options.get(category_key) == option_val:
            state.selected_options.pop(category_key, None)
        else:
            state.selected_options[category_key] = option_val
        self.wizard_store.set(state)
        return state

    def set_image_count(self, state: WizardState, count: int) -> WizardState:
        """Sets target image count (R9 supported counts: 2, 4, 6, 8, 16)."""
        if count in (2, 4, 6, 8, 16):
            state.target_count = count
            self.wizard_store.set(state)
        return state

    def render_studio_panel(
        self,
        state: WizardState,
        char_page: int = 0,
        page_size: int = 6,
    ) -> tuple[str, list[list[dict[str, str]]]]:
        """Renders the text view and inline keyboard layout for the studio."""
        prompt = state.compose_prompt()
        char_name = state.selected_character or "انتخاب‌نشده"

        lines = [
            f"🎭 {bold('استودیوی کاراکترهای ویژه (Vision Lab)')}",
            f"👤 کاراکتر: {bold(char_name)}",
            f"🖼 تعداد تصاویر: {bold(str(state.target_count))}",
            "",
            f"📝 {italic('پرامپت نهایی (ترکیب خودکار گزینه‌ها):')}",
            code(prompt if prompt else "هنوز گزینه‌ای انتخاب نشده است"),
        ]
        text = "\n".join(lines)
        keyboard: list[list[dict[str, str]]] = []

        if state.step == "character_select" or not state.selected_character:
            # Character selection pagination
            chars = self.characters
            start_idx = char_page * page_size
            end_idx = min(start_idx + page_size, len(chars))
            row: list[dict[str, str]] = []

            for i in range(start_idx, end_idx):
                name = chars[i]["name"]
                cb = StudioCharCallback(char_index=i, page=char_page).pack()
                row.append({"text": name, "callback_data": cb})
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)

            # Pagination row
            nav_row: list[dict[str, str]] = []
            if char_page > 0:
                nav_row.append(
                    {
                        "text": "⬅️ قبلی",
                        "callback_data": StudioCharCallback(
                            char_index=-1, page=char_page - 1
                        ).pack(),
                    }
                )
            if end_idx < len(chars):
                nav_row.append(
                    {
                        "text": "بعدی ➡️",
                        "callback_data": StudioCharCallback(
                            char_index=-1, page=char_page + 1
                        ).pack(),
                    }
                )
            if nav_row:
                keyboard.append(nav_row)
        else:
            # Option selection buttons
            row_cnt: list[dict[str, str]] = []
            for cnt in (2, 4, 8):
                mark = "✅ " if state.target_count == cnt else ""
                row_cnt.append(
                    {
                        "text": f"{mark}{cnt} عکس",
                        "callback_data": GenCountCallback(count=cnt).pack(),
                    }
                )
            keyboard.append(row_cnt)

            # Action buttons
            keyboard.append(
                [
                    {
                        "text": "🚀 شروع ساخت تصاویر",
                        "callback_data": GenActionCallback(action="submit").pack(),
                    },
                    {
                        "text": "🔄 تغییر کاراکتر",
                        "callback_data": GenActionCallback(action="change_char").pack(),
                    },
                ]
            )

        # Back and close row (R4)
        keyboard.append(
            [{"text": "❌ بستن پنل", "callback_data": NavCallback(action="close").pack()}]
        )

        return text, keyboard
