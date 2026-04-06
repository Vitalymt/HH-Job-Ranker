#!/bin/bash

# =============================================================
# HH Job Ranker — Setup Script
# Запуск: bash setup.sh
# =============================================================

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}"
echo "╔══════════════════════════════════════╗"
echo "║       HH Job Ranker — Setup          ║"
echo "╚══════════════════════════════════════╝"
echo -e "${NC}"

# --- 1. Проверка и установка Docker ---
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}Docker не найден. Устанавливаю...${NC}"
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    echo -e "${GREEN}Docker установлен.${NC}"
else
    echo -e "${GREEN}✓ Docker найден: $(docker --version)${NC}"
fi

# --- 2. Ввод API ключей ---
echo ""
echo -e "${YELLOW}=== Настройка API ключей ===${NC}"
echo ""

read -p "OpenRouter API Key (sk-or-...): " OPENROUTER_API_KEY
if [ -z "$OPENROUTER_API_KEY" ]; then
    echo -e "${RED}Ошибка: OpenRouter API Key обязателен${NC}"
    exit 1
fi

echo ""
echo "Выберите AI модель (Enter = deepseek/deepseek-chat):"
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
echo "  (для переключения между провайдерами прямо в UI)"
read -p "DeepSeek API Key (Enter = пропустить): " DEEPSEEK_API_KEY
if [ -n "$DEEPSEEK_API_KEY" ]; then
    echo -e "${GREEN}✓ DeepSeek API Key получен${NC}"
fi

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

# --- 3. Создать .env ---
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

# --- 4. Создать папку для данных ---
mkdir -p data
echo -e "${GREEN}✓ Папка data/ создана${NC}"

# --- 5. Остановить старый контейнер если есть ---
if docker ps -a --format '{{.Names}}' | grep -q "^hh-ranker$"; then
    echo -e "${YELLOW}Останавливаю старый контейнер...${NC}"
    docker stop hh-ranker 2>/dev/null || true
    docker rm hh-ranker 2>/dev/null || true
fi

# --- 6. Сборка Docker образа ---
echo ""
echo -e "${YELLOW}Собираю Docker образ...${NC}"
docker build -t hh-ranker . --quiet
echo -e "${GREEN}✓ Образ собран${NC}"

# --- 7. Запуск контейнера ---
echo -e "${YELLOW}Запускаю контейнер...${NC}"
docker run -d \
    --name hh-ranker \
    --restart unless-stopped \
    -p ${PORT}:8000 \
    --env-file .env \
    -v "$(pwd)/data:/app/data" \
    hh-ranker

echo -e "${GREEN}✓ Контейнер запущен${NC}"

# --- 8. Проверка ---
echo ""
echo -e "${YELLOW}Жду запуска приложения...${NC}"
sleep 5

if docker ps --format '{{.Names}}' | grep -q "^hh-ranker$"; then
    # Получить внешний IP
    EXTERNAL_IP=$(curl -s ifconfig.me 2>/dev/null || echo "VM_IP")

    echo ""
    echo -e "${GREEN}"
    echo "╔══════════════════════════════════════════════╗"
    echo "║           ✅ Всё готово!                      ║"
    echo "╠══════════════════════════════════════════════╣"
    echo "║                                              ║"
    echo -e "║  Открой в браузере:                          ║"
    echo -e "║  http://${EXTERNAL_IP}:${PORT}                   ║"
    echo "║                                              ║"
    echo "║  Агент запустится автоматически              ║"
    echo "║  и начнёт искать вакансии.                   ║"
    echo "║                                              ║"
    echo "║  Полезные команды:                           ║"
    echo "║  docker logs hh-ranker -f    # смотреть логи ║"
    echo "║  docker stop hh-ranker       # остановить    ║"
    echo "║  docker start hh-ranker      # запустить     ║"
    echo "╚══════════════════════════════════════════════╝"
    echo -e "${NC}"
else
    echo -e "${RED}Что-то пошло не так. Проверь логи: docker logs hh-ranker${NC}"
    exit 1
fi
