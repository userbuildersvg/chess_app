# Chess AI Platform - Multi-service Docker Image
FROM python:3.9

# Install Node.js for frontend build
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs nginx curl

# Create user for security
RUN useradd -m -u 1000 user

WORKDIR /app

# Install Python dependencies
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Install Langflow for AI integration
RUN pip install --no-cache-dir langflow

# Copy backend code
COPY *.py ./
COPY config.py ./
COPY utils.py ./
COPY game_logic.py ./
COPY langflow_config.py ./
COPY langflow_service.py ./

# Copy flows directory
COPY flows/ ./flows/

# Copy frontend code and build
COPY chess-frontend/ ./frontend/
WORKDIR /app/frontend
RUN npm ci && npm run build

# Setup nginx configuration
WORKDIR /app
RUN rm /etc/nginx/sites-enabled/default
COPY chess-frontend/nginx.conf /etc/nginx/sites-enabled/default

# Copy built frontend to nginx
RUN cp -r /app/frontend/dist/* /var/www/html/

# Expose ports
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/api/status || exit 1

# Start both nginx and FastAPI
CMD ["sh", "-c", "nginx && uvicorn app:app --host 0.0.0.0 --port 8080"]