import msal
from config.credentials import MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID

def get_access_token():
    app = msal.ConfidentialClientApplication(
        client_id=MS_CLIENT_ID,
        client_credential=MS_CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{MS_TENANT_ID}"
    )
    
    # Acquire token for application (client credentials flow)
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    
    if "access_token" in result:
        return result["access_token"]
    else:
        error = result.get("error", "Unknown error")
        description = result.get("error_description", "No description")
        raise Exception(f"Failed to acquire token: {error} - {description}")