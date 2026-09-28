#!/usr/bin/env bash
# Despliega Valkiria en un clúster kind local para pruebas.
# Requisitos: Docker, kind y kubectl. Resultado: API y frontend en http://localhost:8080
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CLUSTER=valkiria
NAMESPACE=valkiria-local
IMAGE=ghcr.io/abrahamcimat/qa_valkiria_v1.0:0.5.0

if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --config "$ROOT/deploy/kind/kind-config.yaml" --wait 120s
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

echo "==> Construyendo y cargando la imagen $IMAGE"
docker build -q -f "$ROOT/deploy/docker/Dockerfile" -t "$IMAGE" "$ROOT"
kind load docker-image "$IMAGE" --name "$CLUSTER"

echo "==> Aplicando manifiestos"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
# Migración y datos sintéticos que PostgreSQL aplica al iniciar.
kubectl -n "$NAMESPACE" create configmap synthetic-db-init \
  --from-file=V1__create_schema.sql="$ROOT/synthetic_db/migrations/V1__create_schema.sql" \
  --from-file=V2__seed_nissan_data.sql="$ROOT/synthetic_db/fixtures/V2__seed_nissan_data.sql" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -k "$ROOT/deploy/kind"
# Reinicia para tomar la imagen recién cargada (la etiqueta no cambia entre builds).
kubectl -n "$NAMESPACE" rollout restart deployment/valkiria-api deployment/synthetic-app

echo "==> Esperando a que los pods estén listos"
for deployment in postgres synthetic-app valkiria-api; do
  kubectl -n "$NAMESPACE" rollout status "deployment/$deployment" --timeout=180s
done

echo "==> Listo: http://localhost:8080  (salud: http://localhost:8080/health)"
