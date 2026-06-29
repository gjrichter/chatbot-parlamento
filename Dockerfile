FROM python:3.12-slim

# Node 20 (LTS) per il server MCP
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Server MCP installato globalmente
RUN npm install -g @aborruso/italianparliament-mcp

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

COPY start.sh .
RUN chmod +x start.sh

CMD ["/app/start.sh"]
