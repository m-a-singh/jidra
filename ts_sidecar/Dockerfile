FROM node:20-slim AS build

WORKDIR /usr/src/app
COPY package.json ./
RUN npm install --omit=dev
COPY index.js ./

ENTRYPOINT ["node", "index.js"]
