FROM node:20-alpine AS deps
WORKDIR /app
COPY frontend/package.json ./package.json
COPY frontend/package-lock.json ./package-lock.json
RUN npm ci --no-audit --no-fund

FROM node:20-alpine AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ARG NEXT_PUBLIC_TOOL_FRONTEND_BASE_URL=http://localhost
ARG NEXT_PUBLIC_TOOL_BACKEND_BASE_URL=http://localhost
ARG NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=
ARG NEXT_DISABLE_SWC_WORKER=1
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
ENV NEXT_PUBLIC_TOOL_FRONTEND_BASE_URL=${NEXT_PUBLIC_TOOL_FRONTEND_BASE_URL}
ENV NEXT_PUBLIC_TOOL_BACKEND_BASE_URL=${NEXT_PUBLIC_TOOL_BACKEND_BASE_URL}
ENV NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=${NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY}
ENV NEXT_DISABLE_SWC_WORKER=${NEXT_DISABLE_SWC_WORKER}
ENV NEXT_TELEMETRY_DISABLED=1
ENV CI=1
COPY --from=deps /app/node_modules ./node_modules
COPY frontend .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
