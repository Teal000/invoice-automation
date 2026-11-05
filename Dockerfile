# Image for the API service.
#
# NOT YET BUILT. Docker is not installed on the development machine, so this
# file has never been built or run. It is written so that it is ready the
# moment Docker exists; until then, treat it as a proposal and expect the
# first build to need corrections. docs/setup.md says the same.
#
# Python 3.12 to match the development environment (3.12.13) and the
# requires-python floor in pyproject.toml.

FROM python:3.12-slim

# Fail fast and log straight through, which matters when the only view of the
# process is `docker logs`.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a source change does not reinstall them. psycopg is
# installed from the binary wheel, which bundles libpq, so no system libpq or
# compiler is needed here.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Then the package itself.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps -e .

# The migrations and the synthetic fixtures. The migrations are needed to
# bring a fresh database up; the fixtures make the container useful for a
# demonstration without mounting anything.
COPY db ./db
COPY data/fixtures ./data/fixtures
COPY data/ground_truth ./data/ground_truth

# Documents are written here. Declared as a volume in compose so uploads
# survive a container being replaced.
RUN mkdir -p /app/data/storage

# Run as a non-root user. The service needs no privileges, and a container
# that cannot write outside its own directories is one less thing to reason
# about.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app/data
USER appuser

EXPOSE 8000

# No --reload: this is not a development server. One worker, because the
# connection-per-request model and a local demonstration do not need more,
# and more workers would multiply connections without being asked.
CMD ["python", "-m", "uvicorn", "invoice_automation.api.main:app", \
     "--host", "0.0.0.0", "--port", "8000"]
