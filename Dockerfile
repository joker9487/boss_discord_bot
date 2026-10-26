FROM python:3.13-slim

ENV TZ=Asia/Taipei \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先裝套件：requirements.txt 沒變就沿用快取，只改程式不用重裝
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
