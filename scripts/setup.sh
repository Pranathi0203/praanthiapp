export PREFIX="pranathiapp"
export LOCATION="westus3"

export SUBSCRIPTION_ID=$(az account show --query id -o tsv)
export TENANT_ID=$(az account show --query tenantId -o tsv)

export RG_SHARED="rg-${PREFIX}-shared"
export RG_DEV="rg-${PREFIX}-dev"
export RG_QA="rg-${PREFIX}-qa"
export CLIENT_ID="1e039560-9dac-4437-8e2c-7e5bc049a221"
export ACR_NAME="pranathiappacr7182"
echo "Loaded: LOCATION=$LOCATION PREFIX=$PREFIX"