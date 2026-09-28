# Cloud deployment blueprint

Use a cloud-specific module per provider and keep state remote/encrypted. Recommended production bindings: AWS ECS/EKS + KMS/Secrets Manager, Azure Container Apps/AKS + Key Vault, GCP Cloud Run/GKE + Secret Manager, IBM Code Engine/OpenShift + Secrets Manager. The initial repository intentionally contains no provider credentials or irreversible infrastructure resources.
