# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.11
ARG DEBIAN_RELEASE=trixie

# build the wheels (mysqlclient needs a compiler and the client headers)
FROM python:${PYTHON_VERSION}-slim-${DEBIAN_RELEASE} AS build

RUN apt-get update \
    && apt-get install -y --no-install-suggests --no-install-recommends \
        gcc pkg-config libmariadb-dev \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=bind,source=requirements.txt,target=requirements.txt \
    pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


FROM python:${PYTHON_VERSION}-slim-${DEBIAN_RELEASE}

# runtime library for mysqlclient; dim talks to MariaDB
RUN apt-get update \
    && apt-get install -y --no-install-suggests --no-install-recommends libmariadb3 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN --mount=type=bind,from=build,source=/wheels,target=/wheels \
    pip install --no-cache-dir --no-index /wheels/*.whl

ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/nonexistent" \
    --shell "/sbin/nologin" \
    --no-create-home \
    --uid "${UID}" \
    dimuser

WORKDIR /dim

RUN mkdir /etc/dim && chown dimuser:dimuser /etc/dim

COPY --chown=dimuser:dimuser . .

USER dimuser

CMD ["gunicorn", "app:application", "-w", "4", "-b", ":8000"]
