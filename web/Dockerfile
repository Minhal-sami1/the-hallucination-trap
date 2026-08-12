FROM node:22-alpine

WORKDIR /workspace/web

COPY web/package*.json ./
RUN npm ci

COPY web/ ./
COPY tokens.css /workspace/tokens.css

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
