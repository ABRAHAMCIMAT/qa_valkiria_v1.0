// Infraestructura de Valkiria en Azure (entorno de pruebas/QA), a nivel de grupo de recursos.
//   az group create -n rg-valkiria-qa -l eastus2
//   az deployment group create -g rg-valkiria-qa -f deploy/azure/bicep/main.bicep -p deploy/azure/bicep/main.bicepparam
// Crea: Log Analytics, Azure Container Registry, AKS (Workload Identity, Key Vault CSI, app routing),
// identidad administrada para la API, Key Vault y PostgreSQL Flexible Server con la base sintética y la de flujos.
// El LLM (Llama 3.2 Instruct) corre con Ollama dentro de AKS (deploy/azure/aks).

targetScope = 'resourceGroup'

@description('Prefijo corto para nombrar recursos (minúsculas y números).')
@minLength(3)
@maxLength(12)
param prefix string = 'valkiria'

@description('Región de Azure.')
param location string = resourceGroup().location

@description('Tamaño de VM de los nodos de AKS. Llama 3.2 3B en CPU requiere al menos 8 GiB de RAM por nodo.')
param aksNodeVmSize string = 'Standard_D4s_v5'

@description('Número de nodos de AKS.')
@minValue(1)
@maxValue(5)
param aksNodeCount int = 2

@description('Usuario administrador de PostgreSQL.')
param postgresAdminLogin string = 'valkiriaadmin'

@description('Contraseña del administrador de PostgreSQL (solo base sintética).')
@secure()
param postgresAdminPassword string

@description('Token con que el pipeline de Azure DevOps envía resultados a Valkiria (HU-010). Vacío: se genera uno nuevo en cada despliegue.')
@secure()
param pipelineCallbackToken string = ''

@description('Valor generado cuando no se indica pipelineCallbackToken (no se pasa a mano).')
@secure()
param generatedPipelineToken string = newGuid()

@description('Namespace y ServiceAccount de Kubernetes que usarán la identidad administrada.')
param kubernetesNamespace string = 'valkiria'
param kubernetesServiceAccount string = 'valkiria-api'

var suffix = uniqueString(resourceGroup().id)
var acrName = take('${prefix}acr${suffix}', 50)
var keyVaultName = take('${prefix}-kv-${suffix}', 24)
var postgresName = '${prefix}-pg-${suffix}'
var databaseName = 'nissan_synthetic'
var workflowDatabaseName = 'valkiria_workflows'
var tags = {
  application: 'valkiria'
  environment: 'qa'
}

// Roles integrados de Azure.
var acrPullRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var keyVaultSecretsUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: {
    adminUserEnabled: false
  }
}

resource aks 'Microsoft.ContainerService/managedClusters@2024-02-01' = {
  name: '${prefix}-aks-${suffix}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    dnsPrefix: '${prefix}-${suffix}'
    agentPoolProfiles: [
      {
        name: 'system'
        mode: 'System'
        count: aksNodeCount
        vmSize: aksNodeVmSize
        osType: 'Linux'
        osSKU: 'AzureLinux'
        type: 'VirtualMachineScaleSets'
      }
    ]
    networkProfile: {
      networkPlugin: 'azure'
      networkPluginMode: 'overlay'
      networkPolicy: 'azure'
    }
    oidcIssuerProfile: { enabled: true }
    securityProfile: {
      workloadIdentity: { enabled: true }
    }
    ingressProfile: {
      webAppRouting: { enabled: true }
    }
    addonProfiles: {
      azureKeyvaultSecretsProvider: {
        enabled: true
        config: { enableSecretRotation: 'true' }
      }
      omsagent: {
        enabled: true
        config: { logAnalyticsWorkspaceResourceID: logs.id }
      }
    }
  }
}

// Los nodos de AKS descargan la imagen desde ACR.
resource aksAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, aks.id, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: aks.properties.identityProfile.kubeletidentity.objectId
    principalType: 'ServicePrincipal'
  }
}

// Identidad de la API (Workload Identity): lee secretos de Key Vault sin credenciales en el clúster.
resource apiIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-api-id-${suffix}'
  location: location
  tags: tags
}

resource apiFederation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: apiIdentity
  name: 'aks-${kubernetesNamespace}-${kubernetesServiceAccount}'
  properties: {
    issuer: aks.properties.oidcIssuerProfile.issuerURL
    subject: 'system:serviceaccount:${kubernetesNamespace}:${kubernetesServiceAccount}'
    audiences: [ 'api://AzureADTokenExchange' ]
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 30
    enablePurgeProtection: true
  }
}

resource apiKeyVaultSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, apiIdentity.id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: keyVaultSecretsUserRoleId
    principalId: apiIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: postgresName
  location: location
  tags: tags
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: postgresAdminLogin
    administratorLoginPassword: postgresAdminPassword
    storage: { storageSizeGB: 32 }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: { mode: 'Disabled' }
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// Estado persistente de los flujos por historia (dependencias, versiones y aprobaciones).
resource workflowDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: workflowDatabaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// Acceso desde servicios de Azure (incluido AKS). Pendiente de producción: red privada (VNet integration).
resource postgresAllowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// Secreto que la API recibe como VALKIRIA_SYNTHETIC_DATABASE_URL (vía Key Vault CSI).
resource databaseUrlSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'synthetic-database-url'
  properties: {
    value: 'postgresql+psycopg://${postgresAdminLogin}:${uriComponent(postgresAdminPassword)}@${postgres.properties.fullyQualifiedDomainName}:5432/${databaseName}?sslmode=require'
  }
}

resource workflowDatabaseUrlSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'workflow-database-url'
  properties: {
    value: 'postgresql+psycopg://${postgresAdminLogin}:${uriComponent(postgresAdminPassword)}@${postgres.properties.fullyQualifiedDomainName}:5432/${workflowDatabaseName}?sslmode=require'
  }
}

// La API lo recibe como VALKIRIA_PIPELINE_CALLBACK_TOKEN; el pipeline lo lee con el mismo nombre desde un grupo de variables ligado a este Key Vault.
resource pipelineTokenSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'valkiria-pipeline-token'
  properties: {
    value: empty(pipelineCallbackToken) ? generatedPipelineToken : pipelineCallbackToken
  }
}

output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output aksName string = aks.name
output keyVaultName string = keyVault.name
output tenantId string = subscription().tenantId
output workloadIdentityClientId string = apiIdentity.properties.clientId
output postgresFqdn string = postgres.properties.fullyQualifiedDomainName
