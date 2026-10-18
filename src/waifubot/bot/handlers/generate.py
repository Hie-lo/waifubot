"""Special Character Studio wizard handler: click-only preset composition (R16B, Vision Lab sync() contract)."""

from pathlib import Path
from typing import Any

import yaml

from waifubot.bot.callbacks import (
    GenActionCallback,
    GenCountCallback,
    NavCallback,
    StudioCharCallback,
    StudioOptCallback,
)
from waifubot.bot.panel import PanelEngine
from waifubot.bot.telegram_html import bold, code, italic
from waifubot.bot.wizard import WizardState, WizardStore

CATEGORY_LABELS: dict[str, str] = {
    "Outfit": "👗 لباس (Outfit)",
    "Expression": "😊 چهره (Expression)",
    "Pose": "💃 ژست (Pose)",
    "Art Style": "🎨 سبک (Style)",
    "Location": "🏰 مکان (Location)",
    "Hairstyle": "💇 مدل مو (Hair)",
    "Atmosphere": "🌫️ فضا (Atmosphere)",
    "Camera": "📷 دوربین (Camera)",
    "Bangs": "💇‍♀️ چتری (Bangs)",
    "Fantasy Race": "🧝 نژاد (Race)",
    "background": "🖼️ پس‌زمینه (Background)",
}


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
        clean = text.strip().lower()
        if clean in ("/cancel", "لغو", "انصراف"):
            return True, ""

        if state.mode == "special_studio":
            return (
                False,
                "⚠️ در بخش کاراکترهای ویژه، پرامپت فقط با انتخاب دکمه‌ها ساخته می‌شود.\nبرای لغو دستور /cancel را بفرستید.",
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
        category_key: str = "",
        opt_page: int = 0,
        opt_page_size: int = 6,
    ) -> tuple[str, list[list[dict[str, str]]]]:
        """Renders the text view and inline keyboard layout for the studio."""
        prompt = state.compose_prompt()
        char_name = state.selected_character or "انتخاب‌نشده"

        # 1. Sub-screen: browsing options for a specific category
        if category_key and category_key in self.categories:
            cat_items = self.categories.get(category_key, [])
            cat_label = CATEGORY_LABELS.get(category_key, category_key)
            start_idx = opt_page * opt_page_size
            end_idx = min(start_idx + opt_page_size, len(cat_items))
            selected_val = state.selected_options.get(category_key, "")

            lines = [
                f"🎭 {bold('استودیوی کاراکترهای ویژه (Vision Lab)')}",
                f"👤 کاراکتر: {bold(char_name)}",
                f"📂 دسته‌بندی: {bold(cat_label)}",
                "",
                "روی گزینه مورد نظر کلیک کنید (کلیک دوباره = لغو انتخاب):",
            ]
            text = "\n".join(lines)
            keyboard: list[list[dict[str, str]]] = []

            row: list[dict[str, str]] = []
            for i in range(start_idx, end_idx):
                item = cat_items[i]
                opt_name = item.get("name", f"Option {i}")
                opt_prompt = item.get("prompt", opt_name)
                is_active = selected_val in (opt_prompt, opt_name)
                mark = "✅ " if is_active else ""
                cb = StudioOptCallback(
                    category_key=category_key, item_index=i, page=opt_page
                ).pack()
                row.append({"text": f"{mark}{opt_name}", "callback_data": cb})
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)

            # Pagination row for category options
            nav_row: list[dict[str, str]] = []
            if opt_page > 0:
                nav_row.append(
                    {
                        "text": "⬅️ قبلی",
                        "callback_data": StudioOptCallback(
                            category_key=category_key, item_index=-1, page=opt_page - 1
                        ).pack(),
                    }
                )
            if end_idx < len(cat_items):
                nav_row.append(
                    {
                        "text": "بعدی ➡️",
                        "callback_data": StudioOptCallback(
                            category_key=category_key, item_index=-1, page=opt_page + 1
                        ).pack(),
                    }
                )
            if nav_row:
                keyboard.append(nav_row)

            # Back button to categories menu
            keyboard.append(
                [
                    {
                        "text": "🔙 بازگشت به دسته‌بندی‌ها",
                        "callback_data": StudioOptCallback(category_key="", item_index=-2).pack(),
                    }
                ]
            )
            return text, keyboard

        # 2. Main screen: character select OR categories selection
        if state.step == "character_select" or not state.selected_character:
            chars = self.characters
            total_pages = max(1, (len(chars) + page_size - 1) // page_size)
            start_idx = char_page * page_size
            end_idx = min(start_idx + page_size, len(chars))

            lines = [
                f"🎭 {bold('استودیوی کاراکترهای ویژه (Vision Lab)')}",
                f"لطفاً یک کاراکتر را برای شروع انتخاب کنید (صفحه {char_page + 1} از {total_pages}):",
            ]
            text = "\n".join(lines)
            keyboard = []

            row = []
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
            nav_row = []
            if char_page > 0:
                nav_row.append(
                    {
                        "text": "⬅️ قبلی",
                        "callback_data": StudioCharCallback(
                            char_index=-1, page=char_page - 1
                        ).pack(),
                    }
                )
            nav_row.append(
                {
                    "text": f"📄 {char_page + 1}/{total_pages}",
                    "callback_data": StudioCharCallback(char_index=-1, page=char_page).pack(),
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

            keyboard.append(
                [{"text": "❌ بستن پنل", "callback_data": NavCallback(action="close").pack()}]
            )
            return text, keyboard

        # 3. Main studio screen with selected character and categories
        selected_summary: list[str] = []
        for cat_k, cat_v in state.selected_options.items():
            lbl = CATEGORY_LABELS.get(cat_k, cat_k)
            selected_summary.append(f"• {lbl}: {code(cat_v[:35])}")

        lines = [
            f"🎭 {bold('استودیوی کاراکترهای ویژه (Vision Lab)')}",
            f"👤 کاراکتر: {bold(char_name)}",
            f"🖼 تعداد تصاویر: {bold(str(state.target_count))}",
        ]
        if selected_summary:
            lines.append("")
            lines.append(bold("گزینه‌های فعال انتخاب‌شده:"))
            lines.extend(selected_summary)

        lines.extend(
            [
                "",
                f"📝 {italic('پرامپت نهایی (ترکیب خودکار گزینه‌ها):')}",
                code(prompt if prompt else "هنوز گزینه‌ای انتخاب نشده است"),
                "",
                "برای افزودن جزئیات، دسته‌بندی‌های زیر را لمس کنید:",
            ]
        )
        text = "\n".join(lines)
        keyboard = []

        # Category buttons (2 per row)
        cat_keys = list(self.categories.keys())
        cat_row: list[dict[str, str]] = []
        for cat_k in cat_keys:
            lbl = CATEGORY_LABELS.get(cat_k, cat_k)
            mark = " ✅" if cat_k in state.selected_options else ""
            cb = StudioOptCallback(category_key=cat_k, item_index=-1, page=0).pack()
            cat_row.append({"text": f"{lbl}{mark}", "callback_data": cb})
            if len(cat_row) == 2:
                keyboard.append(cat_row)
                cat_row = []
        if cat_row:
            keyboard.append(cat_row)

        # Image count selection buttons
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
        keyboard.append(
            [{"text": "❌ بستن پنل", "callback_data": NavCallback(action="close").pack()}]
        )

        return text, keyboard

    def render_mode_selection_panel(self) -> tuple[str, list[list[dict[str, str]]]]:
        """Renders the generator mode selection panel (Vision Lab vs Text-to-Image)."""
        text = (
            f"🎨 {bold('انتخاب حالت تولید تصویر')}\n\n"
            "لطفاً روش تولید تصویر مورد نظر خود را انتخاب کنید:\n\n"
            "1️⃣ <b>استودیوی کاراکترهای ویژه (Vision Lab)</b>:\n"
            "ساخت تصویر از کاراکترهای ویژه با انتخاب گزینه‌های چهره، لباس، ژست و سبک به صورت دکمه‌ای.\n\n"
            "2️⃣ <b>تبدیل متن به تصویر (Text to Image)</b>:\n"
            "نوشتن پرامپت یا متن دلخواه برای تولید هر تصویر به صورت آزاد."
        )
        keyboard = [
            [
                {
                    "text": "🎭 استودیوی کاراکترهای ویژه (Vision Lab)",
                    "callback_data": GenActionCallback(action="mode_vl").pack(),
                }
            ],
            [
                {
                    "text": "✍️ تبدیل متن به تصویر (Text to Image)",
                    "callback_data": GenActionCallback(action="mode_txt").pack(),
                }
            ],
            [
                {
                    "text": "❌ انصراف",
                    "callback_data": NavCallback(action="close").pack(),
                }
            ],
        ]
        return text, keyboard

    def render_text_to_image_panel(
        self, state: WizardState
    ) -> tuple[str, list[list[dict[str, str]]]]:
        """Renders the Text-to-Image prompt review and configuration screen."""
        if not state.custom_prompt:
            text = (
                f"✍️ {bold('تبدیل متن به تصویر (Text to Image)')}\n\n"
                "لطفاً متن یا پرامپت توصیفی تصویر مورد نظرتان را تایپ و ارسال کنید:\n"
                f"<i>(می‌توانید فارسی یا انگلیسی بنویسید)</i>\n\n"
                "برای لغو می‌توانید دستور /cancel را ارسال کنید."
            )
            keyboard = [
                [
                    {
                        "text": "🔙 بازگشت به انتخاب حالت",
                        "callback_data": GenActionCallback(action="mode_picker").pack(),
                    },
                    {
                        "text": "❌ انصراف",
                        "callback_data": NavCallback(action="close").pack(),
                    },
                ]
            ]
            return text, keyboard

        lines = [
            f"✍️ {bold('تبدیل متن به تصویر (Text to Image)')}",
            "",
            f"📝 {bold('پرامپت وارد شده:')}",
            code(state.custom_prompt),
            "",
            f"🖼 تعداد تصاویر: {bold(str(state.target_count))}",
            "",
            "تعداد تصویر مورد نظر را مشخص کرده و دکمه شروع را لمس کنید:",
        ]
        text = "\n".join(lines)
        keyboard: list[list[dict[str, str]]] = []

        # Image count selection buttons
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
                    "text": "✏️ ویرایش پرامپت",
                    "callback_data": GenActionCallback(action="edit_prompt").pack(),
                },
            ]
        )
        keyboard.append(
            [{"text": "❌ بستن پنل", "callback_data": NavCallback(action="close").pack()}]
        )

        return text, keyboard
