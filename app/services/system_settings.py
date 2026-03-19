from uuid import UUID
from fastapi import Depends
from typing import Annotated, Any
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError
from loguru import logger as LOG
import logging

from app.models.system_settings import (
    SystemSetting,
    SystemSettingCreate,
    SystemSettingUpdate,
    SystemSettingResponse,
    SystemSettingsResponse,
    SystemSettingsBulkUpdate,
    DebugConfig,
)
from app.exceptions.http import (
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
)
from app.utils.funcs import utcnow


class _SystemSettingsService:
    """Service for managing system settings."""

    # In-memory cache for frequently accessed settings
    _cache: dict[str, Any] = {}
    _cache_loaded: bool = False

    def _load_cache(self, session: Session) -> None:
        """Load all settings into cache."""
        settings = session.exec(select(SystemSetting)).all()
        for setting in settings:
            self._cache[setting.key] = setting.value
        self._cache_loaded = True
        LOG.debug(f"Loaded {len(settings)} settings into cache")

    def _invalidate_cache(self) -> None:
        """Invalidate the settings cache."""
        self._cache.clear()
        self._cache_loaded = False

    def get_setting(self, key: str, session: Session, default: Any = None) -> Any:
        """Get a single setting value by key."""
        # Try cache first
        if self._cache_loaded and key in self._cache:
            return self._cache[key]
        
        statement = select(SystemSetting).where(SystemSetting.key == key)
        setting = session.exec(statement).first()
        
        if not setting:
            return default
        
        # Update cache
        self._cache[key] = setting.value
        return setting.value

    def get_setting_full(self, key: str, session: Session) -> SystemSettingResponse:
        """Get full setting details by key."""
        statement = select(SystemSetting).where(SystemSetting.key == key)
        setting = session.exec(statement).first()
        
        if not setting:
            raise NotFoundException(f"Setting '{key}' not found")
        
        return SystemSettingResponse(**setting.model_dump())

    def get_all_settings(self, session: Session) -> list[SystemSettingResponse]:
        """Get all system settings."""
        settings = session.exec(select(SystemSetting)).all()
        return [SystemSettingResponse(**s.model_dump()) for s in settings]

    def get_settings_grouped(self, session: Session) -> SystemSettingsResponse:
        """Get all settings grouped by category."""
        settings = session.exec(select(SystemSetting)).all()
        
        grouped = SystemSettingsResponse()
        
        for setting in settings:
            category_dict = getattr(grouped, setting.category, None)
            if category_dict is not None:
                category_dict[setting.key] = setting.value
        
        return grouped

    def get_settings_by_category(self, category: str, session: Session) -> list[SystemSettingResponse]:
        """Get all settings in a specific category."""
        statement = select(SystemSetting).where(SystemSetting.category == category)
        settings = session.exec(statement).all()
        return [SystemSettingResponse(**s.model_dump()) for s in settings]

    def update_setting(self, key: str, data: SystemSettingUpdate, session: Session) -> SystemSettingResponse:
        """Update a single setting value."""
        statement = select(SystemSetting).where(SystemSetting.key == key)
        setting = session.exec(statement).first()
        
        if not setting:
            raise NotFoundException(f"Setting '{key}' not found")
        
        setting.value = data.value
        setting.updated_at = utcnow()
        
        try:
            session.add(setting)
            session.commit()
            session.refresh(setting)
            
            # Update cache
            self._cache[key] = setting.value
            
            # Apply side effects for certain settings
            self._apply_setting_side_effects(key, setting.value)
            
            LOG.info(f"Updated setting '{key}' to '{setting.value}'")
            return SystemSettingResponse(**setting.model_dump())
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Failed to update setting: {e}")

    def bulk_update_settings(self, data: SystemSettingsBulkUpdate, session: Session) -> SystemSettingsResponse:
        """Update multiple settings at once."""
        for key, value in data.settings.items():
            statement = select(SystemSetting).where(SystemSetting.key == key)
            setting = session.exec(statement).first()
            
            if setting:
                setting.value = value
                setting.updated_at = utcnow()
                session.add(setting)
                self._cache[key] = value
                self._apply_setting_side_effects(key, value)
        
        try:
            session.commit()
            LOG.info(f"Bulk updated {len(data.settings)} settings")
            return self.get_settings_grouped(session)
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Failed to bulk update settings: {e}")

    def create_setting(self, data: SystemSettingCreate, session: Session) -> SystemSettingResponse:
        """Create a new system setting."""
        setting = SystemSetting(**data.model_dump())
        
        try:
            session.add(setting)
            session.commit()
            session.refresh(setting)
            
            self._cache[setting.key] = setting.value
            LOG.info(f"Created setting '{setting.key}'")
            return SystemSettingResponse(**setting.model_dump())
        except IntegrityError:
            session.rollback()
            raise ConflictException(f"Setting '{data.key}' already exists")
        except Exception as e:
            session.rollback()
            raise InternalServerErrorException(f"Failed to create setting: {e}")

    def _apply_setting_side_effects(self, key: str, value: Any) -> None:
        """Apply side effects when certain settings change."""
        # Invalidate debug middleware cache for any debug-related setting
        debug_settings = ["debug_mode", "enable_request_logging", "enable_sql_logging", "enable_performance_headers", "log_level"]
        if key in debug_settings:
            try:
                from app.core.debug_middleware import invalidate_debug_cache
                invalidate_debug_cache()
            except ImportError:
                pass
        
        if key == "debug_mode":
            self._apply_debug_mode(value)
        elif key == "log_level":
            self._apply_log_level(value)

    def _apply_debug_mode(self, enabled: bool) -> None:
        """Apply debug mode changes."""
        if enabled:
            LOG.info("DEBUG MODE ENABLED - Verbose logging active")
            # Set log level to DEBUG when debug mode is enabled
            self._apply_log_level("DEBUG")
        else:
            LOG.info("DEBUG MODE DISABLED - Returning to normal logging")
            # Reset to INFO level
            self._apply_log_level("INFO")

    def _apply_log_level(self, level: str) -> None:
        """Dynamically change the logging level."""
        level = level.upper()
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        
        if level not in valid_levels:
            LOG.warning(f"Invalid log level '{level}', using INFO")
            level = "INFO"
        
        # Update Python's root logger
        logging.getLogger().setLevel(getattr(logging, level))
        
        # Update loguru logger
        LOG.info(f"Log level changed to {level}")

    def get_debug_config(self, session: Session) -> DebugConfig:
        """Get the current debug configuration."""
        return DebugConfig(
            debug_mode=self.get_setting("debug_mode", session, False),
            log_level=self.get_setting("log_level", session, "INFO"),
            enable_request_logging=self.get_setting("enable_request_logging", session, False),
            enable_sql_logging=self.get_setting("enable_sql_logging", session, False),
            enable_performance_headers=self.get_setting("enable_performance_headers", session, False),
        )

    def is_debug_mode(self, session: Session) -> bool:
        """Quick check if debug mode is enabled."""
        return self.get_setting("debug_mode", session, False)


# Singleton instance
_system_settings_service = _SystemSettingsService()


def get_system_settings_service() -> _SystemSettingsService:
    return _system_settings_service


SystemSettingsService = Annotated[_SystemSettingsService, Depends(get_system_settings_service)]
