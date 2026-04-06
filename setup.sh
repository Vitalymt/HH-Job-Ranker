#!/bin/bash

# =============================================================
# HH Job Ranker — Full Bootstrap Setup
#
# Три способа запуска на чистой VM:
#
# 1) Через curl (одна строка):
#    bash <(curl -fsSL https://RAW_URL/setup.sh)
#
# 2) Клонировать и запустить:
#    git clone REPO_URL ~/hh-job-ranker
#    bash ~/hh-job-ranker/setup.sh
#
# 3) Скопировать только setup.sh и запустить —
#    скрипт сам склонирует репозиторий.
# =============================================================

set -e

# ── Настройка репозитория ─────────────────────────────────────
REPO_URL="https://github.com/vitalymt/hh-job-ranker"
REPO_BRANCH="claude/hh-job-ranker-app-AmK1X"
INSTALL_DIR="${HOME}/hh-job-ranker"
# ─────────────────────────────────────────────────────────────

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}"
echo "╔══════════════════════════════════════╗"
echo "║       HH Job Ranker — Setup          ║"
echo "╚══════════════════════════════════════╝"
echo -e "${NC}"

# ═══════════════════════════════════════════════════════════════
# ШАГ 0: Клонирование репозитория (если запущен вне папки проекта)
# ═══════════════════════════════════════════════════════════════
if [ ! -f "$(dirname "$0")/main.py" ] && [ ! -f "./main.py" ]; then
    echo -e "${YELLOW}=== Клонирование репозитория ===${NC}"
    echo ""

    # Установить git если нет
    if ! command -v git &> /dev/null; then
        echo -e "${YELLOW}git не найден. Устанавливаю...${NC}"
        if command -v apt-get &> /dev/null; then
            sudo apt-get update -qq && sudo apt-get install -y -qq git
        elif command -v yum &> /dev/null; then
            sudo yum install -y -q git
        else
            echo -e "${RED}Не могу установить git. Установите вручную и повторите.${NC}"
            exit 1
        fi
        echo -e "${GREEN}✓ git установлен${NC}"
    else
        echo -e "${GREEN}✓ git найден: $(git --version)${NC}"
    fi

    # Клонировать
    if [ -d "$INSTALL_DIR" ]; then
        echo -e "${YELLOW}Папка ${INSTALL_DIR} уже существует. Обновляю...${NC}"
        git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || true
    else
        echo -e "${YELLOW}Клонирую в ${INSTALL_DIR}...${NC}"
        git clone --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
        echo -e "${GREEN}✓ Репозиторий склонирован${NC}"
    fi

    echo ""
    echo -e "${YELLOW}Перехожу в папку проекта и продолжаю установку...${NC}"
    echo ""
    # Передать управление setup.sh внутри репозитория
    exec bash "${INSTALL_DIR}/setup.sh"
    exit 0
fi

# Определить рабочую директорию проекта
if [ -f "./main.py" ]; then
    PROJECT_DIR="$(pwd)"
else
    PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
    cd "$PROJECT_DIR"
fi

echo -e "${BLUE}Рабочая папка: ${PROJECT_DIR}${NC}"
echo ""

# ═══════════════════════════════════════════════════════════════
# ШАГ 1: Проверка и установка Docker
# ═══════════════════════════════════════════════════════════════
echo -e "${YELLOW}=== Проверка Docker ===${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}Docker не найден. Устанавливаю...${NC}"
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    echo -e "${GREEN}✓ Docker установлен${NC}"
    echo -e "${YELLOW}⚠ Чтобы Docker работал без sudo, перелогиньтесь или выполни:${NC}"
    echo "   newgrp docker"
    # Продолжаем через sudo
    DOCKER="sudo docker"
else
    echo -e "${GREEN}✓ Docker найден: $(docker --version)${NC}"
    DOCKER="docker"
fi

# ═══════════════════════════════════════════════════════════════
# ШАГ 2: Ввод API ключей
# ═══════════════════════════════════════════════════════════════
echo ""
echo -e "${YELLOW}=== Настройка AI провайдера ===${NC}"
echo ""

# Если уже есть .env — предложить переиспользовать
if [ -f ".env" ]; then
    echo -e "${YELLOW}Найден существующий .env файл.${NC}"
    read -p "Использовать существующие настройки? (Enter = да, n = перенастроить): " REUSE_ENV
    if [ -z "$REUSE_ENV" ] || [ "$REUSE_ENV" = "y" ] || [ "$REUSE_ENV" = "Y" ]; then
        echo -e "${GREEN}✓ Используем существующий .env${NC}"
        SKIP_ENV=true
    fi
fi

if [ -z "$SKIP_ENV" ]; then

    read -p "OpenRouter API Key (sk-or-...): " OPENROUTER_API_KEY
    if [ -z "$OPENROUTER_API_KEY" ]; then
        echo -e "${RED}Ошибка: OpenRouter API Key обязателен${NC}"
        exit 1
    fi

    echo ""
    echo "Выберите AI модель для OpenRouter (Enter = deepseek/deepseek-chat):"
    echo "  1) deepseek/deepseek-chat      [дешёво, быстро]"
    echo "  2) anthropic/claude-haiku-4-5  [качественнее]"
    echo "  3) openai/gpt-4o-mini          [альтернатива]"
    echo "  4) Ввести свою"
    read -p "Выбор [1]: " MODEL_CHOICE

    case $MODEL_CHOICE in
        2) OPENROUTER_MODEL="anthropic/claude-haiku-4-5" ;;
        3) OPENROUTER_MODEL="openai/gpt-4o-mini" ;;
        4) read -p "Введите model string: " OPENROUTER_MODEL ;;
        *) OPENROUTER_MODEL="deepseek/deepseek-chat" ;;
    esac

    echo ""
    echo -e "${YELLOW}Хотите также настроить DeepSeek нативный API?${NC}"
    echo "  (можно переключать прямо в UI, без рестарта)"
    read -p "DeepSeek API Key (Enter = пропустить): " DEEPSEEK_API_KEY
    if [ -n "$DEEPSEEK_API_KEY" ]; then
        echo -e "${GREEN}✓ DeepSeek API Key получен${NC}"
    fi

    echo ""
    read -p "Email для HH User-Agent (любой ваш email): " USER_EMAIL
    if [ -z "$USER_EMAIL" ]; then
        USER_EMAIL="user@example.com"
    fi

    echo ""
    read -p "Порт для веб-интерфейса (Enter = 8000): " PORT
    PORT=${PORT:-8000}

    echo ""
    read -p "Интервал поиска в часах (Enter = 2): " INTERVAL
    INTERVAL=${INTERVAL:-2}

    # --- Создать .env ---
    cat > .env << EOF
OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
OPENROUTER_MODEL=${OPENROUTER_MODEL}
DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-}
DEEPSEEK_MODEL=deepseek-chat
AI_PROVIDER=openrouter
HH_USER_AGENT=HHJobRanker/1.0 (${USER_EMAIL})
DB_PATH=./data/jobs.db
PORT=${PORT}
SEARCH_INTERVAL_HOURS=${INTERVAL}
MAX_PARALLEL_AI_REQUESTS=5
EOF

    echo -e "${GREEN}✓ Файл .env создан${NC}"

fi

# Считать PORT из .env (нужен для docker run и финального URL)
PORT=$(grep "^PORT=" .env | cut -d= -f2)
PORT=${PORT:-8000}

# ═══════════════════════════════════════════════════════════════
# ШАГ 3: Создать папку для данных
# ═══════════════════════════════════════════════════════════════
mkdir -p data
echo -e "${GREEN}✓ Папка data/ создана${NC}"

# ═══════════════════════════════════════════════════════════════
# ШАГ 4: Остановить старый контейнер если есть
# ═══════════════════════════════════════════════════════════════
if $DOCKER ps -a --format '{{.Names}}' | grep -q "^hh-ranker$"; then
    echo -e "${YELLOW}Останавливаю старый контейнер...${NC}"
    $DOCKER stop hh-ranker 2>/dev/null || true
    $DOCKER rm hh-ranker 2>/dev/null || true
fi

# ═══════════════════════════════════════════════════════════════
# ШАГ 5: Сборка Docker образа
# ═══════════════════════════════════════════════════════════════
echo ""
echo -e "${YELLOW}Собираю Docker образ (первый раз ~2-3 минуты)...${NC}"
$DOCKER build -t hh-ranker . --quiet
echo -e "${GREEN}✓ Образ собран${NC}"

# ═══════════════════════════════════════════════════════════════
# ШАГ 6: Запуск контейнера
# ═══════════════════════════════════════════════════════════════
echo -e "${YELLOW}Запускаю контейнер...${NC}"
$DOCKER run -d \
    --name hh-ranker \
    --restart unless-stopped \
    -p "${PORT}:8000" \
    --env-file .env \
    -v "$(pwd)/data:/app/data" \
    hh-ranker

echo -e "${GREEN}✓ Контейнер запущен${NC}"

# ═══════════════════════════════════════════════════════════════
# ШАГ 7: Проверка
# ═══════════════════════════════════════════════════════════════
echo ""
echo -e "${YELLOW}Жду запуска приложения...${NC}"
sleep 5

if $DOCKER ps --format '{{.Names}}' | grep -q "^hh-ranker$"; then
    EXTERNAL_IP=$(curl -s --max-time 5 ifconfig.me 2>/dev/null \
                  || curl -s --max-time 5 api.ipify.org 2>/dev/null \
                  || hostname -I | awk '{print $1}' \
                  || echo "VM_IP")

    echo ""
    echo -e "${GREEN}"
    echo "╔══════════════════════════════════════════════╗"
    echo "║           ✅ Всё готово!                      ║"
    echo "╠══════════════════════════════════════════════╣"
    echo "║                                              ║"
    echo -e "║  Открой в браузере:                          ║"
    printf  "║  http://%-36s║\n" "${EXTERNAL_IP}:${PORT}"
    echo "║                                              ║"
    echo "║  Агент запустится автоматически              ║"
    echo "║  и начнёт искать вакансии.                   ║"
    echo "║                                              ║"
    echo "║  Полезные команды:                           ║"
    echo "║  docker logs hh-ranker -f   # смотреть логи  ║"
    echo "║  docker stop hh-ranker      # остановить     ║"
    echo "║  docker start hh-ranker     # запустить      ║"
    echo "║  bash setup.sh              # переустановить ║"
    echo "╚══════════════════════════════════════════════╝"
    echo -e "${NC}"
else
    echo -e "${RED}Что-то пошло не так. Проверь логи:${NC}"
    echo "  $DOCKER logs hh-ranker"
    exit 1
fi
