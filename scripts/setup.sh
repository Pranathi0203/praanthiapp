export PREFIX="pranathiapp"
export LOCATION="westus3"

export SUBSCRIPTION_ID=$(az account show --query id -o tsv)
export SUBSCRIPTION_NAME=$(az account show --query name -o tsv)
export TENANT_ID=$(az account show --query tenantId -o tsv)

export RG_SHARED="rg-${PREFIX}-shared"
export RG_DEV="rg-${PREFIX}-dev"
export RG_QA="rg-${PREFIX}-qa"
export CLIENT_ID="${AZURE_CLIENT_ID:-}"
export ACR_NAME="${ACR_NAME:-}"

echo "Loaded: LOCATION=$LOCATION PREFIX=$PREFIX SUBSCRIPTION_NAME=$SUBSCRIPTION_NAME"
if [ -z "$CLIENT_ID" ]; then
  echo "AZURE_CLIENT_ID is not set. Set it after you create the GitHub OIDC app registration/service principal."
fi
if [ -z "$ACR_NAME" ]; then
  echo "ACR_NAME is not set. Set it to the Azure Container Registry name for this subscription."
fi
