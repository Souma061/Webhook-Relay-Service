FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
"""
Dockerfile — Container image definition.

Uses Python 3.12 slim image, installs dependencies from pyproject.toml,
and runs the FastAPI app via uvicorn.
"""
