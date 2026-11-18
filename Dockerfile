FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-interop.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Lcapy・SKiDL（DD-0028）。約 350 MB 増えるため既定では入れない。docker compose build --build-arg WITH_INTEROP=1 で入れる。
ARG WITH_INTEROP=0
RUN if [ "$WITH_INTEROP" = "1" ]; then pip install --no-cache-dir -r requirements-interop.txt; fi

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
