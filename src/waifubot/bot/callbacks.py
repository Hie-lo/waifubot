"""Typed CallbackData registry ensuring <= 64 bytes limit and type-safe routing (R8, R41)."""

from abc import ABC, abstractmethod
from typing import ClassVar


class CallbackData(ABC):
    """Base typed callback data with compact versioned prefix."""

    prefix: ClassVar[str] = ""
    version: ClassVar[str] = "v1"

    @abstractmethod
    def pack(self) -> str:
        """Serializes callback data into a compact string <= 64 bytes."""
        ...

    @classmethod
    @abstractmethod
    def unpack(cls, payload: str) -> "CallbackData":
        """Unpacks payload back into typed CallbackData."""
        ...


class CallbackRegistry:
    """Registry managing typed callbacks with automated payload size verification (R8)."""

    def __init__(self) -> None:
        self._registry: dict[str, type[CallbackData]] = {}

    def register(self, callback_cls: type[CallbackData]) -> type[CallbackData]:
        prefix = callback_cls.prefix
        if not prefix:
            raise ValueError(f"Callback class {callback_cls.__name__} has empty prefix")
        if prefix in self._registry:
            raise ValueError(f"Duplicate callback prefix '{prefix}' already registered")
        self._registry[prefix] = callback_cls
        return callback_cls

    def parse(self, raw_data: str) -> CallbackData:
        """Parses raw callback_data string into registered typed CallbackData object."""
        parts = raw_data.split(":", 2)
        if len(parts) < 2:
            raise ValueError(f"Malformed callback data: '{raw_data}'")

        ver, prefix = parts[0], parts[1]
        payload = parts[2] if len(parts) > 2 else ""

        if prefix not in self._registry:
            raise KeyError(f"Unregistered callback prefix '{prefix}' in '{raw_data}'")

        cls = self._registry[prefix]
        if cls.version != ver:
            raise ValueError(f"Unsupported callback version '{ver}' for prefix '{prefix}'")

        return cls.unpack(payload)

    def registered_classes(self) -> list[type[CallbackData]]:
        return list(self._registry.values())


registry = CallbackRegistry()


@registry.register
class NavCallback(CallbackData):
    """Navigation callback (back, close, menu switches)."""

    prefix = "nav"

    def __init__(self, action: str, target: str = "", panel_version: int = 1) -> None:
        self.action = action
        self.target = target
        self.panel_version = panel_version

    def pack(self) -> str:
        data = f"{self.version}:{self.prefix}:{self.action}:{self.target}:{self.panel_version}"
        if len(data.encode("utf-8")) > 64:
            raise ValueError(f"Callback data exceeds 64 bytes: '{data}'")
        return data

    @classmethod
    def unpack(cls, payload: str) -> "NavCallback":
        parts = payload.split(":")
        action = parts[0] if len(parts) > 0 else ""
        target = parts[1] if len(parts) > 1 else ""
        ver = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        return cls(action=action, target=target, panel_version=ver)


@registry.register
class StudioCharCallback(CallbackData):
    """Special character selection callback by catalog index and page (R16B)."""

    prefix = "sc"

    def __init__(self, char_index: int, page: int = 0) -> None:
        self.char_index = char_index
        self.page = page

    def pack(self) -> str:
        data = f"{self.version}:{self.prefix}:{self.char_index}:{self.page}"
        if len(data.encode("utf-8")) > 64:
            raise ValueError(f"Callback data exceeds 64 bytes: '{data}'")
        return data

    @classmethod
    def unpack(cls, payload: str) -> "StudioCharCallback":
        parts = payload.split(":")
        idx = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
        pg = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return cls(char_index=idx, page=pg)


@registry.register
class StudioOptCallback(CallbackData):
    """Special character option category and item index selection (R16B)."""

    prefix = "so"

    def __init__(self, category_key: str, item_index: int) -> None:
        self.category_key = category_key
        self.item_index = item_index

    def pack(self) -> str:
        data = f"{self.version}:{self.prefix}:{self.category_key}:{self.item_index}"
        if len(data.encode("utf-8")) > 64:
            raise ValueError(f"Callback data exceeds 64 bytes: '{data}'")
        return data

    @classmethod
    def unpack(cls, payload: str) -> "StudioOptCallback":
        parts = payload.split(":")
        cat = parts[0] if len(parts) > 0 else ""
        idx = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return cls(category_key=cat, item_index=idx)


@registry.register
class GenCountCallback(CallbackData):
    """Image count selection callback (2, 4, 6, 8, 16 per R9)."""

    prefix = "cnt"

    def __init__(self, count: int) -> None:
        self.count = count

    def pack(self) -> str:
        data = f"{self.version}:{self.prefix}:{self.count}"
        if len(data.encode("utf-8")) > 64:
            raise ValueError(f"Callback data exceeds 64 bytes: '{data}'")
        return data

    @classmethod
    def unpack(cls, payload: str) -> "GenCountCallback":
        count = int(payload) if payload.isdigit() else 4
        return cls(count=count)


@registry.register
class GenActionCallback(CallbackData):
    """Wizard action trigger callback (submit, reset, enhance, nsfw_toggle)."""

    prefix = "act"

    def __init__(self, action: str, nonce: str = "") -> None:
        self.action = action
        self.nonce = nonce

    def pack(self) -> str:
        data = f"{self.version}:{self.prefix}:{self.action}:{self.nonce}"
        if len(data.encode("utf-8")) > 64:
            raise ValueError(f"Callback data exceeds 64 bytes: '{data}'")
        return data

    @classmethod
    def unpack(cls, payload: str) -> "GenActionCallback":
        parts = payload.split(":")
        action = parts[0] if len(parts) > 0 else ""
        nonce = parts[1] if len(parts) > 1 else ""
        return cls(action=action, nonce=nonce)


@registry.register
class ViewerCallback(CallbackData):
    """Review pool/inbox gallery navigation callback (R5)."""

    prefix = "vw"

    def __init__(self, action: str, page: int, image_id: str = "") -> None:
        self.action = action
        self.page = page
        self.image_id = image_id

    def pack(self) -> str:
        data = f"{self.version}:{self.prefix}:{self.action}:{self.page}:{self.image_id}"
        if len(data.encode("utf-8")) > 64:
            raise ValueError(f"Callback data exceeds 64 bytes: '{data}'")
        return data

    @classmethod
    def unpack(cls, payload: str) -> "ViewerCallback":
        parts = payload.split(":")
        action = parts[0] if len(parts) > 0 else ""
        pg = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        img = parts[2] if len(parts) > 2 else ""
        return cls(action=action, page=pg, image_id=img)


@registry.register
class SettingCallback(CallbackData):
    """Settings screen adjust callback (R25)."""

    prefix = "set"

    def __init__(self, key: str, action: str, val: str = "") -> None:
        self.key = key
        self.action = action
        self.val = val

    def pack(self) -> str:
        data = f"{self.version}:{self.prefix}:{self.key}:{self.action}:{self.val}"
        if len(data.encode("utf-8")) > 64:
            raise ValueError(f"Callback data exceeds 64 bytes: '{data}'")
        return data

    @classmethod
    def unpack(cls, payload: str) -> "SettingCallback":
        parts = payload.split(":")
        key = parts[0] if len(parts) > 0 else ""
        action = parts[1] if len(parts) > 1 else ""
        val = parts[2] if len(parts) > 2 else ""
        return cls(key=key, action=action, val=val)
