#!/usr/bin/env bash
# Elimina el clúster kind de pruebas y todo lo desplegado en él.
set -euo pipefail
kind delete cluster --name valkiria
