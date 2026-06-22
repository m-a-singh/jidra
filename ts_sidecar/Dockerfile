FROM node:22-alpine AS build

WORKDIR /usr/src/app
COPY package.json ./
RUN npm install --omit=dev
COPY index.js ./

ENTRYPOINT ["node", "index.js"]
