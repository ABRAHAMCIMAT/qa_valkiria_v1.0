using './main.bicep'

param prefix = 'valkiria'
param aksNodeVmSize = 'Standard_D4s_v5'
param aksNodeCount = 2
// La contraseña se toma de una variable de entorno; nunca se escribe en el repositorio.
param postgresAdminPassword = readEnvironmentVariable('VALKIRIA_PG_ADMIN_PASSWORD')
// Opcional: fija el token del pipeline (HU-010) para que no rote en cada despliegue.
param pipelineCallbackToken = readEnvironmentVariable('VALKIRIA_PIPELINE_CALLBACK_TOKEN', '')
