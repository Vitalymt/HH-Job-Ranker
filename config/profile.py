"""
Отредактируйте профиль под себя — или сделайте это прямо в UI (⚙️ Настройки).

Этот файл используется только как источник значений по умолчанию при первом запуске.
Как только вы сохраните профиль через UI, изменения здесь не будут иметь эффекта.
"""

from config.defaults import DEFAULT_CANDIDATE_PROFILE as CANDIDATE_PROFILE
from config.defaults import DEFAULT_SEED_QUERIES as SEED_QUERIES

__all__ = ["CANDIDATE_PROFILE", "SEED_QUERIES"]
