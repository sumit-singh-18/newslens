FROM python:3.11-slim

WORKDIR /app

ENV PORT=7860

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all backend files
COPY . .

# HuggingFace Spaces uses port 7860
EXPOSE 7860

# Start command
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "7860"]
