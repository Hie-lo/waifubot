"""Composition root for WaifuBot application.

Wires adapters to core ports and domain services.
This is the only module in bot/ permitted to import from waifubot.adapters.
"""

from dataclasses import dataclass

from waifubot.adapters.ai_factory import create_chat_model, create_enhancer
from waifubot.adapters.clock import SystemClock
from waifubot.adapters.providers.mock import MockGenerationProvider, MockSlotPool
from waifubot.adapters.storage.memory import MemoryImageStore
from waifubot.core.ports import (
    ChatModel,
    Clock,
    Enhancer,
    GenerationProvider,
    ImageStore,
    ProviderSlotPool,
)
from waifubot.services.generation import GenerationService


@dataclass
class AppConfig:
    """Application runtime configuration."""

    bot_token: str = "mock-token"
    storage_dir: str = "data/images"
    admin_ids: tuple[int, ...] = (1001,)
    default_provider: str = "mock"


class BotContainer:
    """Dependency injection container holding initialized services and adapters."""

    def __init__(
        self,
        config: AppConfig,
        clock: Clock,
        slot_pool: ProviderSlotPool,
        provider: GenerationProvider,
        image_store: ImageStore,
        enhancer: Enhancer | None = None,
        chat_model: ChatModel | None = None,
    ) -> None:
        self.config = config
        self.clock = clock
        self.slot_pool = slot_pool
        self.provider = provider
        self.image_store = image_store
        self.enhancer = enhancer
        self.chat_model = chat_model

        # Initialize application services
        self.generation_service = GenerationService(clock=self.clock)


def create_app(
    config: AppConfig | None = None,
    clock: Clock | None = None,
    slot_pool: ProviderSlotPool | None = None,
    provider: GenerationProvider | None = None,
    image_store: ImageStore | None = None,
    enhancer: Enhancer | None = None,
    chat_model: ChatModel | None = None,
) -> BotContainer:
    """Composition root factory: builds the container with supplied or default adapters."""
    resolved_config = config or AppConfig()
    resolved_clock = clock or SystemClock()
    resolved_slot_pool = slot_pool or MockSlotPool(size=4)
    resolved_provider = provider or MockGenerationProvider(name=resolved_config.default_provider)
    resolved_store = image_store or MemoryImageStore()
    resolved_enhancer = enhancer if enhancer is not None else create_enhancer()
    resolved_chat_model = chat_model if chat_model is not None else create_chat_model()

    return BotContainer(
        config=resolved_config,
        clock=resolved_clock,
        slot_pool=resolved_slot_pool,
        provider=resolved_provider,
        image_store=resolved_store,
        enhancer=resolved_enhancer,
        chat_model=resolved_chat_model,
    )
