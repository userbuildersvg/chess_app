# Chess AI Platform - Multi-service Docker Image
FROM python:3.9

# Install Node.js for frontend build
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs nginx curl stockfish

# Create user for security
RUN useradd -m -u 1000 user

WORKDIR /app

# Install Python dependencies
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Copy backend code
COPY *.py ./
COPY config.py ./
COPY utils.py ./
COPY game_logic.py ./
COPY langflow_config.py ./
COPY langflow_service.py ./

# Copy flows directory
COPY flows/ ./flows/

# The schema, for the same reason Dockerfile.backend copies it: `COPY *.py`
# does not include it, and without it the app now refuses to boot rather than
# serving an empty database in silence.
COPY migrations/ ./migrations/

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

# V4.5 selects moves through gemini_move_service (direct REST), with Langflow
# only as a legacy fallback. Default it off so a container is correct out of
# the box; still overridable with `-e DISABLE_LANGFLOW=false`.
ENV DISABLE_LANGFLOW=true

# Sized for a small container, matching the runner script used in development.
ENV STOCKFISH_DEPTH=15
ENV STOCKFISH_RANK_DEPTH=10

# The learning DB lives here. learning_service creates it on first use, but
# the directory is made now so it can be mounted as a volume without docker
# inventing it as root-owned.
RUN mkdir -p /app/data

# Expose ports
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

# Start both nginx and FastAPI
# --no-proxy-headers is NOT optional, and it is not a performance tweak.
#
# uvicorn ships ProxyHeadersMiddleware ENABLED BY DEFAULT, with
# --forwarded-allow-ips defaulting to the immediate peer. When it is on,
# uvicorn REWRITES scope["client"] from the X-Forwarded-For header - so
# `request.client.host`, which every "unforgeable socket peer" fallback in
# this application relies on, becomes a value the caller wrote.
#
# That defeated the rate limiter twice over. `rate_limit.client_ip()` reads
# X-Forwarded-For deliberately and carefully (TRUSTED_PROXY_HOPS), but its
# safe fallback was uvicorn's already-poisoned client address, so rotating one
# header per request produced a fresh bucket every time: unlimited password
# guessing, unlimited beta-code attempts, unlimited Gemini spend. Reproduced
# end to end, and closed by this flag - twelve failed logins went from
# 401 x12 to 401 x10 + 429 x2 with nothing else changed.
#
# With it off, uvicorn leaves scope["client"] as the real TCP peer and this
# application has exactly ONE place that decides what the caller's address is:
# rate_limit.client_ip(), governed by TRUSTED_PROXY_HOPS. One decision, in one
# file, that a person can read. Do not remove this flag to "fix" a wrong
# client IP - set TRUSTED_PROXY_HOPS instead.
CMD ["sh", "-c", "nginx && uvicorn app:app --host 0.0.0.0 --port 8080 --no-proxy-headers"]
