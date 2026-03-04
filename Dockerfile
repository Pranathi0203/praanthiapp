FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better caching)
COPY src/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy app
COPY src /app

# App Service expects container to listen on PORT
ENV PORT=8000
EXPOSE 8000

# Change if your entry file is different
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]