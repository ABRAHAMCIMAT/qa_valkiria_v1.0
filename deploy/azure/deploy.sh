#!/usr/bin/env bash
# Despliega Valkiria en AKS a partir de las salidas de deploy/azure/bicep/main.bicep.
# Requisitos: az (con sesión iniciada), kubectl. Uso:
#   RESOURCE_GROUP=rg-valkiria-qa IMAGE_TAG=0.5.0 deploy/azure/deploy.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
: "${RESOURCE_GROUP:?Define RESOURCE_GROUP}"
: "${IMAGE_TAG:?Define IMAGE_TAG}"
DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-main}"
NAMESPACE=valkiria

output() {
  az deployment group show -g "$RESOURCE_GROUP" -n "$DEPLOYMENT_NAME" --query "properties.outputs.$1.value" -o tsv
}

ACR_LOGIN_SERVER="$(output acrLoginServer)"
AKS_NAME="$(output aksName)"
KEYVAULT_NAME="$(output keyVaultName)"
TENANT_ID="$(output tenantId)"
WORKLOAD_CLIENT_ID="$(output workloadIdentityClientId)"

echo "==> Credenciales de $AKS_NAME"
az aks get-credentials -g "$RESOURCE_GROUP" -n "$AKS_NAME" --overwrite-existing >/dev/null

echo "==> Renderizando manifiestos"
RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT
kubectl kustomize "$ROOT/deploy/azure/aks" \
  | sed -e "s#__ACR_LOGIN_SERVER__#$ACR_LOGIN_SERVER#g" \
        -e "s#__IMAGE_TAG__#$IMAGE_TAG#g" \
        -e "s#__KEYVAULT_NAME__#$KEYVAULT_NAME#g" \
        -e "s#__TENANT_ID__#$TENANT_ID#g" \
        -e "s#__WORKLOAD_CLIENT_ID__#$WORKLOAD_CLIENT_ID#g" > "$RENDERED"
if grep -q "__[A-Z_]*__" "$RENDERED"; then
  echo "Quedaron valores sin reemplazar:" >&2
  grep -o "__[A-Z_]*__" "$RENDERED" | sort -u >&2
  exit 1
fi

echo "==> Aplicando en el namespace $NAMESPACE"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NAMESPACE" create configmap synthetic-db-init \
  --from-file=V1__create_schema.sql="$ROOT/synthetic_db/migrations/V1__create_schema.sql" \
  --from-file=V2__seed_nissan_data.sql="$ROOT/synthetic_db/fixtures/V2__seed_nissan_data.sql" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "$RENDERED"

echo "==> Esperando a que los pods estén listos (la primera vez Ollama descarga el modelo)"
kubectl -n "$NAMESPACE" rollout status deployment/ollama --timeout=900s
kubectl -n "$NAMESPACE" rollout status deployment/synthetic-app --timeout=300s
kubectl -n "$NAMESPACE" rollout status deployment/valkiria-api --timeout=300s

INGRESS_IP="$(kubectl -n "$NAMESPACE" get ingress valkiria-api -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"
echo "==> Listo: http://${INGRESS_IP:-<pendiente>}/health"
