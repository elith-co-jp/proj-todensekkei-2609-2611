FROM python:3.13-slim

# TZ: ZIP 内のファイル日時やログなど、OS の時計を使う所も日本時間にする（DD-0036）。
# JST-9 は POSIX 形式なので tzdata が入っていない slim イメージでも効く。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=JST-9 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-interop.txt constraints.txt ./
# 版は constraints.txt で固定する（CI と同じ組み合わせ）
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt
# Lcapy・SKiDL（DD-0028）。約 350 MB 増えるため既定では入れない。docker compose build --build-arg WITH_INTEROP=1 で入れる。
ARG WITH_INTEROP=0
RUN if [ "$WITH_INTEROP" = "1" ]; then pip install --no-cache-dir -r requirements-interop.txt; fi

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
