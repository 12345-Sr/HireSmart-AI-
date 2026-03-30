import msal
import os
import time
import streamlit as st
from dotenv import load_dotenv

# Load local .env file (safely ignored in Streamlit Cloud)
load_dotenv()

def get_secret(secret_name):
    """Fetches a secret from Streamlit Cloud or local environment variables."""
    try:
        if secret_name in st.secrets:
            return st.secrets[secret_name]
    except FileNotFoundError:
        pass
    return os.getenv(secret_name)

# Using st.cache_resource ensures we only initialize MSAL once per server spin-up
@st.cache_resource
def get_msal_app():
    client_id = get_secret("O365_CLIENT_ID")
    tenant_id = get_secret("O365_TENANT_ID")
    
    if not client_id or not tenant_id:
        st.error("❌ Missing O365_CLIENT_ID or O365_TENANT_ID in secrets!")
        st.stop()
        
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    return msal.PublicClientApplication(client_id, authority=authority)

def require_login():
    """
    Checks if the user is logged in. 
    If not, displays the login UI and stops the app. 
    Returns True if authenticated.
    """
    # 1. If token is already in memory, they are logged in.
    if "access_token" in st.session_state:
        return True

    # 2. If not logged in, show the login UI
    st.warning("🔒 You must log in to access your data.")
    msal_app = get_msal_app()

    if st.button("Log in with Microsoft"):
        flow = msal_app.initiate_device_flow(scopes=["Files.Read.All"])
        
        if "user_code" in flow:
            st.info(f"**Step 1:** Click this link to open Microsoft Login: [{flow['verification_uri']}]({flow['verification_uri']})")
            st.error(f"**Step 2:** Enter this code: **{flow['user_code']}**")
            
            with st.spinner("Waiting for you to complete the login on Microsoft's website..."):
                result = msal_app.acquire_token_by_device_flow(flow)
                
                if "access_token" in result:
                    # Save the token to Streamlit's session state
                    st.session_state["access_token"] = result["access_token"]
                    st.success("✅ Login successful!")
                    time.sleep(1)
                    st.rerun() # Refresh the page to clear the login UI
                else:
                    st.error(f"Login failed: {result.get('error_description')}")
        else:
            st.error("Failed to initiate login flow.")
            
    # We return False if they haven't successfully logged in yet
    return False

def logout():
    """Clears the session state to log the user out."""
    if "access_token" in st.session_state:
        del st.session_state["access_token"]
        st.rerun()