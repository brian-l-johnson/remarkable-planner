FROM node:22-alpine

WORKDIR /app

COPY package.json .
RUN npm install --omit=dev

COPY server.mjs .

# Token will be mounted via k8s secret at /secret/rmapi-token
RUN mkdir -p /secret

EXPOSE 8080
CMD ["node", "server.mjs"]
