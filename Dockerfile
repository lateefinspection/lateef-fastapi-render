# Use official Python image
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy files
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port
EXPOSE 10000

# Start server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
